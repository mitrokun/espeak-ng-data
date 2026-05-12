"""
Модуль для преобразования английских слов в русское фонетическое представление.
Активируется, только если установлена библиотека `eng-to-ipa`.
"""
import logging
import re
from typing import Match

import eng_to_ipa as ipa

_LOGGER = logging.getLogger(__name__)


class EnglishNormalizer:
    """
    Инкапсулирует логику для преобразования английских слов
    в русское фонетическое представление.
    """
    ENGLISH_EXCEPTIONS = {
        # Бренды и имена
        "google": "гугл", "apple": "эпл", "microsoft": "майкрософт", "xiaomi": "сяом+и",
        "samsung": "самсунг", "toyota": "тойота", "volkswagen": "фольцваген",
        "coca": "кока", "cola": "кола", "pepsi": "пэпси", "whatsapp": "вотсап",
        "telegram": "телеграм", "youtube": "ютуб", "instagram": "инстаграм",
        "facebook": "фэйсбук", "twitter": "твиттер", "iphone": "айф+он",
        "tesla": "тесла", "spacex": "спэйс икс", "amazon": "амазон", "camera": "к+амера",
        "python": "пайтон", "AI": "эй+ай", "api": "эйпиай", "glados": "гл+адос",
        "IT": "+ай т+и", "wi-fi": "вай фай", "rtx": "эрте+икс", "nasa": "н+аса",
        "photoshop": "фотош+оп", "SOS": "сос", "pdf": "пэдэ+эф", "raw": "р+оу",

        "scp": "эссипи́", "cuda": "ку́да", "ibm": "эйбиэ́м", "usb": "юэсби́",
        "chatgpt": "чат джипити́", "gpt": "джипити", "copilot": "копа́йлот",
        "intel": "и́нтел", "android": "андроид", "linux": "линукс", "3d": "тридэ́",
        "amd": "айэмди́", "enter": "+энта", "setup": "сет+ап", "mode": "мод",
        "pc": "пис+и",


        # Ё
        "work": "ворк", "world": "ворлд", "bird": "бёрд",
        "girl": "гёрл", "burn": "бёрн", "her": "хёр",
        "early": "ёрли", "service": "сёрвис",
        # Служебные слова
        "a": "э", "the": "зэ", "of": "оф", "and": "энд", "for": "фо",
        "to": "ту", "in": "ин", "on": "он", "is": "из", "or": "ор",
        # Слова, где IPA-библиотека ошибается
        "knowledge": "ноуледж", "new": "нью", "just": "джаст", "error": "+эрор",
        "video": "видео", "ru": "ру", "com": "ком", "done": "дон", "media": "медиа",
        "hot": "хот", "https": "аштитипиэс", "http": "аштитипи", "upper": "аппер",
    }

    IPA_TO_RUSSIAN_MAP = {
        "ˈ": "", "ˌ": "", "ː": "", "p": "п", "b": "б", "t": "т", "d": "д",
        "k": "к", "g": "г", "m": "м", "n": "н", "f": "ф", "v": "в", "s": "с",
        "z": "з", "h": "х", "l": "л", "r": "р", "w": "в", "j": "й", "ʃ": "ш",
        "ʒ": "ж", "tʃ": "ч", "ʧ": "ч", "dʒ": "дж", "ʤ": "дж", "ŋ": "нг",
        "θ": "с", "ð": "з", "i": "и", "ɪ": "и", "ɛ": "э", "æ": "э", "ɑ": "а",
        "ɔ": "о", "u": "у", "ʊ": "у", "ʌ": "а", "ə": "э", "ər": "эр", "ɚ": "эр",
        "eɪ": "эй", "aɪ": "ай", "ɔɪ": "ой", "aʊ": "ау", "oʊ": "оу", "ɪə": "иэ",
        "eə": "еэ", "ʊə": "уэ",
    }

    def __init__(self):
        self._max_ipa_key_len = max(len(key) for key in self.IPA_TO_RUSSIAN_MAP.keys())

    def _convert_ipa_to_russian(self, ipa_text: str) -> str:
        result, pos = "", 0
        while pos < len(ipa_text):
            found = False
            for length in range(self._max_ipa_key_len, 0, -1):
                chunk = ipa_text[pos:pos + length]
                if chunk in self.IPA_TO_RUSSIAN_MAP:
                    result += self.IPA_TO_RUSSIAN_MAP[chunk]
                    pos += length
                    found = True
                    break
            if not found:
                pos += 1
        return result

    def _transliterate_word(self, match: Match[str]) -> str:
        word_original = match.group(0)

        normalized_word = word_original.replace("’", "'")

        # Далее используем только `normalized_word`
        if normalized_word in self.ENGLISH_EXCEPTIONS:
            return self.ENGLISH_EXCEPTIONS[normalized_word]

        word_lower = normalized_word.lower()
        if word_lower in self.ENGLISH_EXCEPTIONS:
            return self.ENGLISH_EXCEPTIONS[word_lower]

        try:
            # Передаём в библиотеку уже нормализованное слово
            ipa_transcription = ipa.convert(word_lower)
            ipa_transcription = re.sub(r'[/]', '', ipa_transcription).strip()
            if '*' in ipa_transcription:
                raise ValueError("IPA conversion failed.")

            russian_phonetics = self._convert_ipa_to_russian(ipa_transcription)
            russian_phonetics = re.sub(r'йй', 'й', russian_phonetics)
            russian_phonetics = re.sub(r'([чшщждж])ь', r'\1', russian_phonetics)
            _LOGGER.debug(f"Phonetic replacement: '{word_lower}' -> '{ipa_transcription}' -> '{russian_phonetics}'")
            return russian_phonetics
        except Exception:
            _LOGGER.debug(f"Could not get IPA for '{word_lower}'. Falling back to original word for espeak.")
            return word_original

    def normalize(self, text: str) -> str:
        """Находит в тексте английские слова, включая сокращения, и заменяет их на русское произношение."""
        return re.sub(r"\b[a-zA-Z]+(?:[-'’][a-zA-Z]+)*\b", self._transliterate_word, text)