import argparse
import json
import logging
import asyncio
import re
from typing import Any, Dict, Optional, Set

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

from .process import PiperProcessManager
from .sentence_boundary import SentenceBoundaryDetector

_LOGGER = logging.getLogger(__name__)

# Словарь для коррекции ударений, отсортированный по алфавиту
_CORRECTION_WORDS: Set[str] = {
    "адреса", "атлас", "беды", "белкa", "белки", "белок", "берегу",
    "большая", "боры", "бури", "ведение", "верхом", "вести", "веса",
    "века", "веках", "ветра", "ветряная", "вечера", "вина", "виски", "войны",
    "волны", "войска", "воды", "ворона", "ворот", "ворота", "выходить", "гвоздик",
    "глаза", "глотка", "глоток", "глотку", "глубины", "глубоко", "года",
    "головы", "голоса", "гоним", "горе", "города", "господа", "графа", "гроши",
    "груди", "дела", "дорог", "дороги", "дорогой", "другом", "духи", "душа",
    "души", "дыбы", "еду", "жаркое", "жару", "жила", "жучка", "заводи",
    "залом", "замок", "замки", "запах", "заросли", "засели", "засыпал",
    "здорово", "земли", "зимы", "знаком", "избегать", "извести", "игры",
    "ирис", "катера", "кирка", "клещи", "клубы", "козлы", "колки",
    "коне", "корпуса", "краю", "кружка", "кружки", "крыла", "леса",
    "лесок", "лета", "лиса", "луга", "лука", "любим", "мало", "мастера",
    "мела", "меньшинства", "места", "мести", "меха", "мою", "моя",
    "мудрено", "мука", "муки", "мукой", "начала", "начало", "ноги", "номера", "ношу", "нужды", "облака",
    "окна", "опера", "орган", "органы", "органов", "органом", "остро", "отпуска",
    "пайки", "парил", "паруса","парить", "паром", "пекло", "пили", "письма", "пирога", "пища",
    "плачу", "повара", "поезда", "пола", "полки", "полосы", "полу", "полы",
    "поля", "полюса", "попадал", "пора", "поручи", "постели", "потом",
    "поту", "пошло", "привод", "пристав", "пристань", "пропасть",
    "простынь", "пряди", "пылу", "реки", "рога", "руки", "самого",
    "самой", "самому", "саду", "сахара", "сведение", "свечи", "связи", "сели",
    "село", "семьи", "сестры", "синее", "скачка", "слез", "слезу", 
    "слова", "смычка", "содержим", "соска", "соски", "сорок", "сорока", "спешить",
    "спина", "стада", "стены", "стоишь", "стоит", "стону", "стороны", "стою",
    "стоящий", "страны", "стрелка", "стрелки", "стрелку", "стрелок",
    "судьбы", "сыром", "строки", "толки", "толпы", "тому", "трусы", "туши",
    "тюрьмы", "угольный", "уже", "уха", "холода", "хлопок", "хоры", "хромом",
    "целую", "цепи", "цвета", "чайку", "часу", "чека", "чудное",
    "широты", "просыпался",
}
# "всем", "все", "нем", "пчелы", "села", "стекла", "чем", "черта", "берег", "звезды", "озера",

_STRESS_MARK = "\u0301"
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_RUSSIAN_VOWELS_SET = set(_RUSSIAN_VOWELS)


def _count_vowels(word: str) -> int:
    return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)


