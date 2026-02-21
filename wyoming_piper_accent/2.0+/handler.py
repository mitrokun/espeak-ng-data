"""Event handler for clients of the server."""

import argparse
import asyncio
import logging
import regex as re
import datetime
import json
from collections import OrderedDict
from typing import Any, Dict, Optional, Set

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

from .sentence_boundary import SentenceBoundaryDetector
from .download import ensure_voice_exists, find_voice
from .homographs import CORRECTION_WORDS

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


def _count_vowels(word: str) -> int:
    return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)


def preprocess_text_for_stress(text: str, accentor: Optional[Any], user_marker: str = '+') -> str:
    current_text = text

    # ШАГ 1: Автоматические омографы через Silero
    if accentor and CORRECTION_WORDS:
        try:
            text_with_silero_stress = accentor(text)
            split_pattern = f'([\\s{re.escape(".,!?-")}]+)'
            
            original_parts = re.split(split_pattern, text)
            stressed_parts = re.split(split_pattern, text_with_silero_stress)

            if len(original_parts) == len(stressed_parts):
                final_parts = []
                for orig_part, stressed_part in zip(original_parts, stressed_parts):
                    if orig_part.lower().strip(".,!?-") in CORRECTION_WORDS:
                        final_parts.append(stressed_part)
                    else:
                        final_parts.append(orig_part)
                current_text = "".join(final_parts)
        except Exception as e:
            _LOGGER.debug(f"[{_ts()}] Silero error: {e}")

    # ШАГ 2: Универсальная обработка плюсов (за+мок -> за́мок)
    # Работает, даже если accentor = None
    if user_marker in current_text:
        stress_pattern = re.compile(re.escape(user_marker) + f"([{_RUSSIAN_VOWELS}])")
        parts = re.split(f'([\\s{re.escape(".,!?-")}]+)', current_text)
        final_unicode_parts = []
        
        for part in parts:
            if not part or part.isspace() or part in ".,!?-":
                final_unicode_parts.append(part)
                continue
                
            if user_marker in part:
                clean_word = part.replace(user_marker, "")
                # Не ставим ударение в словах из 1 слога
                if _count_vowels(clean_word) <= 1:
                    final_unicode_parts.append(clean_word)
                else:
                    # Превращаем + гласная в гласная + Unicode-ударение
                    processed_word = stress_pattern.sub(lambda m: m.group(1) + _STRESS_MARK, part)
                    final_unicode_parts.append(processed_word)
            else:
                final_unicode_parts.append(part)
        return "".join(final_unicode_parts)

    return current_text.replace(user_marker, '')


_VOICES_CACHE: OrderedDict[str, PiperVoice] = OrderedDict()
_VOICE_LOCK = asyncio.Lock()


