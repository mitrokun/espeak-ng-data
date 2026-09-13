"""
Модуль для преобразования английских слов в русское фонетическое представление.
Активируется, только если установлена библиотека `eng-to-ipa`.
"""
import logging
import re
from typing import Match

import eng_to_ipa as ipa

_LOGGER = logging.getLogger(__name__)

# Предкомпилированные регулярки для пост-обработки русской фонетики
_RE_DOUBLE_Y = re.compile(r'йй')
_RE_SOFT_SIGN = re.compile(r'([чшщждж])ь')
_RE_SLASHES = re.compile(r'[/]')

# Поиск английских слов и сокращений с возможной точкой на конце
_ENG_WORD_PATTERN = re.compile(r"\b(?=[0-9]*[a-zA-Z])[a-zA-Z0-9]+(?:[-'’][a-zA-Z0-9]+)*\b\.?")

# Паттерн для поиска римских веков, которые НЕЛЬЗЯ трогать англофикатором
_CENTURY_GUARD_PATTERN = re.compile(
    r'\b[IVXLCDM]+(?:-[а-яё]{1,3})?'
    r'(?:\s*(?:[-–—]|и|или)\s*[IVXLCDM]+(?:-[а-яё]{1,3})?)?'
    r'\s+век(?:а|у|ом|е|ов|ам|ами|ах)?\b',
    re.IGNORECASE
)


