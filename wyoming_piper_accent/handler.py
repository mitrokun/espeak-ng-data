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

_CORRECTION_WORDS: Set[str] = {
    # Омографы, имена собственные и проблемные случаи
    "атлас", "белки", "берегу", "большая", "боры", "броня",
    "бури", "ведение", "верхом", "вести", "ветряная",
    "вина", "виски", "ворона", "ворот", "ворота",
    "все", "выходить", "гвоздик", "глотка", "глоток", "города",
    "горе", "господа", "готов", "графа", "гроши", "дорогой",
    "дорог", "духи", "душа", "дыбы", "еду", "жаркое",
    "жила", "жучка", "заводи", "залом", "замок", "запах",
    "засели", "здорово", "знаком", "избегать", "извести", "ирис",
    "кирка", "клещи", "клубы", "козлы", "колки", "коне",
    "кружка", "кружки", "крыла", "леса", "лесок", "лиса",
    "лука", "мало", "мела", "меньшинства", "мести", "меха",
    "миловать", "милую", "мою", "мудрено", "мука", "начала",
    "начало", "одержим", "опера", "орган", "пайки", "пали",
    "парить", "паром", "пекло", "перед", "пили", "пища",
    "плачу", "пола", "полки", "полы", "пора", "порты", "пирога",
    "поручи", "постели", "потом", "пошло", "привод", "пристав",
    "пристань", "провод", "пропасть", "простынь", "пряди", "пчелы",
    "самого", "сведение",
    "села", "сели", "село", "синее", "скачка", "смычка",
    "сорок", "сорока", "спешить", "спина", "стекла", "стоит",
    "стою", "стоящий", "стоишь", "стрелка", "стрелки", "стрелку", "сыром",
    "толки", "торги", "трезвение", "трусы", "туши", "угольный",
    "уже", "уха", "хлопок", "хоры", "хромом", "целую",
    "чайку", "чека", "чёрта", "чудное", "связи", "слова", "земли", "катера",
    "один", "судьбы", "корпуса", "краю", "дела", "войны", "глубины", "поту",
    "цвета", "головы", "облака",
}


_STRESS_MARK = "\u0301"
_RUSSIAN_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_RUSSIAN_VOWELS_SET = set(_RUSSIAN_VOWELS)

def _count_vowels(word: str) -> int:
    """Вспомогательная функция для подсчета гласных в слове."""
    return sum(1 for char in word if char in _RUSSIAN_VOWELS_SET)


