"""Event handler for clients of the server."""

import argparse
import asyncio
import datetime
import json
import logging
import random
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import regex as re
from piper import PiperVoice, SynthesisConfig
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
)

from .download import VoiceNotFoundError, ensure_voice_exists, find_voice
from .homographs import CORRECTION_WORDS
from .sentence_boundary import SentenceBoundaryDetector
from .espeak_fixes import remove_stress_for_espeak

_LOGGER = logging.getLogger(__name__)

try:
    from .english_normalizer import EnglishNormalizer
    ENG_AVAILABLE = True
except ImportError:
    EnglishNormalizer = None
    ENG_AVAILABLE = False

try:
    from .russian_normalizer import RussianNormalizer
    RUS_AVAILABLE = True
except ImportError:
    RussianNormalizer = None
    RUS_AVAILABLE = False


def _ts() -> str:
    """Возвращает абсолютный таймстемп с миллисекундами."""
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


# --- ЛОГИКА УДАРЕНИЙ ---

_STRESS_MARK = "\u0301"
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_RUSSIAN_VOWELS_SET = set(_RUSSIAN_VOWELS)
_PUNCTUATION = ".,!?:[]{}<>'—–-/"


def _count_vowels(word: str) -> int:
    return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)


def preprocess_text_for_stress(
    text: str, accentor: Optional[Any], user_marker: str = "+"
) -> str:
    current_text = text

    if accentor and CORRECTION_WORDS:
        # Быстрый поиск всех слов в предложении (используем регулярку \p{L} для букв)
        words = set(re.findall(r'\p{L}+', text.lower()))
        
        # Если в предложении есть хотя бы один омограф из словаря
        if words & CORRECTION_WORDS:
            try:
                text_with_silero_stress = accentor(text)
                split_pattern = f"([\\s{re.escape(_PUNCTUATION)}]+)"

                original_parts = re.split(split_pattern, text)
                stressed_parts = re.split(split_pattern, text_with_silero_stress)

                if len(original_parts) == len(stressed_parts):
                    final_parts = []
                    for orig_part, stressed_part in zip(original_parts, stressed_parts):
                        clean_word = orig_part.lower().strip(_PUNCTUATION)
                        if clean_word in CORRECTION_WORDS:
                            final_parts.append(stressed_part)
                        else:
                            final_parts.append(orig_part)
                    current_text = "".join(final_parts)
                else:
                    _LOGGER.debug(
                        f"[{_ts()}] [STRESS SKIP] Parts mismatch: "
                        f"{len(original_parts)} vs {len(stressed_parts)}."
                    )
            except Exception as e:
                _LOGGER.debug(f"[{_ts()}] Silero error: {e}")
        else:
            # Если омографов нет, мы просто пропускаем тяжелый вызов accentor(text)
            _LOGGER.debug(f"[{_ts()}] [STR] Bypass")

    # Обработка ручного маркера '+' (продолжает работать мгновенно, даже если Silero пропущен)
    if user_marker in current_text:
        stress_pattern = re.compile(re.escape(user_marker) + f"([{_RUSSIAN_VOWELS}])")
        split_pattern = f"([\\s{re.escape(_PUNCTUATION)}]+)"
        parts = re.split(split_pattern, current_text)
        final_unicode_parts = []

        for part in parts:
            if (
                not part
                or part.isspace()
                or any(c in _PUNCTUATION for c in part if not c.isalnum())
            ):
                final_unicode_parts.append(part)
                continue

            if user_marker in part:
                clean_word = part.replace(user_marker, "")
                if _count_vowels(clean_word) <= 1:
                    final_unicode_parts.append(clean_word)
                else:
                    processed_word = stress_pattern.sub(
                        lambda m: m.group(1) if m.group(1) in "ёЁ" else m.group(1) + _STRESS_MARK, part
                    )
                    final_unicode_parts.append(processed_word)
            else:
                final_unicode_parts.append(part)
        return "".join(final_unicode_parts)

    return current_text.replace(user_marker, "")


_VOICES_CACHE: OrderedDict[str, PiperVoice] = OrderedDict()
_VOICE_LOCK = asyncio.Lock()


class PiperEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        voices_info: Dict[str, Any],
        accentor: Optional[Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.voices_info = voices_info
        self.accentor = accentor

        self.is_streaming: bool = False
        self.audio_started: bool = False
        self._is_first_sentence_in_event: bool = True

        self.sbd = SentenceBoundaryDetector(emit_break_markers=True)
        self._synthesize: Optional[Synthesize] = None
        self._custom_configs_cache: Dict[str, dict] = {}

        self.eng_normalizer = None
        self.rus_normalizer = None

        if not cli_args.no_normalization:
            if ENG_AVAILABLE:
                self.eng_normalizer = EnglishNormalizer()
            if RUS_AVAILABLE:
                self.rus_normalizer = RussianNormalizer(use_yo=cli_args.yo)

        self._sentence_buffer: List[str] = []
        self._buffer_char_limit = 110

    async def _flush_buffer(self, synthesize_obj: Synthesize):
        if not self._sentence_buffer:
            return

        combined_text = " ".join(self._sentence_buffer)
        if len(self._sentence_buffer) > 1:
            _LOGGER.debug(
                f"[{_ts()}] [BUFFER] Merged {len(self._sentence_buffer)} items, "
                f"total {len(combined_text)} chars"
            )

        self._sentence_buffer = []
        syn_copy = Synthesize(text=combined_text, voice=synthesize_obj.voice)
        await self._handle_synthesize(syn_copy, send_stop=False, break_type=None)

    async def _process_and_synthesize(self, sentence: str, synthesize_obj: Synthesize):
        # 1. Обработка маркеров структуры текста
        if sentence in ("<PARAGRAPH_BREAK>", "<DIALOGUE_BREAK>"):
            await self._flush_buffer(synthesize_obj)
            b_type = "paragraph" if sentence == "<PARAGRAPH_BREAK>" else "dialogue"
            await self._handle_synthesize(
                synthesize_obj, send_stop=False, break_type=b_type
            )
            return

        if not sentence.strip():
            return

        # 2. Фикс для Home Assistant
        stripped_sentence = sentence.lstrip()
        if self._is_first_sentence_in_event and stripped_sentence.startswith(("—", "–")):
            await self._handle_synthesize(
                synthesize_obj, send_stop=False, break_type="dialogue"
            )

        self._is_first_sentence_in_event = False

        # 3. Выбор голоса и определение языка
        voice_name = (
            synthesize_obj.voice.name if synthesize_obj.voice else None
        ) or self.cli_args.voice
        voice_info = self.voices_info.get(voice_name)

        if not voice_info:
            if voice_name not in self._custom_configs_cache:
                try:
                    _, config_path = find_voice(voice_name, self.cli_args.data_dir)
                    with open(config_path, "r", encoding="utf-8") as f:
                        self._custom_configs_cache[voice_name] = json.load(f)
                except Exception:
                    self._custom_configs_cache[voice_name] = {}
            voice_info = self._custom_configs_cache[voice_name]

        lang_id = (
            voice_info.get("espeak", {}).get("voice")
            or voice_info.get("language", {}).get("code")
            or voice_name
        )
        is_russian = str(lang_id).lower().startswith("ru")

        # 4. Нормализация текста
        temp_text = sentence
        log_steps = []

        if is_russian:
            if self.eng_normalizer:
                transformed = self.eng_normalizer.normalize(temp_text)
                if transformed != temp_text:
                    temp_text = transformed
                    log_steps.append(f"[{_ts()}] [ENG] {temp_text}")

            if self.rus_normalizer:
                transformed = self.rus_normalizer.normalize(temp_text)
                if transformed != temp_text:
                    temp_text = transformed
                    log_steps.append(f"[{_ts()}][RUS] {temp_text}")

            transformed = preprocess_text_for_stress(temp_text, self.accentor)
            if transformed != temp_text:
                temp_text = transformed
                log_steps.append(f"[{_ts()}] [STR] {temp_text}")

            # --- espeak_fixes ---
            transformed = remove_stress_for_espeak(temp_text)
            if transformed != temp_text:
                temp_text = transformed
                log_steps.append(f"[{_ts()}] [ESPEAK_FIX] {temp_text}")

        _LOGGER.debug(f"[{_ts()}] [RAW] {sentence}")
        for log_entry in log_steps:
            _LOGGER.debug(log_entry)

        if is_russian:
            _LOGGER.debug(f"[{_ts()}][PREP DONE]")
        else:
            _LOGGER.debug(f"[{_ts()}] [PREP SKIP] Non-Russian voice ({lang_id})")

        # 5. Буферизация предложений
        current_buf_len = sum(len(s) for s in self._sentence_buffer)
        new_len = len(temp_text)

        if (current_buf_len + new_len) > self._buffer_char_limit:
            await self._flush_buffer(synthesize_obj)

        self._sentence_buffer.append(temp_text)

        if len(temp_text) >= self._buffer_char_limit:
            await self._flush_buffer(synthesize_obj)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            return True

        try:
            if Synthesize.is_type(event.type):
                if self.is_streaming:
                    return True

                synthesize = Synthesize.from_event(event)
                self.sbd = SentenceBoundaryDetector(emit_break_markers=True)
                self.audio_started = False
                self._sentence_buffer = []
                self._is_first_sentence_in_event = True

                for sentence in self.sbd.add_chunk(synthesize.text):
                    await self._process_and_synthesize(sentence, synthesize)

                final_sentence = self.sbd.finish()
                if final_sentence:
                    await self._process_and_synthesize(final_sentence, synthesize)

                await self._flush_buffer(synthesize)

                if self.audio_started:
                    await self.write_event(AudioStop().event())
                return True

            if self.cli_args.no_streaming:
                return True

            if SynthesizeStart.is_type(event.type):
                stream_start = SynthesizeStart.from_event(event)
                self.is_streaming = True
                self.audio_started = False
                self.sbd = SentenceBoundaryDetector(emit_break_markers=True)
                self._synthesize = Synthesize(text="", voice=stream_start.voice)
                self._sentence_buffer = []
                self._is_first_sentence_in_event = True
                return True

            if SynthesizeChunk.is_type(event.type):
                assert self._synthesize is not None
                stream_chunk = SynthesizeChunk.from_event(event)
                for sentence in self.sbd.add_chunk(stream_chunk.text):
                    await self._process_and_synthesize(sentence, self._synthesize)
                return True

            if SynthesizeStop.is_type(event.type):
                assert self._synthesize is not None
                final_sentence = self.sbd.finish()
                if final_sentence:
                    await self._process_and_synthesize(final_sentence, self._synthesize)

                await self._flush_buffer(self._synthesize)

                if self.audio_started:
                    await self.write_event(AudioStop().event())
                await self.write_event(SynthesizeStopped().event())

                self.is_streaming = False
                self.audio_started = False
                return True

            return True

        except VoiceNotFoundError as err:
            _LOGGER.error(f"Voice not found: {err}")
            await self.write_event(
                Error(text=f"Voice not found: {err}", code="voice_not_found").event()
            )
            return True

        except Exception as err:
            await self.write_event(
                Error(text=str(err), code=err.__class__.__name__).event()
            )
            raise err

    async def _handle_synthesize(
        self,
        synthesize: Synthesize,
        send_stop: bool = True,
        break_type: Optional[str] = None,
    ) -> bool:
        global _VOICES_CACHE

        if not break_type:
            text = " ".join(synthesize.text.strip().splitlines())
            if not text:
                return True
            _LOGGER.debug(f"[{_ts()}] Synth: {text}")

            if self.cli_args.auto_punctuation and text:
                if not any(text.endswith(p) for p in self.cli_args.auto_punctuation):
                    text += self.cli_args.auto_punctuation[0]

        # Получаем имя и спикера
        voice_name, voice_speaker = (
            (synthesize.voice.name, synthesize.voice.speaker)
            if synthesize.voice
            else (None, None)
        )
        voice_name = voice_name or self.cli_args.voice

        if voice_name == self.cli_args.voice:
            voice_speaker = voice_speaker or self.cli_args.speaker

        assert voice_name is not None
        voice_name = self.voices_info.get(voice_name, {}).get("key", voice_name)

        async with _VOICE_LOCK:
            if voice_name in _VOICES_CACHE:
                voice = _VOICES_CACHE.pop(voice_name)
                _VOICES_CACHE[voice_name] = voice
                if not break_type:
                    _LOGGER.debug(f"[{_ts()}] Voice '{voice_name}' in cache.")
            else:
                _LOGGER.debug(f"[{_ts()}] Loading voice: {voice_name}")
                ensure_voice_exists(
                    voice_name,
                    self.cli_args.data_dir,
                    self.cli_args.download_dir,
                    self.voices_info,
                )
                model_path, config_path = find_voice(voice_name, self.cli_args.data_dir)
                voice = PiperVoice.load(
                    model_path, config_path, use_cuda=self.cli_args.use_cuda
                )
                _VOICES_CACHE[voice_name] = voice
                _LOGGER.debug(f"[{_ts()}] Model loaded.")

                while len(_VOICES_CACHE) > max(1, self.cli_args.max_cached_voices):
                    old_v, _ = _VOICES_CACHE.popitem(last=False)
                    _LOGGER.debug(f"[{_ts()}] Evicted '{old_v}' from cache.")

            rate, width, channels = voice.config.sample_rate, 2, 1

            # --- ОБРАБОТКА МАРКЕРОВ ПАУЗ ---
            if break_type:
                if not self.audio_started:
                    await self.write_event(
                        AudioStart(rate=rate, width=width, channels=channels).event()
                    )
                    self.audio_started = True

                if self.cli_args.sentence_silence > 0:
                    multiplier = 1.5 if break_type == "paragraph" else 0.7
                    log_tag = f"[{break_type.upper()}]"

                    silence_sec = self.cli_args.sentence_silence * multiplier
                    silence_bytes = b"\x00" * (
                        int(rate * silence_sec) * width * channels
                    )

                    if silence_bytes:
                        _LOGGER.debug(f"[{_ts()}] {log_tag} +{silence_sec:.2g}s added")
                        await self.write_event(
                            AudioChunk(
                                audio=silence_bytes,
                                rate=rate,
                                width=width,
                                channels=channels,
                            ).event()
                        )

                if send_stop:
                    await self.write_event(AudioStop().event())
                return True

            # --- СИНТЕЗ ТЕКСТА ---
            syn_config = SynthesisConfig(
                length_scale=self.cli_args.length_scale,
                noise_scale=self.cli_args.noise_scale,
                noise_w_scale=self.cli_args.noise_w_scale,
            )

            if voice_speaker is not None:
                syn_config.speaker_id = voice.config.speaker_id_map.get(voice_speaker)
                if syn_config.speaker_id is None:
                    try:
                        syn_config.speaker_id = int(voice_speaker)
                    except (ValueError, TypeError):
                        pass

            if not self.audio_started:
                try:
                    for _ in voice.synthesize("И раз, и два", syn_config):
                        pass
                    _LOGGER.debug(f"[{_ts()}] [WARM-UP]")
                except Exception:
                    pass

                await self.write_event(
                    AudioStart(rate=rate, width=width, channels=channels).event()
                )
                self.audio_started = True

            # Замер производительности
            start_synth_time = time.perf_counter()
            total_audio_bytes = 0

            for chunk in voice.synthesize(text, syn_config):
                chunk_bytes = chunk.audio_int16_bytes
                total_audio_bytes += len(chunk_bytes)
                await self.write_event(
                    AudioChunk(
                        audio=chunk_bytes,
                        rate=rate,
                        width=width,
                        channels=channels,
                    ).event()
                )

            # Расчет метрик
            synth_duration = time.perf_counter() - start_synth_time
            audio_duration = total_audio_bytes / (rate * width * channels)
            rtfx = (audio_duration / synth_duration) if synth_duration > 0 else 0.0

            # --- СТАНДАРТНАЯ ПАУЗА ПОСЛЕ ТЕКСТА ---
            if self.cli_args.sentence_silence > 0:
                silence_sec = random.uniform(
                    self.cli_args.sentence_silence * 0.6, self.cli_args.sentence_silence
                )
                silence_bytes = b"\x00" * (int(rate * silence_sec) * width * channels)

                if silence_bytes:
                    _LOGGER.debug(f"[{_ts()}] [SILENCE] {silence_sec:.2f}s")
                    await self.write_event(
                        AudioChunk(
                            audio=silence_bytes,
                            rate=rate,
                            width=width,
                            channels=channels,
                        ).event()
                    )

            # Итоговый лог с RTFx
            if audio_duration > 0:
                _LOGGER.debug(
                    f"[{_ts()}] [DONE] Audio {audio_duration:.2f}s | "
                    f"Synth {synth_duration:.3f}s | RTFx {rtfx:.1f}x"
                )
            else:
                _LOGGER.debug(f"[{_ts()}] [DONE]")

        if send_stop:
            await self.write_event(AudioStop().event())

        return True