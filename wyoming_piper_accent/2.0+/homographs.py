"""Загрузчик словаря омографов для коррекции ударений."""

import logging
from pathlib import Path
from typing import Optional, Set

_LOGGER = logging.getLogger(__name__)
_CORRECTION_WORDS: Optional[Set[str]] = None


def get_correction_words() -> Set[str]:
    """Возвращает словарь омографов, загружая его с диска только при первом вызове."""
    global _CORRECTION_WORDS
    if _CORRECTION_WORDS is not None:
        return _CORRECTION_WORDS

    dict_path = Path(__file__).parent / "homographs.txt"
    if not dict_path.exists():
        _LOGGER.warning(f"Файл омографов не найден: {dict_path}")
        _CORRECTION_WORDS = set()
        return _CORRECTION_WORDS

    words = set()
    with open(dict_path, "r", encoding="utf-8") as f:
        for line in f:
            word = line.split("#")[0].strip().lower()
            if word:
                words.add(word)

    _LOGGER.info(f"Словарь омографов загружен: {len(words)} слов.")
    _CORRECTION_WORDS = words
    return _CORRECTION_WORDS