def preprocess_text_for_stress(
    text: str, 
    accentor: Optional[Any],
    user_marker: str = '+'
) -> str:
    """
    Применяет ударения от Silero только для заданного списка проблемных слов.
    """
    # Если Silero не загружен или список слов пуст, выходим.
    # Но перед этим проверяем, нет ли в тексте ручных ударений.
    if not accentor or not _CORRECTION_WORDS:
        if user_marker not in text:
            return text  # Нет ни модели, ни ручных маркеров - делать нечего.
        
        _LOGGER.debug("Accentor not loaded, but manual stress markers found for processing.")
        text_with_markers = text
    else:
        _LOGGER.debug("Applying selective stress correction for %d words.", len(_CORRECTION_WORDS))
        try:
            # Этап 1: Получаем полностью ударный текст от Silero
            text_with_silero_stress = accentor(text)
            _LOGGER.debug("Full text from Silero: %s", text_with_silero_stress)

            # Этап 2: Собираем гибридный текст
            split_pattern = f'([\\s{re.escape(".,!?-")}]+)'
            original_parts = re.split(split_pattern, text)
            stressed_parts = re.split(split_pattern, text_with_silero_stress)

            # Проверка на случай, если Silero изменил структуру текста
            if len(original_parts) != len(stressed_parts):
                _LOGGER.warning("Text splitting mismatch. Using Silero's full output.")
                text_with_markers = text_with_silero_stress
            else:
                final_parts = []
                for orig_part, stressed_part in zip(original_parts, stressed_parts):
                    # Ключ для поиска - слово в нижнем регистре без знаков препинания
                    lookup_key = orig_part.lower().strip(".,!?-")
                    if lookup_key in _CORRECTION_WORDS:
                        # Если слово в нашем списке, берем версию с ударением от Silero
                        _LOGGER.debug("Applying Silero stress for word: '%s' -> '%s'", orig_part, stressed_part)
                        final_parts.append(stressed_part)
                    else:
                        # Иначе - берем оригинальное слово без ударения
                        final_parts.append(orig_part)
                
                text_with_markers = "".join(final_parts)

        except Exception:
            _LOGGER.exception("Error during selective stress application. Using original text.")
            text_with_markers = text
    
    _LOGGER.debug("Text with combined '+' markers: %s", text_with_markers)

    # Этап 3: Финальное преобразование маркеров '+' в Unicode-символ ударения.
    # Этот блок нужен всегда для обработки и ручных, и автоматических маркеров.
    stress_pattern = re.compile(re.escape(user_marker) + f"([{_RUSSIAN_VOWELS}])")
    parts = re.split(f'([\\s{re.escape(".,!?-")}]+)', text_with_markers)
    final_unicode_parts = []

    for part in parts:
        if not part or part.isspace() or part in ".,!?-":
            final_unicode_parts.append(part)
            continue

        word = part
        clean_word = word.replace(user_marker, '')

        # Удаляем ударения из слов с одной гласной (контроль качества)
        if _count_vowels(clean_word) <= 1:
            final_unicode_parts.append(clean_word)
        else:
            def stress_replacer(match):
                return match.group(1) + _STRESS_MARK
            
            processed_word = stress_pattern.sub(stress_replacer, word)
            final_unicode_parts.append(processed_word)
    
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
        # self.correction_words больше не нужен, т.к. используется _CORRECTION_WORDS
        self.sbd = SentenceBoundaryDetector()
        self.is_streaming: Optional[bool] = None
        self._synthesize: Optional[Synthesize] = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        try:
            if Synthesize.is_type(event.type):
                if self.is_streaming:
                    return True

                synthesize = Synthesize.from_event(event)
                return await self._handle_synthesize(synthesize)

            if not self.cli_args.streaming:
                return True

            if SynthesizeStart.is_type(event.type):
                stream_start = SynthesizeStart.from_event(event)
                self.is_streaming = True
                self.sbd = SentenceBoundaryDetector()
                self._synthesize = Synthesize(text="", voice=stream_start.voice)
                _LOGGER.debug("Text stream started: voice=%s", stream_start.voice)
                return True

            if SynthesizeChunk.is_type(event.type):
                assert self._synthesize is not None
                stream_chunk = SynthesizeChunk.from_event(event)
                for sentence in self.sbd.add_chunk(stream_chunk.text):
                    _LOGGER.debug("Synthesizing stream sentence: %s", sentence)
                    self._synthesize.text = sentence
                    await self._handle_synthesize(self._synthesize)

                return True

            if SynthesizeStop.is_type(event.type):
                assert self._synthesize is not None
                self._synthesize.text = self.sbd.finish()
                if self._synthesize.text:
                    await self._handle_synthesize(self._synthesize)

                await self.write_event(SynthesizeStopped().event())
                _LOGGER.debug("Text stream stopped")
                return True

            return True

        except Exception as err:
            await self.write_event(
                Error(text=str(err), code=err.__class__.__name__).event()
            )
            _LOGGER.exception("Error handling event")
            return False

    async def _handle_synthesize(self, synthesize: Synthesize) -> bool:
        _LOGGER.debug("Original text from client: %s", synthesize.text)
        
        # Функция теперь использует глобальный _CORRECTION_WORDS
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
            # ... (остальная часть _handle_synthesize без изменений) ...
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

            await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
            
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
                
                await self.write_event(AudioStop().event())
                _LOGGER.debug("Completed request and sent AudioStop.")

        return True