class EnglishNormalizer:
    """
    Инкапсулирует логику для преобразования английских слов
    в русское фонетическое представление.
    """
    ABBREVIATIONS_NO_DOT = {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "inc"}

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
        "docker": "д+окер", "runtime ": "рант+айм", "legacy": "л+егаси", "BDSM": "бэд+ээс+эм",

        "scp": "эссип+и", "cuda": "ку́да", "ibm": "эйбиэ́м", "usb": "юэсби́", "cpp": "сипип+и",
        "chatgpt": "чат джипити́", "gpt": "джипит+и", "copilot": "копа́йлот", "vpn": "вэпэ+эн",
        "intel": "и́нтэл", "android": "андроид", "linux": "линукс", "3d": "трид+э",
        "amd": "айэмди́", "enter": "+энта", "setup": "сет+ап", "mode": "мод",
        "pc": "пис+и", "CINEWS": "си Ньюз", "PR": "пи+ар", "HR": "эйч+ар",
        "USSA": "юэсэс+эй", "omnilink": "омнил+инк", "data": "д+ата", "classiﬁed": "классиф+айд",
        "LLM": "элэл+эм", "RCP": "эрсип+и", "btw": "байзэв+эй", "roblox": "р+облокс",

        "trex": "трэкс", "dddd": "дидидид+и", "psrt": "пиэсэрт+и", "cmsf": "сиэмэс+эф", "grss": "джиарэс+эс",
        "crew": "крю", "shpl": "эсэйчпи+эль", "cctv": "сиситив+и", "aipac": "айп+ак", "kgbt": "кейджибит+и",
        "ussa": "юэсэс+эй", "ussr": "юэсэс+ар", "b2b": "битуб+и", 

        "SEC": "эс-и-с+и", "UN": "ю+эн", "SEAL": "с+ил", "SWAT": "св+ат", 

        # Флот, армия и альянсы
        "uss": "юэс+эс", "hms": "эйчэм+эс", "nato": "н+ато",
        "norad": "н+орад", "darpa": "д+арпа", "usaf": "юэсэй+эф", 
        "usmc": "юэсэмс+и", "idf": "айди+эф",

        # Спецслужбы
        "fbi": "эфби+ай", "cia": "сиай+эй", "nsa": "энэс+эй",
        "dea": "диэ+эй", "atf": "эйти+эф", "dhs": "диэйч+эс",
        "interpol": "интерп+ол", "europol": "европ+ол",
        "mi6": "эм ай сикс", "mi5": "эм ай файв", "mossad": "мосс+ад",

        # Международные организации
        "usaid": "юс+эйд", "unicef": "юнис+еф", "unesco": "юн+еско",
        "unhcr": "юэнэйчси+ар", "opec": "оп+ек", "brics": "брикс",
        "asean": "асе+ан", "nafta": "н+афта", "imf": "айэм+эф", "wef": "вэф",

        # Госагентства
        "cern": "церн", "fda": "эфди+эй", "cdc": "сидис+и", "faa": "эфэй+эй",
        "fema": "фема", "noaa": "н+оа", "usps": "юэспи+эс",

        # Медиа и спорт
        "bbc": "бибис+и", "cnn": "сиэн+эн", "cbs": "сиби+эс",
        "nbc": "энбис+и", "abc": "эйбис+и", "hbo": "эйчби+оу",
        "fifa": "фиф+а", "uefa": "уэф+а", "ufc": "юэфс+и",
        "nba": "энби+эй", "nhl": "энэйч+эл",

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
        "qualia": "кв+алиа", "authentic": "аут+энтик", "aesthetic": "эст+этик",
        "stunning": "ст+аннинг", "nice": "найс", "job": "джоб",
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
        sorted_keys = sorted(self.IPA_TO_RUSSIAN_MAP.keys(), key=len, reverse=True)
        pattern_str = "|".join(re.escape(k) for k in sorted_keys)
        self._ipa_regex = re.compile(f"({pattern_str})")

    def _convert_ipa_to_russian(self, ipa_text: str) -> str:
        """Быстрая замена IPA-символов на русские звуки через скомпилированную регулярку."""
        return self._ipa_regex.sub(lambda m: self.IPA_TO_RUSSIAN_MAP[m.group(1)], ipa_text)

    def _transliterate_word(self, match: Match[str]) -> str:
        raw_match = match.group(0)

        # 1. Отделяем точку, если она есть
        has_dot = raw_match.endswith('.')
        word_original = raw_match[:-1] if has_dot else raw_match

        normalized_word = word_original.replace("’", "'")
        word_lower = normalized_word.lower()

        # 2. Получаем транслитерацию
        if normalized_word in self.ENGLISH_EXCEPTIONS:
            translated = self.ENGLISH_EXCEPTIONS[normalized_word]
        elif word_lower in self.ENGLISH_EXCEPTIONS:
            translated = self.ENGLISH_EXCEPTIONS[word_lower]
        else:
            try:
                ipa_transcription = ipa.convert(word_lower)
                ipa_transcription = _RE_SLASHES.sub('', ipa_transcription).strip()
                if '*' in ipa_transcription:
                    raise ValueError("IPA conversion failed.")

                russian_phonetics = self._convert_ipa_to_russian(ipa_transcription)
                russian_phonetics = _RE_DOUBLE_Y.sub('й', russian_phonetics)
                russian_phonetics = _RE_SOFT_SIGN.sub(r'\1', russian_phonetics)
                _LOGGER.debug(f"Replacement: '{word_lower}' -> '{ipa_transcription}' -> '{russian_phonetics}'")
                translated = russian_phonetics
            except Exception:
                _LOGGER.debug(f"Could not get IPA for '{word_lower}'. Falling back to original word for espeak.")
                translated = word_original

        # 3. Возвращаем точку на место, ЕСЛИ это не сокращение
        if has_dot:
            if word_lower in self.ABBREVIATIONS_NO_DOT:
                return translated
            return translated + "."

        return translated

    def normalize(self, text: str) -> str:
        """Находит в тексте английские слова и заменяет их на русское произношение, игнорируя века."""
        if not text:
            return text

        # Если в тексте есть слово 'век', находим диапазоны, которые нельзя трогать
        century_spans = []
        if 'век' in text.lower():
            century_spans = [m.span() for m in _CENTURY_GUARD_PATTERN.finditer(text)]

        def _replace_guarded(match: Match[str]) -> str:
            m_start, m_end = match.span()
            # Если токен находится внутри фразы с веком (например V или VI в "V—VI веков") — не трогаем
            for c_start, c_end in century_spans:
                if c_start <= m_start < c_end:
                    return match.group(0)
            return self._transliterate_word(match)

        return _ENG_WORD_PATTERN.sub(_replace_guarded, text)