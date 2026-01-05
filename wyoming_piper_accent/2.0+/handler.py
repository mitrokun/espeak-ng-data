"""Event handler for clients of the server."""

import argparse
import asyncio
import logging
import regex as re
from typing import Any, Dict, Optional, Set

from piper import PiperVoice, SynthesisConfig
from .sentence_boundary import SentenceBoundaryDetector
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

from .download import ensure_voice_exists, find_voice

_LOGGER = logging.getLogger(__name__)

# --- Опциональный импорт английского нормализатора ---
try:
    from .english_normalizer import EnglishNormalizer
    ENG_TO_IPA_AVAILABLE = True
except ImportError:
    EnglishNormalizer = None
    ENG_TO_IPA_AVAILABLE = False


# --- Логика расстановки ударений ---

_CORRECTION_WORDS: Set[str] = set([
    "адреса", "алое", "атлас", "беды", "белкa", "белки", "белок",
    "берегу", "берет", "большая", "боры", "бури", "ведение",
    "века", "веках", "верхом", "веса", "вести", "ветра",
    "ветряная", "вечера", "вина", "виски", "воды", "войны",
    "войска", "волны", "ворона", "ворот", "ворота", "выходить",
    "гвоздик", "главы", "глаза", "глотка", "глоток", "глотку",
    "глубины", "глубоко", "гнезда", "года", "головы", "голоса",
    "гоним", "горе", "города", "горы", "господа", "графа",
    "грозы", "гроши", "груди", "дела", "доктора", "дома",
    "дорог", "дороги", "дорогой", "другом", "духи", "душа",
    "души", "дыбы", "еду", "жаркое", "жару", "жила",
    "жучка", "заводи", "залом", "замки", "замок", "заморозки",
    "запах", "заросли", "засели", "засыпал", "здорово", "земли",
    "зеркала", "зимы", "знаком", "игры", "избегать", "извести",
    "ирис", "катера", "кирка", "клещи", "клубы", "козлы",
    "колки", "коне", "корпуса", "краю", "кружка", "кружки",
    "крыла", "леса", "лесок", "лесу", "лета", "лиса",
    "луга", "лука", "любим", "мало", "мастера", "мела",
    "меньшинства", "места", "мести", "меха", "мою", "моя",
    "мудрено", "мука", "муки", "мукой", "начала", "начало",
    "ноги", "номера", "ношу", "нужды", "облака", "озера",
    "окна", "округа", "округе", "опера", "орган", "органов",
    "органом", "органы", "остро", "отпуска", "пайки", "парил",
    "парить", "паром", "паруса", "пекло", "пили", "пирога",
    "письма", "пища", "плачу", "повара", "поезда", "позднее",
    "пола", "полки", "полосы", "полу", "полы", "поля",
    "полюса", "помеси", "попадал", "пора", "поручи", "постели",
    "потерпите", "поту", "пошло", "привод", "пристав", "пристань",
    "проводами", "пропасть", "проруби", "простынь", "просыпался", "пряди",
    "пылу", "реки", "рога", "родами", "руки", "саду",
    "самого", "самой", "самому", "сахара", "сбегать", "сведение",
    "свечи", "связи", "сели", "село", "семьи", "сестры",
    "синее", "скачка", "скачками", "слез", "слезу", "слова",
    "смычка", "содержим", "сорок", "сорока", "соска", "соски",
    "спешить", "спина", "среды", "стада", "стены", "степи",
    "стоит", "стоишь", "стону", "стороны", "стою", "стоящий",
    "страны", "стрелка", "стрелки", "стрелок", "стрелку", "строки",
    "судьбы", "сыром", "толки", "толпы", "тому", "тормоза",
    "трусы", "туши", "тюрьмы", "угольный", "уже", "уха",
    "учителя", "хлопок", "хлопком", "хлопока", "холода", "хоры",
    "хромом", "цвета", "целую", "цепи", "чайку", "часу",
    "чека", "числа", "чудное", "широты"
])

_STRESS_MARK = "\u0301"
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_RUSSIAN_VOWELS_SET = set(_RUSSIAN_VOWELS)


def _count_vowels(word: str) -> int:
    return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)


