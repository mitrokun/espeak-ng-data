# sentence_boundary.py (Версия 10.3 - Clean & Stable)
"""
Определяет границы предложений в потоке токенов.
Содержит защиту от разрыва дробных чисел (0.5) при потоковой передаче.
"""
from collections.abc import Iterable
import regex as re

# --- КОНФИГУРАЦИЯ ---
HARD_LIMIT = 350
MERGE_BUFFER_LIMIT = 20

# ЕДИНОЕ РЕГУЛЯРНОЕ ВЫРАЖЕНИЕ
SENTENCE_BOUNDARY_RE = re.compile(
    r"""
    (?<!\b\p{L}{1,3})              # Не должно быть короткого слова/инициала (г., ул.)
    (?<!\p{Ll}\.\p{Ll})            # НЕ должно быть "буква.буква" (файл.py)
    ([.!?…])                       # ЗАХВАТЫВАЕМ сам знак конца предложения
    (?=\s+\p{Lu}|\s*$)             # После знака должен быть пробел+Заглавная или конец строки
    """,
    re.VERBOSE | re.UNICODE
)

LIST_ITEM_RE = re.compile(r"^\s*(?:(\d+)\.|([*-]))\s*(.*)", re.MULTILINE)


def post_clean_sentence(sentence: str) -> str:
    """Применяет финальные, деликатные правила форматирования."""
    
    sentence = re.sub(r"\s*\((.*?)\)", r", \1, ", sentence)

    def list_replacer(match):
        num, bullet, text = match.groups()
        if num:
            return f"{num}, {text}"
        return text

    sentence = LIST_ITEM_RE.sub(list_replacer, sentence)
    sentence = sentence.replace('\n', ' ').replace(';', '.')
    sentence = re.sub(r"\b([\p{IsCyrillic}]{1,3})\.\s+(?=\p{Lu})", r"\1, ", sentence)
    sentence = re.sub(r"^[.,\s]+", "", sentence)
    sentence = re.sub(r"[\*«»\"]", "", sentence)
    sentence = re.sub(r"\s*—\s*", ", ", sentence)
    sentence = re.sub(r"\s+", " ", sentence)
    # Исправляем двойные запятые и другие артефакты
    sentence = re.sub(r"\s*([,.]\s*){2,}", r"\1 ", sentence).strip()
    return sentence


class SentenceBoundaryDetector:
    def __init__(self) -> None:
        self.buffer = ""
        self.held_sentence = ""

    def _maybe_yield(self, text: str) -> Iterable[str]:
        """
        Внутренняя логика накопления. 
        Если накопили достаточно -> yield, иначе сохраняем в held_sentence.
        """
        cleaned = post_clean_sentence(text)
        if not cleaned:
            return

        if not self.held_sentence:
            self.held_sentence = cleaned
        else:
            joiner = self.held_sentence
            if joiner.endswith('.'):
                joiner = joiner[:-1] + ','
            self.held_sentence = f"{joiner} {cleaned}"

        if len(self.held_sentence) >= MERGE_BUFFER_LIMIT:
            yield self.held_sentence
            self.held_sentence = ""

    def add_chunk(self, chunk: str) -> Iterable[str]:
        self.buffer += chunk

        while True:
            match = SENTENCE_BOUNDARY_RE.search(self.buffer)
            
            # Если разделитель не найден
            if not match:
                # Проверка на переполнение буфера
                if len(self.buffer) > HARD_LIMIT:
                    split_pos = self.buffer.rfind(' ', 0, HARD_LIMIT)
                    if split_pos == -1: 
                        split_pos = HARD_LIMIT
                    
                    sentence = self.buffer[:split_pos]
                    yield from self._maybe_yield(sentence)
                    
                    self.buffer = self.buffer[split_pos:]
                    continue # Продолжаем искать в остатке
                break # Ждем новых данных

            # --- ЗАЩИТА ОТ ДРОБНЫХ ЧИСЕЛ ---
            sep_char = match.group(1)
            sep_end_pos = match.end(1)
            sep_start_pos = match.start(1)

            # Если точка в самом конце буфера И перед ней цифра
            if (sep_char == '.' and 
                sep_end_pos == len(self.buffer) and 
                sep_start_pos > 0 and 
                self.buffer[sep_start_pos - 1].isdigit()):
                
                # Прерываем обработку, ждем следующий чанк
                break
            # -------------------------------

            # Стандартная обработка найденного предложения
            sentence_end_pos = match.end(1)
            sentence = self.buffer[:sentence_end_pos]
            yield from self._maybe_yield(sentence)
            
            self.buffer = self.buffer[sentence_end_pos:]

    def finish(self) -> str:
        """
        Завершает обработку. Возвращает остаток как строку.
        """
        # 1. Если что-то осталось в буфере (например "Windows 10.")
        if self.buffer:
            # Просто добавляем это в накопление, не пытаясь yield-ить
            cleaned = post_clean_sentence(self.buffer)
            if cleaned:
                if not self.held_sentence:
                    self.held_sentence = cleaned
                else:
                    joiner = self.held_sentence
                    if joiner.endswith('.'):
                        joiner = joiner[:-1] + ','
                    self.held_sentence = f"{joiner} {cleaned}"
            self.buffer = ""

        # 2. Возвращаем всё, что накопилось
        final_text = self.held_sentence
        self.held_sentence = ""
        return final_text