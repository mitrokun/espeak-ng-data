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


_CORRECTION_WORDS: Set[str] = {
    "адреса", "атлас", "беды", "белкa", "белки", "белок", "берегу",
    "большая", "боры", "бури", "ведение", "верхом", "вести", "веса",
    "века", "веках", "ветряная", "вечера", "вина", "виски", "войны",
    "войска", "воды", "ворона", "ворот", "ворота", "выходить", "гвоздик",
    "глаза", "глотка", "глоток", "глотку", "глубины", "глубоко", "года",
    "головы", "гоним", "горе", "города", "господа", "графа", "гроши",
    "дела", "дорог", "дороги", "дорогой", "другом", "духи", "душа",
    "души", "дыбы", "еду", "жаркое", "жару", "жила", "жучка", "заводи",
    "залом", "замок", "замки", "запах", "заросли", "засели", "засыпал",
    "здорово", "земли", "зимы", "знаком", "избегать", "извести", "игры",
    "ирис", "катера", "кирка", "клещи", "клубы", "козлы", "колки",
    "коне", "корпуса", "краю", "кружка", "кружки", "крыла", "леса",
    "лесок", "лета", "лиса", "лука", "любим", "мало", "мастера",
    "мела", "меньшинства", "места", "мести", "меха", "мою", "моя",
    "мудрено", "мука", "муки", "мукой", "начала", "начало", "ноги", "ношу", "нужды", "облака",
    "окна", "опера", "орган", "остро", "отпуска", "пайки", "парил", "паруса",
    "парить", "паром", "пекло", "пили", "письма", "пирога", "пища",
    "плачу", "повара", "пола", "полки", "полосы", "полу", "полы",
    "поля", "полюса", "попадал", "пора", "поручи", "постели", "потом",
    "поту", "пошло", "привод", "пристав", "пристань", "пропасть",
    "простынь", "пряди", "пылу", "реки", "рога", "руки", "самого",
    "самой", "самому", "саду", "сведение", "свечи", "связи", "сели",
    "село", "семьи", "сестры", "синее", "скачка", "слез", "слезу",
    "слова", "смычка", "содержим", "сорок", "сорока", "спешить",
    "спина", "стада", "стоишь", "стоит", "стону", "стороны", "стою",
    "стоящий", "страны", "стрелка", "стрелки", "стрелку", "стрелок",
    "судьбы", "сыром", "толки", "толпы", "тому", "трусы", "туши",
    "тюрьмы", "угольный", "уже", "уха", "хлопок", "хоры", "хромом",
    "целую", "цепи", "цвета", "чайку", "часу", "чека", "чудное",
    "широты", "просыпался",
}
_STRESS_MARK = "\u0301"
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_RUSSIAN_VOWELS_SET = set(_RUSSIAN_VOWELS)
def _count_vowels(word: str) -> int: return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)
def preprocess_text_for_stress(text: str, accentor: Optional[Any], user_marker: str = '+') -> str:
    if not accentor: return text.replace(user_marker, '')
    try:
        if not _CORRECTION_WORDS and user_marker not in text: return accentor(text)
        text_with_silero_stress = accentor(text)
        split_pattern = f'([\\s{re.escape(".,!?-")}]+)'
        original_parts = re.split(split_pattern, text)
        stressed_parts = re.split(split_pattern, text_with_silero_stress)
        final_parts = []
        if len(original_parts) != len(stressed_parts):
            _LOGGER.warning("Text splitting mismatch. Using Silero's full output.")
            final_parts = stressed_parts
        else:
            for orig_part, stressed_part in zip(original_parts, stressed_parts):
                lookup_key = orig_part.lower().strip(".,!?-")
                if lookup_key in _CORRECTION_WORDS:
                    _LOGGER.debug("Applying Silero stress for word: '%s' -> '%s'", orig_part, stressed_part)
                    final_parts.append(stressed_part)
                else: final_parts.append(orig_part)
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
            if _count_vowels(clean_word) <= 1: final_unicode_parts.append(clean_word)
            else:
                processed_word = stress_pattern.sub(lambda m: m.group(1) + _STRESS_MARK, word)
                final_unicode_parts.append(processed_word)
        else: final_unicode_parts.append(part)
    return "".join(final_unicode_parts)