def preprocess_text_for_stress(text: str, accentor: Optional[Any], user_marker: str = '+') -> str:
    if not accentor:
        return text.replace(user_marker, '')

    try:
        if not _CORRECTION_WORDS and user_marker not in text:
            return accentor(text)

        text_with_silero_stress = accentor(text)
        split_pattern = f'([\\s{re.escape(".,!?-")}]+)'
        original_parts = re.split(split_pattern, text)
        stressed_parts = re.split(split_pattern, text_with_silero_stress)

        if len(original_parts) != len(stressed_parts):
            _LOGGER.warning("Text splitting mismatch. Using Silero's full output.")
            return text_with_silero_stress

        final_parts = [
            stressed_part
            if orig_part.lower().strip(".,!?-") in _CORRECTION_WORDS
            else orig_part
            for orig_part, stressed_part in zip(original_parts, stressed_parts)
        ]
        text_with_markers = "".join(final_parts)

    except Exception:
        _LOGGER.exception("Error during selective stress application. Using original text.")
        text_with_markers = text

    stress_pattern = re.compile(re.escape(user_marker) + f"([{_RUSSIAN_VOWELS}])")
    parts = re.split(f'([\\s{re.escape(".,!?-")}]+)', text_with_markers)
    final_unicode_parts = []
    for part in parts:
        if not part or part.isspace() or part in ".,!?-":
            final_unicode_parts.append(part)
            continue

        if stress_pattern.search(part):
            word = part
            clean_word = word.replace(user_marker, "")
            if _count_vowels(clean_word) <= 1:
                final_unicode_parts.append(clean_word)
            else:
                processed_word = stress_pattern.sub(lambda m: m.group(1) + _STRESS_MARK, word)
                final_unicode_parts.append(processed_word)
        else:
            final_unicode_parts.append(part)

    return "".join(final_unicode_parts)


# --- Глобальные переменные для кеширования голоса ---
_VOICE: Optional[PiperVoice] = None
_VOICE_NAME: Optional[str] = None
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

        if ENG_TO_IPA_AVAILABLE and EnglishNormalizer:
            self.eng_normalizer = EnglishNormalizer()
            _LOGGER.debug("English-to-Russian normalizer is enabled.")
        else:
            self.eng_normalizer = None
            _LOGGER.warning("`eng-to-ipa` library not found. English-to-Russian normalizer is disabled. Run `pip install eng-to-ipa` to enable it.")

    async def _process_and_synthesize(self, sentence: str, synthesize_obj: Synthesize):
        if not sentence.strip():
            return

        if self.eng_normalizer:
            sentence = self.eng_normalizer.normalize(sentence)

        stressed_sentence = preprocess_text_for_stress(sentence, self.accentor)
        _LOGGER.debug(f"Final text for synthesis: {stressed_sentence}")

        synthesize_obj.text = stressed_sentence
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
        global _VOICE, _VOICE_NAME

        text = " ".join(synthesize.text.strip().splitlines())
        if not text:
            return True

        _LOGGER.debug(f"Synthesizing: {synthesize}")

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

        async with _VOICE_LOCK:
            if voice_name != _VOICE_NAME:
                _LOGGER.debug(f"Loading voice: {voice_name}")
                ensure_voice_exists(
                    voice_name,
                    self.cli_args.data_dir,
                    self.cli_args.download_dir,
                    self.voices_info,
                )
                model_path, config_path = find_voice(voice_name, self.cli_args.data_dir)
                _VOICE, _VOICE_NAME = PiperVoice.load(model_path, config_path, use_cuda=self.cli_args.use_cuda), voice_name

            assert _VOICE is not None

            syn_config = SynthesisConfig(
                length_scale=self.cli_args.length_scale,
                noise_scale=self.cli_args.noise_scale,
                noise_w_scale=self.cli_args.noise_w_scale,
            )

            if voice_speaker is not None:
                syn_config.speaker_id = _VOICE.config.speaker_id_map.get(voice_speaker)
                if syn_config.speaker_id is None:
                    try:
                        syn_config.speaker_id = int(voice_speaker)
                    except (ValueError, TypeError):
                        _LOGGER.warning("Speaker '%s' not found for voice '%s'", voice_speaker, voice_name)

            rate, width, channels = _VOICE.config.sample_rate, 2, 1

            if self.audio_started and self.cli_args.sentence_silence > 0:
                num_silence_samples = int(rate * self.cli_args.sentence_silence)
                
                # 2 байта на семпл (16-бит)
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

            for chunk in _VOICE.synthesize(text, syn_config):
                await self.write_event(
                    AudioChunk(
                        audio=chunk.audio_int16_bytes,
                        rate=rate,
                        width=width,
                        channels=channels,
                    ).event()
                )

        if send_stop:
            await self.write_event(AudioStop().event())

        return True
