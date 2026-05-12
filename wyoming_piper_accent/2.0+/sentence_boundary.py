"""
Определяет границы предложений в потоке токенов.
Все регулярные выражения предкомпилированы.
Списки используют длинное тире для глубокой паузы в TTS.
"""
import regex as re
from collections.abc import Iterable

# --- КОНФИГУРАЦИЯ ---
HARD_LIMIT = 350

# Удобный Python Set для сокращений
ABBR_SET = {
    "г", "ул", "обл", "пер", "пр", "просп", "наб", "бул", "стр", "корп", "кв", "пос", "сел", "д",
    "акад", "проф", "доц", "канд", "тов", "гр", "ген", "лейт", "кап", "зам", "зав", "дир", "ред", "св", "дж",
    "см", "ср", "напр", "вып", "табл", "рис", "ил", "цит", "гл", "ст", "изд", "техн", "им", "руб", "коп", "мес"
}

# Сортируем от длинных к коротким для максимальной скорости регулярных выражений
ABBR_FOR_INTONATION = "|".join(sorted(ABBR_SET, key=len, reverse=True))

# 1. ПРЕДКОМПИЛИРУЕМ ГЛАВНЫЕ РАЗДЕЛИТЕЛИ
SENTENCE_BOUNDARY_RE = re.compile(
    rf"""
    (?<!\b(?i:{ABBR_FOR_INTONATION}))  # Защита сокращений
    (?<!\b\p{{Lu}})                    # Защита одиночных инициалов
    (?<!\p{{Ll}}\.\p{{Ll}})            # Защита т.д. и т.п.
    (?<!(?:^|\n)[ \t]*\d+)             # ЗАЩИТА СПИСКОВ: не рвать после цифры в начале строки
    ([.!?])                            # САМ ЗНАК
    (?=
        \s+                        
        (?:[-—–]\s*)?                  # Разрешаем дефис/тире в начале следующего
        [("«\[]*                       # Разрешаем открывающие скобки и кавычки
        [\p{{Lu}}\d]                   # ОБЯЗАТЕЛЬНО Заглавная буква или Цифра!
    )
    """,
    re.VERBOSE | re.UNICODE
)

# Разделяем виды пауз на 2 группы: 
# Group 1: (\n\s*\n) -> полноценный абзац
# Group 2: (\n[ \t]*(?=[-—–])) -> смена спикера (тире не съедается за счет lookahead)
BREAK_RE = re.compile(r'(\n\s*\n)|(\n[ \t]*(?=[-—–]))')

FALLBACK_SPLIT_RE = re.compile(r'(.*\s)', re.DOTALL)

# 2. ПРЕДКОМПИЛИРУЕМ ПАТТЕРНЫ ДЛЯ ОЧИСТКИ ТЕКСТА
PARENS_RE = re.compile(r"\s*\((.*?)\)")
LIST_ITEM_RE = re.compile(r"^\s*(?:(\d+)\.|([*-]))\s*(.*)", re.MULTILINE)
LEADING_PUNCT_RE = re.compile(r"^[.,\s]+")
MULTI_SPACE_RE = re.compile(r"\s+")
DOUBLE_PUNCT_RE = re.compile(r"\s*([,.]\s*){2,}")

# Сверхбыстрая таблица удаления мусорных символов
REMOVE_CHARS = str.maketrans("", "", "*«»\"„“")


def _list_replacer(match) -> str:
    """Обработчик для нумерованных списков (добавляет тире вместо точки)."""
    num, bullet, text = match.groups()
    return f"{num} — {text}" if num else text


def post_clean_sentence(sentence: str) -> str:
    """Применяет финальные правила форматирования."""
    sentence = LIST_ITEM_RE.sub(_list_replacer, sentence)
    sentence = sentence.replace('…', ' —').replace('\n', ' ').replace(';', ' —')
    sentence = sentence.translate(REMOVE_CHARS)
    sentence = PARENS_RE.sub(r", \1, ", sentence)
    sentence = LEADING_PUNCT_RE.sub("", sentence)
    sentence = MULTI_SPACE_RE.sub(" ", sentence)
    sentence = DOUBLE_PUNCT_RE.sub(r"\1 ", sentence).strip()
    return sentence


class SentenceBoundaryDetector:
    def __init__(self, emit_break_markers: bool = False) -> None:
        self.buffer = ""
        self.emit_break_markers = emit_break_markers

    def add_chunk(self, chunk: str) -> Iterable[str]:
        self.buffer += chunk

        while True:
            match_break = BREAK_RE.search(self.buffer)
            match_punc = SENTENCE_BOUNDARY_RE.search(self.buffer)
            
            if match_break and (not match_punc or match_break.start() <= match_punc.start()):
                sentence = self.buffer[:match_break.start()].strip()
                if sentence:
                    yield post_clean_sentence(sentence)
                
                if self.emit_break_markers:
                    # Если сработала первая группа — это классический абзац
                    if match_break.group(1):
                        yield "<PARAGRAPH_BREAK>"
                    # Иначе — это перенос перед репликой диалога
                    else:
                        yield "<DIALOGUE_BREAK>"
                    
                self.buffer = self.buffer[match_break.end():]
                continue

            if not match_punc:
                if len(self.buffer) > HARD_LIMIT:
                    match = FALLBACK_SPLIT_RE.search(self.buffer[:HARD_LIMIT])
                    split_pos = match.end() - 1 if match else HARD_LIMIT
                    
                    if split_pos <= 0:
                        split_pos = HARD_LIMIT

                    sentence = self.buffer[:split_pos].strip()
                    if sentence:
                        yield post_clean_sentence(sentence)
                    
                    self.buffer = self.buffer[split_pos:].lstrip()
                    continue

                break 

            sep_char = match_punc.group(1)
            sep_end_pos = match_punc.end(1)
            sep_start_pos = match_punc.start(1)

            # Защита от разделения десятичных дробей
            if (sep_char == '.' and 
                sep_end_pos == len(self.buffer) and 
                sep_start_pos > 0 and 
                self.buffer[sep_start_pos - 1].isdigit()):
                break

            sentence_end_pos = match_punc.end(1)
            sentence = self.buffer[:sentence_end_pos].strip()
            
            if sentence:
                yield post_clean_sentence(sentence)
            
            self.buffer = self.buffer[sentence_end_pos:]

    def finish(self) -> str:
        res = post_clean_sentence(self.buffer)
        self.buffer = ""
        return res