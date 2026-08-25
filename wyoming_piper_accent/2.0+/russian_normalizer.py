import re
import logging
from pathlib import Path

from .sentence_boundary import ABBR_FOR_INTONATION
from .vse_yoficator import VseYoficator

_LOGGER = logging.getLogger(__name__)

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False

# Предкомпилированные регулярки для ускорения
_WORDS_FIND_PATTERN = re.compile(r'[а-яА-ЯёЁ]+')
_WORD_REPLACE_PATTERN = re.compile(r'[а-яА-ЯёЁ-]+')


class VsemYoficator:
    """Модуль ёфикации слова 'всем' -> 'всём' в предложном падеже."""

    def __init__(self):
        self.RE_HAS_VSEM = re.compile(r'\bвсем\b', re.IGNORECASE)
        self.RE_VSEM_YO = re.compile(
            r'\b(в|во|о|об|обо|на|при)\s+(?:(этом|том|своем|своём)\s+)?([Вв])сем\b',
            re.IGNORECASE
        )

    def process(self, text: str) -> str:
        if not self.RE_HAS_VSEM.search(text):
            return text

        def _replace(m: re.Match) -> str:
            prep = m.group(1)
            demo = m.group(2)
            v_char = m.group(3)
            
            yo_word = 'Всём' if v_char[0].isupper() else 'всём'

            if demo:
                return f"{prep} {demo} {yo_word}"
            return f"{prep} {yo_word}"

        return self.RE_VSEM_YO.sub(_replace, text)


