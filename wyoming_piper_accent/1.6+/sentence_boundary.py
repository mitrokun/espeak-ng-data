"""Guess the sentence boundaries in text."""

from collections.abc import Iterable

import regex as re

SENTENCE_END = r"[.!?…]|[。！？]|[؟]|[।॥]"
ABBREVIATION_RE = re.compile(r"\b\p{L}{1,3}\.$", re.UNICODE)

SENTENCE_BOUNDARY_RE = re.compile(
    rf"(.*?(?:{SENTENCE_END}+))(?=\s+[\p{{Lu}}\p{{Lt}}\p{{Lo}}]|(?:\s+\d+\.\s+))",
    re.DOTALL,
)
WORD_ASTERISKS = re.compile(r"\*+([^\*]+)\*+")
LINE_ASTERICKS = re.compile(r"(?<=^|\n)\s*\*+")


class SentenceBoundaryDetector:
    def __init__(self) -> None:
        self.remaining_text = ""
        self.current_sentence = ""
        self.held_sentence = ""

    def _process_sentence(self, sentence_text: str) -> Iterable[str]:
        """
        Обрабатывает кандидатное предложение.
        Удерживает его, если оно однословное, или объединяет с ранее удержанным.
        """
        sentence = clean_text(sentence_text.strip())
        if not sentence:
            return

        if self.held_sentence:
            yield f"{self.held_sentence} {sentence}"
            self.held_sentence = ""
        else:
            if len(sentence.split()) == 1:
                self.held_sentence = sentence
            else:
                yield sentence

    def add_chunk(self, chunk: str) -> Iterable[str]:
        """Обрабатывает входящий фрагмент текста и возвращает готовые предложения."""
        self.remaining_text += chunk
        while self.remaining_text:
            match = SENTENCE_BOUNDARY_RE.search(self.remaining_text)
            if not match:
                break

            match_text = match.group(0)

            if not self.current_sentence:
                self.current_sentence = match_text
            elif ABBREVIATION_RE.search(self.current_sentence[-5:]):
                self.current_sentence += match_text
            else:
                yield from self._process_sentence(self.current_sentence)
                self.current_sentence = match_text

            if not ABBREVIATION_RE.search(self.current_sentence[-5:]):
                yield from self._process_sentence(self.current_sentence)
                self.current_sentence = ""

            self.remaining_text = self.remaining_text[match.end() :]

    def finish(self) -> str:
        """
        Завершает обработку, возвращая любой оставшийся текст.
        Обрабатывает удержанное предложение в конце потока.
        """
        tail = (self.current_sentence + self.remaining_text).strip()

        if self.held_sentence:
            if tail:
                text = f"{self.held_sentence} {tail}"
            else:
                text = self.held_sentence
        else:
            text = tail

        self.remaining_text = ""
        self.current_sentence = ""
        self.held_sentence = ""

        return clean_text(text)


def clean_text(text: str) -> str:
    """
    Cleans up text by removing asterisks and replacing em dashes.
    """
    text = text.replace("—", ", ")
    text = WORD_ASTERISKS.sub(r"\1", text)
    text = LINE_ASTERICKS.sub("", text)
    return text