class PiperEventHandler(AsyncEventHandler):
    def __init__(self, wyoming_info: Info, cli_args: argparse.Namespace, voices_info: Dict[str, Any], accentor: Optional[Any], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.voices_info = voices_info
        self.accentor = accentor
        self.is_streaming: bool = False
        self.audio_started: bool = False
        self.sbd = SentenceBoundaryDetector()
        self._synthesize: Optional[Synthesize] = None

        self._custom_configs_cache: Dict[str, dict] = {}

        self.eng_normalizer = EnglishNormalizer() if ENG_AVAILABLE else None
        self.rus_normalizer = RussianNormalizer() if RUS_AVAILABLE else None

    async def _process_and_synthesize(self, sentence: str, synthesize_obj: Synthesize):
        if not sentence.strip():
            return

        current_text = sentence
        _LOGGER.debug(f"[{_ts()}] [RAW] {current_text}")

        # --- ПРОВЕРКА ЯЗЫКА ---
        voice_name = (synthesize_obj.voice.name if synthesize_obj.voice else None) or self.cli_args.voice
        assert voice_name is not None
        
        # 1. Пытаемся взять из официального каталога
        voice_info = self.voices_info.get(voice_name)

        # 2. Если это кастомный голос, читаем его конфиг (и кэшируем)
        if not voice_info:
            if voice_name not in self._custom_configs_cache:
                try:
                    _, config_path = find_voice(voice_name, self.cli_args.data_dir)
                    with open(config_path, "r", encoding="utf-8") as f:
                        self._custom_configs_cache[voice_name] = json.load(f)
                except Exception as e:
                    _LOGGER.debug(f"[{_ts()}] Could not load custom config for {voice_name}: {e}")
                    self._custom_configs_cache[voice_name] = {}

            voice_info = self._custom_configs_cache[voice_name]
        
        lang_id = (
            voice_info.get("espeak", {}).get("voice") or 
            voice_info.get("language", {}).get("code") or 
            voice_name
        )
        
        is_russian = str(lang_id).lower().startswith("ru")

        if is_russian:
            if self.eng_normalizer:
                transformed = self.eng_normalizer.normalize(current_text)
                if transformed != current_text:
                    current_text = transformed
                    _LOGGER.debug(f"[{_ts()}] [ENG] {current_text}")

            if self.rus_normalizer:
                transformed = self.rus_normalizer.normalize(current_text)
                if transformed != current_text:
                    current_text = transformed
                    _LOGGER.debug(f"[{_ts()}] [RUS] {current_text}")

            transformed = preprocess_text_for_stress(current_text, self.accentor)
            if transformed != current_text:
                current_text = transformed
                _LOGGER.debug(f"[{_ts()}] [STR] {current_text}")
                
            _LOGGER.debug(f"[{_ts()}] [PREP DONE]")
        else:
            _LOGGER.debug(f"[{_ts()}] [PREP SKIP] Non-Russian voice ({lang_id})")

        # Финальная отправка в синтезатор
        synthesize_obj.text = current_text
        await self._handle_synthesize(synthesize_obj, send_stop=False)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            return True

        try:
            if Synthesize.is_type(event.type):
                if self.is_streaming:
                    return True

                synthesize = Synthesize.from_event(event)
                self.sbd = SentenceBoundaryDetector()
                self.audio_started = False

                for sentence in self.sbd.add_chunk(synthesize.text):
                    await self._process_and_synthesize(sentence, synthesize)

                final_sentence = self.sbd.finish()
                if final_sentence:
                    await self._process_and_synthesize(final_sentence, synthesize)

                if self.audio_started:
                    await self.write_event(AudioStop().event())
                return True

            if self.cli_args.no_streaming:
                return True

            if SynthesizeStart.is_type(event.type):
                stream_start = SynthesizeStart.from_event(event)
                self.is_streaming = True
                self.audio_started = False
                self.sbd = SentenceBoundaryDetector()
                self._synthesize = Synthesize(text="", voice=stream_start.voice)
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

                if self.audio_started:
                    await self.write_event(AudioStop().event())

                await self.write_event(SynthesizeStopped().event())
                self.is_streaming = False
                self.audio_started = False
                return True

            return True

        except Exception as err:
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            raise err

    async def _handle_synthesize(self, synthesize: Synthesize, send_stop: bool = True) -> bool:
        global _VOICES_CACHE

        text = " ".join(synthesize.text.strip().splitlines())
        if not text:
            return True

        _LOGGER.debug(f"[{_ts()}] Synthesizing: {text}")

        if self.cli_args.auto_punctuation and text:
            if not any(text.endswith(p) for p in self.cli_args.auto_punctuation):
                text += self.cli_args.auto_punctuation[0]

        voice_name, voice_speaker = (synthesize.voice.name, synthesize.voice.speaker) if synthesize.voice else (None, None)
        voice_name = voice_name or self.cli_args.voice
        if voice_name == self.cli_args.voice:
            voice_speaker = voice_speaker or self.cli_args.speaker

        assert voice_name is not None
        voice_name = self.voices_info.get(voice_name, {}).get("key", voice_name)
        assert voice_name is not None

        # Минимум 1 голос должен быть загружен в кэш
        max_voices = max(1, self.cli_args.max_cached_voices)

        async with _VOICE_LOCK:
            if voice_name in _VOICES_CACHE:

                voice = _VOICES_CACHE.pop(voice_name)
                _VOICES_CACHE[voice_name] = voice
                _LOGGER.debug(f"[{_ts()}] Voice '{voice_name}' in cache.")
            else:
                # Голоса нет в памяти, нужно загружать
                _LOGGER.debug(f"[{_ts()}] Loading voice: {voice_name}")
                ensure_voice_exists(
                    voice_name,
                    self.cli_args.data_dir,
                    self.cli_args.download_dir,
                    self.voices_info,
                )
                model_path, config_path = find_voice(voice_name, self.cli_args.data_dir)
                voice = PiperVoice.load(model_path, config_path, use_cuda=self.cli_args.use_cuda)
                _LOGGER.debug(f"[{_ts()}] Model loaded.")

                # Добавляем новый голос в кэш
                _VOICES_CACHE[voice_name] = voice

                # Если кэш переполнен, удаляем самый старый голос
                while len(_VOICES_CACHE) > max_voices:
                    oldest_voice_name, _ = _VOICES_CACHE.popitem(last=False)
                    _LOGGER.debug(f"[{_ts()}] Evicted '{oldest_voice_name}' from RAM cache to free space.")

            # voice теперь гарантированно содержит нужный PiperVoice
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
                        _LOGGER.warning(f"[{_ts()}] Speaker '{voice_speaker}' not found.")

            rate, width, channels = voice.config.sample_rate, 2, 1

            if self.audio_started and self.cli_args.sentence_silence > 0:
                num_silence_samples = int(rate * self.cli_args.sentence_silence)
                silence_bytes = b'\x00' * (num_silence_samples * width * channels)
                
                if silence_bytes:
                    await self.write_event(
                        AudioChunk(
                            audio=silence_bytes,
                            rate=rate,
                            width=width,
                            channels=channels,
                        ).event()
                    )

            if not self.audio_started:
                await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
                self.audio_started = True

            for chunk in voice.synthesize(text, syn_config):
                await self.write_event(
                    AudioChunk(
                        audio=chunk.audio_int16_bytes,
                        rate=rate,
                        width=width,
                        channels=channels,
                    ).event()
                )

            _LOGGER.debug(f"[{_ts()}] [DONE]")

        if send_stop:
            await self.write_event(AudioStop().event())

        return True