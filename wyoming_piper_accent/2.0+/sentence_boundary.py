from collections.abc import Iterable
import regex as re

SENTENCE_END = r"[.!?…]|[。！？]|[؟]|[।॥]"
ABBREVIATION_RE = re.compile(r"\b\p{L}{1,3}\.$", re.UNICODE)
# Основное выражение для поиска границ предложений.
# Ищет текст, заканчивающийся на знак конца предложения,
# за которым следует пробел и заглавная буква (или начало/конец текста).
SENTENCE_BOUNDARY_RE = re.compile(
    rf"(.*?(?:{SENTENCE_END}+))(?=\s+[\p{{Lu}}\p{{Lt}}\p{{Lo}}]|\s*[-*(]|\s*\d+\.\s+|\s*$)",
    re.DOTALL,
)

WORD_ASTERISKS = re.compile(r"\*+([^\*]+)\*+")
LINE_ASTERICKS = re.compile(r"(?<=^|\n)\s*\*+")


def pre_clean_for_splitting(chunk: str) -> str:
    """
    "Грубая" очистка чанка от символов, мешающих разделению.
    Применяется ДО поиска границ.
    """
    # Удаляем кавычки
    chunk = chunk.replace('"', '').replace('«', '').replace('»', '')
    # Заменяем тире и дефисы на запятые, чтобы предотвратить неверное разделение
    chunk = re.sub(r'[\s]*[—–-][\s]*', ', ', chunk)
    return chunk


def post_clean_sentence(sentence: str) -> str:
    """
    "Тонкая" очистка уже найденного, целого предложения от форматирования.
    Применяется ПОСЛЕ поиска границ.
    """
    sentence = WORD_ASTERISKS.sub(r"\1", sentence)
    sentence = LINE_ASTERICKS.sub("", sentence)
    return sentence.strip()


class SentenceBoundaryDetector:
    def __init__(self) -> None:
        self.buffer = ""
        self.held_sentence = ""

    def _process_sentence(self, sentence_text: str) -> Iterable[str]:
        """
        Обрабатывает уже найденное предложение: финализирует очистку и решает, вернуть его или удержать.
        """
        sentence = post_clean_sentence(sentence_text)
        if not sentence:
            return

        if self.held_sentence:
            # Если было удержанное предложение, склеиваем его с текущим
            yield f"{self.held_sentence} {sentence}"
            self.held_sentence = ""
        else:
            # Проверяем, не является ли предложение слишком коротким (одно слово)
            if len(sentence.split()) <= 1 and not ABBREVIATION_RE.search(sentence):
                # Если да, удерживаем его
                self.held_sentence = sentence
            else:
                # В противном случае, возвращаем
                yield sentence

    def add_chunk(self, chunk: str) -> Iterable[str]:
        """
        Обрабатывает входящий фрагмент текста, очищает его и возвращает готовые предложения.
        """
        # 1. Применяем предварительную очистку
        cleaned_chunk = pre_clean_for_splitting(chunk)
        self.buffer += cleaned_chunk

        # 2. Ищем границы предложений
        while True:
            match = SENTENCE_BOUNDARY_RE.search(self.buffer)
            if not match:
                break

            sentence_part = match.group(0)
            self.buffer = self.buffer[match.end():]

            # 3. Обрабатываем найденное предложение (с логикой удержания)
            yield from self._process_sentence(sentence_part)

    def finish(self) -> Iterable[str]:
        """
        Завершает обработку, возвращая любой оставшийся текст.
        """
        tail = self.buffer.strip()
        if self.held_sentence:
            # Если есть и удержанное предложение, и остаток в буфере, склеиваем их
            if tail:
                yield from self._process_sentence(f"{self.held_sentence} {tail}")
            # Иначе просто возвращаем удержанное
            else:
                yield from self._process_sentence(self.held_sentence)
        elif tail:
            # Если удержанного нет, но есть остаток в буфере
            yield from self._process_sentence(tail)

        # Сброс состояния
        self.buffer = ""
        self.held_sentence = ""