class RussianNormalizer:

    _SHARED_YO_MAP = None  
    _SHARED_STRESS_MAP = None
    _SHARED_CAPITALIZED_STRESS_MAP = None
    _SHARED_ALL_DICT_KEYS = None

    _INITIALS_MAP = {
        'А': 'А\u0301', 'Б': 'Бэ', 'В': 'Вэ', 'Г': 'Гэ', 'Д': 'Дэ',
        'Е': 'Е\u0301', 'Ё': 'Ё', 'Ж': 'Же', 'З': 'Зэ', 'И': 'И\u0301',
        'Й': 'Йот', 'К': 'Ка', 'Л': 'Эль', 'М': 'Эм', 'Н': 'Эн',
        'О': 'О\u0301', 'П': 'Пэ', 'Р': 'Эр', 'С': 'Эс', 'Т': 'Тэ',
        'У': 'У\u0301', 'Ф': 'Эф', 'Х': 'Ха', 'Ц': 'Цэ', 'Ч': 'Че',
        'Ш': 'Шэ', 'Щ': 'Ща', 'Э': 'Э\u0301', 'Ю': 'Ю\u0301', 'Я': 'Я\u0301',
    }

    DAY_NOMINATIVE_MAP = {
        1: "первое", 2: "второе", 3: "третье", 4: "четвёртое", 5: "пятое",
        6: "шестое", 7: "седьмое", 8: "восьмое", 9: "девятое", 10: "десятое",
        11: "одиннадцатое", 12: "двенадцатое", 13: "тринадцатое", 14: "четырнадцатое", 15: "пятнадцатое",
        16: "шестнадцатое", 17: "семнадцатое", 18: "восемнадцатое", 19: "девятнадцатое", 20: "двадцатое",
        21: "двадцать первое", 22: "двадцать второе", 23: "двадцать третье", 24: "двадцать четвёртое", 25: "двадцать пятое",
        26: "двадцать шестое", 27: "двадцать седьмое", 28: "двадцать восьмое", 29: "двадцать девятое", 30: "тридцатое",
        31: "тридцать первое"
    }

    DAY_GENITIVE_MAP = {
        1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого",
        6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого", 10: "десятого",
        11: "одиннадцатого", 12: "двенадцатого", 13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого",
        16: "шестнадцатого", 17: "семнадцатого", 18: "восемнадцатого", 19: "девятнадцатого", 20: "двадцатого",
        21: "двадцать первого", 22: "двадцать второго", 23: "двадцать третьего", 24: "двадцать четвёртого", 25: "двадцать пятого",
        26: "двадцать шестого", 27: "двадцать седьмого", 28: "двадцать восьмого", 29: "двадцать девятого", 30: "тридцатого",
        31: "тридцать первого"
    }

    DAY_DATIVE_MAP = {
        1: "первому", 2: "второму", 3: "третьему", 4: "четвёртому", 5: "пятому",
        6: "шестому", 7: "седьмому", 8: "восьмому", 9: "девятому", 10: "десятому",
        11: "одиннадцатому", 12: "двенадцатому", 13: "тринадцатому", 14: "четырнадцатому", 15: "пятнадцатому",
        16: "шестнадцатому", 17: "семнадцатому", 18: "восемнадцатому", 19: "девятнадцатому", 20: "двадцатому",
        21: "двадцать первому", 22: "двадцать второму", 23: "двадцать третьему", 24: "двадцать четвёртому", 25: "двадцать пятому",
        26: "двадцать шестому", 27: "двадцать седьмому", 28: "двадцать восьмому", 29: "двадцать девятому", 30: "тридцатому",
        31: "тридцать первому"
    }

    def __init__(self, use_yo: bool = False):
        self.use_yo = use_yo
        self.yo_map = {}
        self.stress_map = {}
        self.capitalized_stress_map = {}

        if self.use_yo:
            self.vse_yoficator = VseYoficator()
            self.vsem_yoficator = VsemYoficator()
        else:
            self.vse_yoficator = None
            self.vsem_yoficator = None

        self.compound_prefixes = {
            'зелено': 'зелёно',
            'черно': 'чёрно',
            'темно': 'тёмно',
            'пестро': 'пёстро',
            'светло': 'све́тло',
        }
        
        months_genitive = r'января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря'
        self.date_pattern = re.compile(
            rf'(?:\b(?P<prep>с|со|от|до|после|около|к|ко|по|на|в|во)\s+)?'
            rf'\b(?P<day>3[01]|[12]\d|[1-9])'
            rf'(?:-?[а-яеё]{{1,3}})?\s+'
            rf'(?P<month>{months_genitive})\b',
            re.IGNORECASE
        )

        self.year_pattern = re.compile(
            r'\b(?P<num>\d{1,4})'
            r'(?P<suffix>-[а-яё]{1,3})?'
            r'\s+'
            r'(?P<god>год[а-яё]{0,3})\b',
            re.IGNORECASE
        )
        
        self.abbr_clean_pattern = re.compile(
            rf'\b({ABBR_FOR_INTONATION})\.(?=\s)', 
            re.IGNORECASE
        )
        self.re_has_digits = re.compile(r'\d')
        self.re_has_adverb = re.compile(r'\bпо-', re.IGNORECASE)
        self.adverb_pattern = re.compile(r'\bпо-(моему|твоему|своему)\b', re.IGNORECASE)

        if not NUM2WORDS_AVAILABLE:
            _LOGGER.warning("Библиотека `num2words` не найдена. Преобразование чисел в текст недоступно.")

        if self.use_yo:
            if RussianNormalizer._SHARED_YO_MAP is None:
                self._load_yo_dictionary()
                RussianNormalizer._SHARED_YO_MAP = self.yo_map
            else:
                self.yo_map = RussianNormalizer._SHARED_YO_MAP

        if RussianNormalizer._SHARED_STRESS_MAP is None:
            self._load_stress_dictionary()
            RussianNormalizer._SHARED_STRESS_MAP = self.stress_map
            RussianNormalizer._SHARED_CAPITALIZED_STRESS_MAP = self.capitalized_stress_map
        else:
            self.stress_map = RussianNormalizer._SHARED_STRESS_MAP
            self.capitalized_stress_map = RussianNormalizer._SHARED_CAPITALIZED_STRESS_MAP

        # Собираем общий кэш ключей
        if RussianNormalizer._SHARED_ALL_DICT_KEYS is None:
            RussianNormalizer._SHARED_ALL_DICT_KEYS = (
                set(self.stress_map.keys())
                | set(self.capitalized_stress_map.keys())
                | set(self.yo_map.keys())
                | set(self.compound_prefixes.keys())
            )

        self._all_dict_keys = RussianNormalizer._SHARED_ALL_DICT_KEYS

    def _load_yo_dictionary(self):
        try:
            dict_path = Path(__file__).parent / "yo.txt"
            if not dict_path.exists():
                return

            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    word_yo = line.strip().lower()
                    if not word_yo: continue
                    word_e = word_yo.replace('ё', 'е')
                    if word_e != word_yo:
                        self.yo_map[word_e] = word_yo
            _LOGGER.info(f"Словарь ёфикации загружен: {len(self.yo_map)} слов.")
        except Exception as e:
            _LOGGER.error(f"Ошибка загрузки словаря ё: {e}")

    def _load_stress_dictionary(self):
        try:
            dict_path = Path(__file__).parent / "user.txt"
            if not dict_path.exists():
                return

            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.split('#')[0].strip()
                    if not line:
                        continue
                    
                    if '+' in line or 'ё' in line.lower():
                        word_clean = line.replace('+', '')
                        is_capitalized = word_clean[0].isupper()
                        low_val = line.lower()
                        
                        low_key_yo = word_clean.lower()
                        low_key_e = low_key_yo.replace('ё', 'е')
                        
                        keys = {low_key_yo, low_key_e}
                        
                        for key in keys:
                            if is_capitalized:
                                self.capitalized_stress_map[key] = low_val
                            else:
                                self.stress_map[key] = low_val
            
            _LOGGER.info(f"Словарь ударений: {len(self.stress_map)} обычных, {len(self.capitalized_stress_map)} имен собственных.")
        except Exception as e:
            _LOGGER.error(f"Ошибка загрузки словаря ударений: {e}")

    def _apply_fix_match(self, match: re.Match) -> str:
        word = match.group(0)
        if not word: return word
        
        low_word = word.lower()
        is_capitalized = word[0].isupper()

        if is_capitalized and low_word in self.capitalized_stress_map:
            return self._restore_case(word, self.capitalized_stress_map[low_word])
        if low_word in self.stress_map:
            return self._restore_case(word, self.stress_map[low_word])
        if self.use_yo and low_word in self.yo_map:
            return self._restore_case(word, self.yo_map[low_word])

        if '-' in word:
            parts_orig = word.split('-')
            new_parts = []
            changed = False
            
            for i, p_orig in enumerate(parts_orig):
                if not p_orig:
                    new_parts.append(p_orig)
                    continue
                    
                p_low = p_orig.lower()
                is_last = (i == len(parts_orig) - 1)
                
                if not is_last and p_low in self.compound_prefixes:
                    new_parts.append(self._restore_case(p_orig, self.compound_prefixes[p_low]))
                    changed = True
                    continue

                p_is_cap = p_orig[0].isupper()
                if p_is_cap and p_low in self.capitalized_stress_map:
                    new_parts.append(self._restore_case(p_orig, self.capitalized_stress_map[p_low]))
                    changed = True
                elif p_low in self.stress_map:
                    new_parts.append(self._restore_case(p_orig, self.stress_map[p_low]))
                    changed = True
                elif self.use_yo and p_low in self.yo_map:
                    new_parts.append(self._restore_case(p_orig, self.yo_map[p_low]))
                    changed = True
                else:
                    new_parts.append(p_orig)
            
            if changed:
                return '-'.join(new_parts)
                
        return word

    def _restore_case(self, original: str, replacement: str) -> str:
        if original.isupper():
            return replacement.upper()
        if original[0].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement

    def _replace_plus_sign(self, text: str) -> str:
        text = re.sub(r'\+(?![аеёиоуыэюяАЕЁИОУЫЭЮЯ])', ' плюс ', text)
        text = re.sub(r' +', ' ', text)
        return text

    def _get_noun_form(self, n: int, forms: list) -> str:
        if 10 < n % 100 < 20: return forms[2]
        last = n % 10
        if last == 1: return forms[0]
        if 2 <= last <= 4: return forms[1]
        return forms[2]

    def _float_to_text(self, num_str: str, for_percent: bool = False) -> str:
        if not NUM2WORDS_AVAILABLE:
            return num_str.replace('.', ' и ').replace(',', ' и ')
        clean_num = num_str.replace(',', '.')
        try:
            parts = clean_num.split('.')
            if len(parts) != 2: return num_str
            int_part = int(parts[0])
            frac_part = int(parts[1])
            frac_len = len(parts[1])
            int_text = num2words(int_part, lang='ru')
            
            if frac_len == 1:
                frac_text = num2words(frac_part, lang='ru')
                return f"{int_text} и {frac_text}"

            frac_text = num2words(frac_part, lang='ru')
            last_two = frac_part % 100
            last_digit = frac_part % 10
            if last_digit == 1 and last_two != 11:
                frac_text = re.sub(r'\bодин$', 'одна', frac_text)
            elif last_digit == 2 and last_two != 12:
                frac_text = re.sub(r'\bдва$', 'две', frac_text)

            suffix = ""
            if frac_len == 2:
                suffix = " " if (last_digit == 1 and last_two != 11) else " "
            elif frac_len == 3:
                suffix = " тысячная" if (last_digit == 1 and last_two != 11) else " тысячных"
            else:
                return f"{int_text} точка {frac_text}"
            return f"{int_text} и {frac_text}{suffix}"
        except Exception:
            return num_str

    def _replace_dates(self, match: re.Match) -> str:
        prep = match.group('prep')
        day_num = int(match.group('day'))
        month_word = match.group('month')
        full_str = match.group(0)

        form = 'genitive'
        
        if prep:
            p_low = prep.lower()
            if p_low in {'к', 'ко'}:
                form = 'dative'
            elif p_low in {'по', 'на', 'в', 'во'}:
                form = 'nominative'

        if '-е' in full_str.lower() or '-ое' in full_str.lower():
            form = 'nominative'
        elif '-му' in full_str.lower() or '-ому' in full_str.lower() or '-ему' in full_str.lower():
            form = 'dative'

        if form == 'nominative':
            day_text = self.DAY_NOMINATIVE_MAP.get(day_num, str(day_num))
        elif form == 'dative':
            day_text = self.DAY_DATIVE_MAP.get(day_num, str(day_num))
        else:
            day_text = self.DAY_GENITIVE_MAP.get(day_num, str(day_num))

        first_char = full_str[0]
        if prep:
            if first_char.isupper():
                prep = prep[0].upper() + prep[1:]
            return f"{prep} {day_text} {month_word}"
        else:
            if first_char.isupper():
                day_text = day_text[0].upper() + day_text[1:]
            return f"{day_text} {month_word}"

    def _replace_percentages(self, match: re.Match) -> str:
        num_str = match.group(1).replace(',', '.')
        if '.' in num_str:
            parts = num_str.split('.')
            frac_part_str = parts[1]
            if len(frac_part_str) == 1:
                text_num = self._float_to_text(num_str, for_percent=True)
                frac_val = int(frac_part_str)
                word = self._get_noun_form(frac_val, ['процент', 'процента', 'процентов'])
                return f"{text_num} {word}"
            else:
                text_num = self._float_to_text(num_str)
                return f"{text_num} процента"
        word = self._get_noun_form(int(num_str), ['процент', 'процента', 'процентов'])
        return f"{num_str} {word}"

    def _replace_floats(self, match: re.Match) -> str:
        return self._float_to_text(match.group(0))

    def _replace_years(self, match: re.Match) -> str:
        if not NUM2WORDS_AVAILABLE:
            return match.group(0)

        num_str = match.group('num')
        suffix = match.group('suffix')
        god_word_raw = match.group('god')
        god_word = god_word_raw.lower()

        try:
            num_val = int(num_str)
        except ValueError:
            return match.group(0)

        always_ordinal_words = {'годы', 'годов', 'годам', 'годами', 'годах', 'году', 'годом'}

        is_ordinal = (
            (len(num_str) >= 3) or 
            (suffix is not None) or 
            (god_word in always_ordinal_words)
        )

        if not is_ordinal:
            try:
                cardinal_text = num2words(num_val, lang='ru')
                if match.group(0)[0].isupper():
                    cardinal_text = cardinal_text[0].upper() + cardinal_text[1:]
                return f"{cardinal_text} {god_word_raw}"
            except Exception:
                return match.group(0)

        try:
            ordinal_text = num2words(num_val, to='ordinal', lang='ru')
        except Exception:
            return match.group(0)

        suffix_map = {
            'год':    {'ый': 'ый',  'ой': 'ой',  'ий': 'ий'},
            'года':   {'ый': 'ого', 'ой': 'ого', 'ий': 'ьего'},
            'году':   {'ый': 'ом',  'ой': 'ом',  'ий': 'ьем'},
            'годом':  {'ый': 'ым',  'ой': 'ым',  'ий': 'ьим'},
            'годы':   {'ый': 'ые',  'ой': 'ые',  'ий': 'ьи'},
            'годов':  {'ый': 'ых',  'ой': 'ых',  'ий': 'ьих'},
            'годам':  {'ый': 'ым',  'ой': 'ым',  'ий': 'ьим'},
            'годами': {'ый': 'ыми', 'ой': 'ыми', 'ий': 'ьими'},
            'годах':  {'ый': 'ых',  'ой': 'ых',  'ий': 'ьих'},
        }

        target_rules = suffix_map.get(god_word, suffix_map['год'])

        words = ordinal_text.split()
        last_word = words[-1]

        for base_end, new_end in target_rules.items():
            if last_word.endswith(base_end):
                last_word = last_word[:-len(base_end)] + new_end
                break
        
        words[-1] = last_word
        normalized_num = " ".join(words)

        if match.group(0)[0].isupper():
            normalized_num = normalized_num[0].upper() + normalized_num[1:]

        return f"{normalized_num} {god_word_raw}"

    def _replace_initials(self, text: str) -> str:
        pattern = re.compile(r'\b([А-ЯЁ])\.(\s*)')
        
        def replace_callback(m: re.Match) -> str:
            letter = m.group(1)
            spaces = m.group(2)
            
            replacement = self._INITIALS_MAP.get(letter, letter)
            
            full_str = m.string
            end_idx = m.end()
            remaining = full_str[end_idx:].strip()
            
            if re.match(r'^[А-ЯЁ]\.', remaining):
                return replacement + " "
                
            if not remaining or re.match(r'^[)\s\]}»"”’\-—–.!?;:]+$', remaining):
                return replacement + "." + spaces
                
            return replacement + spaces

        return pattern.sub(replace_callback, text)

    def _process_abbreviations(self, text: str) -> str:
        if '.' not in text:
            return text
        return self.abbr_clean_pattern.sub(r'\1', text)

    def _process_adverbs(self, text: str) -> str:
        if not self.re_has_adverb.search(text):
            return text
        return self.adverb_pattern.sub(lambda m: m.group(0).replace('-', ''), text)

    def _process_plus_signs(self, text: str) -> str:
        if '+' not in text:
            return text
        return self._replace_plus_sign(text)

    def _process_initials_wrapper(self, text: str) -> str:
        if '.' not in text:
            return text
        return self._replace_initials(text)

    def _process_numeric_blocks(self, text: str) -> str:
        if not self.re_has_digits.search(text):
            return text

        text = self.date_pattern.sub(self._replace_dates, text)

        if '%' in text:
            text = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', self._replace_percentages, text)

        text = self.year_pattern.sub(self._replace_years, text)
        text = re.sub(r'\b\d+[.,]\d+\b', self._replace_floats, text)

        return text

    def _process_introductory_vowels(self, text: str) -> str:
        if ',' not in text:
            return text
        return re.sub(r'^([—–«"\s-]*)([АО])(?=,)', r'\1\2' + '\u0301', text)

    def normalize(self, text: str) -> str:
        if not text:
            return text

        # 1. Сокращения
        text = self._process_abbreviations(text)

        # 2. Все-всё и Всем-всём
        if self.use_yo:
            text = self.vse_yoficator.process(text)
            text = self.vsem_yoficator.process(text)

        # 3. Наречия
        text = self._process_adverbs(text)

        # 4. Плюсы в числах
        text = self._process_plus_signs(text)

        # 5. Ударения (user.txt) И Ёфикация (yo.txt) с БЫСТРЫМ СТОРОЖЕВЫМ ФИЛЬТРОМ
        if self._all_dict_keys:
            sentence_words = {w.lower() for w in _WORDS_FIND_PATTERN.findall(text)}
            if sentence_words & self._all_dict_keys:
                text = _WORD_REPLACE_PATTERN.sub(self._apply_fix_match, text)

        # 6. Обработка инициалов
        text = self._process_initials_wrapper(text)

        # 7. Числовой блок
        text = self._process_numeric_blocks(text)

        # 8. Автоматическое ударение для одиночных А и О перед запятой
        text = self._process_introductory_vowels(text)

        return text