# --- Глобальные переменные для кеширования голоса ---
_VOICE: Optional[PiperVoice] = None
_VOICE_NAME: Optional[str] = None
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
        # <<< ИЗМЕНЕНИЕ: Создаем экземпляр НАШЕГО детектора
        self.sbd = SentenceBoundaryDetector()
        self._synthesize: Optional[Synthesize] = None

    async def _process_and_synthesize(self, sentence: str, synthesize_obj: Synthesize):
        """Применяет ударения и отправляет на синтез."""
        if not sentence.strip():
            return
        
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
                if self.is_streaming: return True
                synthesize = Synthesize.from_event(event)
                self.sbd = SentenceBoundaryDetector()
                self.audio_started = False
                
                for sentence in self.sbd.add_chunk(synthesize.text):
                    await self._process_and_synthesize(sentence, synthesize)
                
                for sentence in self.sbd.finish():
                    await self._process_and_synthesize(sentence, synthesize)

                if self.audio_started: await self.write_event(AudioStop().event())
                return True

            if self.cli_args.no_streaming: return True

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
                for sentence in self.sbd.finish():
                    await self._process_and_synthesize(sentence, self._synthesize)

                if self.audio_started: await self.write_event(AudioStop().event())
                await self.write_event(SynthesizeStopped().event())
                self.is_streaming = False
                self.audio_started = False
                return True

            return True

        except Exception as err:
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            raise err

    # _handle_synthesize остается без изменений
    async def _handle_synthesize(self, synthesize: Synthesize, send_stop: bool = True) -> bool:
        global _VOICE, _VOICE_NAME
        raw_text = synthesize.text
        if not raw_text.strip(): return True
        _LOGGER.debug(f"Synthesizing: {synthesize}")
        text = " ".join(raw_text.strip().splitlines())
        if self.cli_args.auto_punctuation and text:
            has_punctuation = any(text.endswith(p) for p in self.cli_args.auto_punctuation)
            if not has_punctuation: text = text + self.cli_args.auto_punctuation[0]
        voice_name: Optional[str] = None
        voice_speaker: Optional[str] = None
        if synthesize.voice is not None:
            voice_name = synthesize.voice.name
            voice_speaker = synthesize.voice.speaker
        voice_name = voice_name or self.cli_args.voice
        if voice_name == self.cli_args.voice:
            voice_speaker = voice_speaker or self.cli_args.speaker
        assert voice_name is not None
        voice_info = self.voices_info.get(voice_name, {})
        voice_name = voice_info.get("key", voice_name)
        assert voice_name is not None
        async with _VOICE_LOCK:
            if voice_name != _VOICE_NAME:
                _LOGGER.debug(f"Loading voice: {voice_name}")
                ensure_voice_exists(
                    voice_name, self.cli_args.data_dir, self.cli_args.download_dir, self.voices_info
                )
                model_path, config_path = find_voice(voice_name, self.cli_args.data_dir)
                _VOICE = PiperVoice.load(model_path, config_path, use_cuda=self.cli_args.use_cuda)
                _VOICE_NAME = voice_name
            assert _VOICE is not None
            syn_config = SynthesisConfig()
            if voice_speaker is not None:
                syn_config.speaker_id = _VOICE.config.speaker_id_map.get(voice_speaker)
                if syn_config.speaker_id is None:
                    try: syn_config.speaker_id = int(voice_speaker)
                    except (ValueError, TypeError): _LOGGER.warning("Speaker '%s' not found for voice '%s'", voice_speaker, voice_name)
            syn_config.length_scale = self.cli_args.length_scale
            syn_config.noise_scale = self.cli_args.noise_scale
            syn_config.noise_w_scale = self.cli_args.noise_w_scale
            rate = _VOICE.config.sample_rate
            width = 2
            channels = 1
            if not self.audio_started:
                await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
                self.audio_started = True
            for chunk in _VOICE.synthesize(text, syn_config):
                await self.write_event(
                    AudioChunk(
                        audio=chunk.audio_int16_bytes, rate=rate, width=width, channels=channels
                    ).event()
                )
        if send_stop: await self.write_event(AudioStop().event())
        return True