def preprocess_text_for_stress(
    text: str,
    accentor: Optional[Any],
    user_marker: str = '+'
) -> str:
    if not accentor or not _CORRECTION_WORDS:
        if user_marker not in text:
            return text
        _LOGGER.debug("Accentor not loaded, but manual stress markers found for processing.")
        text_with_markers = text
    else:
        _LOGGER.debug("Applying selective stress correction for %d words.", len(_CORRECTION_WORDS))
        try:
            text_with_silero_stress = accentor(text)
            _LOGGER.debug("Full text from Silero: %s", text_with_silero_stress)
            split_pattern = f'([\\s{re.escape(".,!?-")}]+)'
            original_parts = re.split(split_pattern, text)
            stressed_parts = re.split(split_pattern, text_with_silero_stress)
            if len(original_parts) != len(stressed_parts):
                _LOGGER.warning("Text splitting mismatch. Using Silero's full output.")
                text_with_markers = text_with_silero_stress
            else:
                final_parts = []
                for orig_part, stressed_part in zip(original_parts, stressed_parts):
                    lookup_key = orig_part.lower().strip(".,!?-")
                    if lookup_key in _CORRECTION_WORDS:
                        _LOGGER.debug("Applying Silero stress for word: '%s' -> '%s'", orig_part, stressed_part)
                        final_parts.append(stressed_part)
                    else:
                        final_parts.append(orig_part)
                text_with_markers = "".join(final_parts)
        except Exception:
            _LOGGER.exception("Error during selective stress application. Using original text.")
            text_with_markers = text
    
    _LOGGER.debug("Text with stress: %s", text_with_markers)
    
    # Этот паттерн определяет валидное ударение: маркер '+' ПЕРЕД гласной буквой.
    # Он будет использоваться и для проверки, и для замены.
    stress_pattern = re.compile(re.escape(user_marker) + f"([{_RUSSIAN_VOWELS}])")
    
    parts = re.split(f'([\\s{re.escape(".,!?-")}]+)', text_with_markers)
    final_unicode_parts = []
    
    for part in parts:
        if not part or part.isspace() or part in ".,!?-":
            final_unicode_parts.append(part)
            continue
        
        # Обрабатываем слово, только если в нем найдено валидное ударение ('+гласная').
        # Это автоматически отфильтрует "5+5", "A+B" и т.д.
        if stress_pattern.search(part):
            word = part
            clean_word = word.replace(user_marker, "")
            if _count_vowels(clean_word) <= 1:
                final_unicode_parts.append(clean_word)
            else:
                def stress_replacer(match):
                    return match.group(1) + _STRESS_MARK
                processed_word = stress_pattern.sub(stress_replacer, word)
                final_unicode_parts.append(processed_word)
        else:
            # Если валидное ударение не найдено, добавляем часть без изменений.
            final_unicode_parts.append(part)
            
    return "".join(final_unicode_parts)


class PiperEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        process_manager: PiperProcessManager,
        accentor: Optional[Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.process_manager = process_manager
        self.accentor = accentor
        self.sbd = SentenceBoundaryDetector()
        self._synthesize: Optional[Synthesize] = None
        self._is_streaming_session: bool = False
        self._audio_started: bool = False

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        try:
            if Synthesize.is_type(event.type):
                if self._is_streaming_session:
                    _LOGGER.warning("Received Synthesize event during an active stream. Ignoring.")
                    return True
                synthesize = Synthesize.from_event(event)
                return await self._handle_synthesize(synthesize)

            if not self.cli_args.streaming:
                return True

            if SynthesizeStart.is_type(event.type):
                stream_start = SynthesizeStart.from_event(event)
                _LOGGER.debug("Stream session started: voice=%s", stream_start.voice)
                
                self._is_streaming_session = True
                self._audio_started = False
                self.sbd = SentenceBoundaryDetector()
                self._synthesize = Synthesize(text="", voice=stream_start.voice)
                return True

            if SynthesizeChunk.is_type(event.type):
                if not self._is_streaming_session:
                    _LOGGER.warning("Received SynthesizeChunk without an active session. Ignoring.")
                    return True

                assert self._synthesize is not None
                stream_chunk = SynthesizeChunk.from_event(event)
                for sentence in self.sbd.add_chunk(stream_chunk.text):
                    _LOGGER.debug("Synthesizing stream sentence: %s", sentence)
                    self._synthesize.text = sentence
                    await self._handle_synthesize(self._synthesize)
                return True

            if SynthesizeStop.is_type(event.type):
                if not self._is_streaming_session:
                    _LOGGER.warning("Received SynthesizeStop without an active session. Ignoring.")
                    return True

                assert self._synthesize is not None
                self._synthesize.text = self.sbd.finish()
                if self._synthesize.text:
                    await self._handle_synthesize(self._synthesize)

                if self._audio_started:
                    await self.write_event(AudioStop().event())
                    _LOGGER.debug("Sent final AudioStop for the session.")

                await self.write_event(SynthesizeStopped().event())
                _LOGGER.debug("Stream session stopped")
                
                self._is_streaming_session = False
                self._audio_started = False
                return True

            return True

        except Exception as err:
            _LOGGER.exception("Error handling event")
            self._is_streaming_session = False
            self._audio_started = False
            await self.write_event(
                Error(text=str(err), code=err.__class__.__name__).event()
            )
            return False

    async def _handle_synthesize(self, synthesize: Synthesize) -> bool:
        _LOGGER.debug("Original text from client: %s", synthesize.text)
        
        text_with_stress = preprocess_text_for_stress(
            synthesize.text, self.accentor
        )
        
        _LOGGER.debug("Text after stress processing: %s", text_with_stress)
        
        text = " ".join(text_with_stress.strip().splitlines())

        if self.cli_args.auto_punctuation and text:
            has_punctuation = any(text.endswith(p) for p in self.cli_args.auto_punctuation)
            if not has_punctuation:
                text = text + self.cli_args.auto_punctuation[0]

        async with self.process_manager.processes_lock:
            _LOGGER.debug("Acquired process lock for text: '%s'", text)
            
            voice_name = synthesize.voice.name if synthesize.voice else None
            voice_speaker = synthesize.voice.speaker if synthesize.voice else None

            piper_proc = await self.process_manager.get_process(voice_name=voice_name)
            assert piper_proc.proc.stdin and piper_proc.proc.stdout

            piper_proc.synthesis_done.clear()
            
            audio_config = piper_proc.config.get("audio", {})
            rate = audio_config.get("sample_rate", 22050)
            width = 2
            channels = 1

            input_obj: Dict[str, Any] = {"text": text}
            if voice_speaker:
                speaker_id = piper_proc.get_speaker_id(voice_speaker)
                if speaker_id is not None:
                    input_obj["speaker_id"] = speaker_id
                else:
                    _LOGGER.warning("Speaker '%s' not found for voice '%s'", voice_speaker, voice_name)

            input_json = json.dumps(input_obj, ensure_ascii=False)
            _LOGGER.debug("Sending to piper stdin: %s", input_json)
            
            piper_proc.proc.stdin.write((input_json + "\n").encode("utf-8"))
            await piper_proc.proc.stdin.drain()

            if not self._audio_started:
                await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
                self._audio_started = True
            
            bytes_per_chunk = self.cli_args.samples_per_chunk * width * channels
            
            read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))
            done_task = asyncio.create_task(piper_proc.synthesis_done.wait())
            
            synthesis_finished = False
            try:
                while True:
                    if synthesis_finished:
                        try:
                            chunk = await asyncio.wait_for(read_task, timeout=0.1)
                        except asyncio.TimeoutError:
                            _LOGGER.debug("Stdout buffer is now considered empty.")
                            break
                    else:
                        finished, pending = await asyncio.wait(
                            [read_task, done_task], return_when=asyncio.FIRST_COMPLETED
                        )
                        if done_task in finished:
                            _LOGGER.debug("Synthesis done event received. Will now drain stdout buffer.")
                            synthesis_finished = True
                            if read_task not in finished:
                                continue
                        chunk = read_task.result()
                    if not chunk:
                        _LOGGER.debug("Piper stdout closed (EOF).")
                        break
                    
                    await self.write_event(
                        AudioChunk(audio=chunk, rate=rate, width=width, channels=channels).event()
                    )
                    read_task = asyncio.create_task(piper_proc.proc.stdout.read(bytes_per_chunk))

            finally:
                if 'read_task' in locals() and not read_task.done():
                    read_task.cancel()
                if not done_task.done():
                    done_task.cancel()
                
                if not self._is_streaming_session:
                    await self.write_event(AudioStop().event())
                    _LOGGER.debug("Completed non-streaming request and sent AudioStop.")
                else:
                    _LOGGER.debug("Completed synthesis for a streaming chunk.")

        return True