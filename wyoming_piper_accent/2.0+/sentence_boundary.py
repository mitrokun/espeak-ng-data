# sentence_boundary.py (Версия 10.0 - Архитектура "Единый Умный Поиск")
"""
Определяет границы предложений в потоке токенов, используя единое,
надежное регулярное выражение для поиска валидных границ "на лету".
Адаптирует текст для высококачественного синтеза речи (TTS).
"""
from collections.abc import Iterable
import regex as re

# --- КОНФИГУРАЦИЯ ---
HARD_LIMIT = 350
MERGE_BUFFER_LIMIT = 20

# ЕДИНОЕ РЕГУЛЯРНОЕ ВЫРАЖЕНИЕ ДЛЯ ПОИСКА ВАЛИДНОЙ ГРАНИЦЫ
# Оно находит конец предложения, только если он НЕ является ложным.
# (?<!...) - "негативное заглядывание назад"
SENTENCE_BOUNDARY_RE = re.compile(
    r"""
    (?<!\b\p{L}{1,3})              # Не должно быть короткого слова/инициала (г., ул.)
    (?<!\p{Ll}\.\p{Ll})            # НЕ должно быть "буква.буква" (файл.py)
    ([.!?…])                       # ЗАХВАТЫВАЕМ сам знак конца предложения
    (?=\s+\p{Lu}|\s*$)             # После знака должен быть пробел или конец строки
    """,
    re.VERBOSE | re.UNICODE
)

# Регулярные выражения для деликатной очистки
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

    def _process_and_yield(self, sentence_text: str) -> Iterable[str]:
        sentence = post_clean_sentence(sentence_text)
        if not sentence:
            return

        if not self.held_sentence:
            self.held_sentence = sentence
        else:
            joiner = self.held_sentence
            if joiner.endswith('.'):
                joiner = joiner[:-1] + ','
            self.held_sentence = f"{joiner} {sentence}"

        if len(self.held_sentence) >= MERGE_BUFFER_LIMIT:
            yield self.held_sentence
            self.held_sentence = ""

    def add_chunk(self, chunk: str) -> Iterable[str]:
        self.buffer += chunk

        while True:
            match = SENTENCE_BOUNDARY_RE.search(self.buffer)
            if not match:
                if len(self.buffer) > HARD_LIMIT:
                    split_pos = self.buffer.rfind(' ', 0, HARD_LIMIT)
                    if split_pos == -1: split_pos = HARD_LIMIT
                    sentence = self.buffer[:split_pos]
                    yield from self._process_and_yield(sentence)
                    self.buffer = self.buffer[split_pos:]
                    continue
                break

            sentence_end_pos = match.end(1) # Конец захваченной группы (сам разделитель)
            sentence = self.buffer[:sentence_end_pos]
            yield from self._process_and_yield(sentence)
            self.buffer = self.buffer[sentence_end_pos:]

    def finish(self) -> str:
        if self.buffer:
            sentences_from_buffer = list(self._process_and_yield(self.buffer))
            if sentences_from_buffer:
                return " ".join(sentences_from_buffer)
        
        final_text = self.held_sentence
        self.buffer = ""
        self.held_sentence = ""
        return final_text