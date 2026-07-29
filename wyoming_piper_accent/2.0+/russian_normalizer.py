import re
import logging
from pathlib import Path

from .sentence_boundary import ABBR_FOR_INTONATION

_LOGGER = logging.getLogger(__name__)

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False


class RussianNormalizer:

    _SHARED_YO_MAP = None  
    _SHARED_STRESS_MAP = None
    _SHARED_CAPITALIZED_STRESS_MAP = None

    # Карта для озвучивания одиночных инициалов
    _INITIALS_MAP = {
        'А': 'А\u0301',
        'Б': 'Бэ',
        'В': 'Вэ',
        'Г': 'Гэ',
        'Д': 'Дэ',
        'Е': 'Е\u0301',
        'Ё': 'Ё',
        'Ж': 'Же',
        'З': 'Зэ',
        'И': 'И\u0301',
        'Й': 'Йот',
        'К': 'Ка',
        'Л': 'Эль',
        'М': 'Эм',
        'Н': 'Эн',
        'О': 'О\u0301',
        'П': 'Пэ',
        'Р': 'Эр',
        'С': 'Эс',
        'Т': 'Тэ',
        'У': 'У\u0301',
        'Ф': 'Эф',
        'Х': 'Ха',
        'Ц': 'Цэ',
        'Ч': 'Че',
        'Ш': 'Шэ',
        'Щ': 'Ща',
        'Э': 'Э\u0301',
        'Ю': 'Ю\u0301',
        'Я': 'Я\u0301',
    }

    def __init__(self, use_yo: bool = False):
        self.use_yo = use_yo
        self.yo_map = {}
        self.stress_map = {}
        self.capitalized_stress_map = {}

        self.compound_prefixes = {
            'зелено': 'зелёно',
            'черно': 'чёрно',
            'темно': 'тёмно',
            'пестро': 'пёстро',
            'светло': 'све́тло',
        }
        
        # Регулярное выражение для поиска годов
        self.year_pattern = re.compile(
            r'\b(?P<num>\d{1,4})'              # Число (год)
            r'(?:-?[а-яё]{1,3})?'              # Любой из стандартных суффиксов
            r'\s+'                             # Пробел
            r'(?P<god>год[а-яё]{0,3})\b',      # Форма слова "год"
            re.IGNORECASE
        )
        
        if not NUM2WORDS_AVAILABLE:
            _LOGGER.warning("Библиотека `num2words` не найдена. Преобразование чисел в текст недоступно.")

        # Загрузка словаря ёфикации
        if self.use_yo:
            if RussianNormalizer._SHARED_YO_MAP is None:
                self._load_yo_dictionary()
                RussianNormalizer._SHARED_YO_MAP = self.yo_map
            else:
                self.yo_map = RussianNormalizer._SHARED_YO_MAP

        # Загрузка пользовательских ударений
        if RussianNormalizer._SHARED_STRESS_MAP is None:
            self._load_stress_dictionary()
            RussianNormalizer._SHARED_STRESS_MAP = self.stress_map
            RussianNormalizer._SHARED_CAPITALIZED_STRESS_MAP = self.capitalized_stress_map
        else:
            self.stress_map = RussianNormalizer._SHARED_STRESS_MAP
            self.capitalized_stress_map = RussianNormalizer._SHARED_CAPITALIZED_STRESS_MAP

        self.abbr_clean_pattern = re.compile(
            rf'\b({ABBR_FOR_INTONATION})\.(?=\s)', 
            re.IGNORECASE
        )

        self.adverb_fixes = {
            r'\bпо-моему\b': 'помоему',
            r'\bпо-твоему\b': 'потвоему',
            r'\bпо-своему\b': 'посвоему',
        }

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
            new_parts =[]
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
                suffix = " сотая" if (last_digit == 1 and last_two != 11) else " сотых"
            elif frac_len == 3:
                suffix = " тысячная" if (last_digit == 1 and last_two != 11) else " тысячных"
            else:
                return f"{int_text} точка {frac_text}"
            return f"{int_text} и {frac_text}{suffix}"
        except Exception:
            return num_str

    def _replace_percentages(self, match: re.Match) -> str:
        num_str = match.group(1).replace(',', '.')
        if '.' in num_str:
            parts = num_str.split('.')
            frac_part_str = parts[1]
            if len(frac_part_str) == 1:
                text_num = self._float_to_text(num_str, for_percent=True)
                frac_val = int(frac_part_str)
                word = self._get_noun_form(frac_val,['процент', 'процента', 'процентов'])
                return f"{text_num} {word}"
            else:
                text_num = self._float_to_text(num_str)
                return f"{text_num} процента"
        word = self._get_noun_form(int(num_str),['процент', 'процента', 'процентов'])
        return f"{num_str} {word}"

    def _replace_floats(self, match: re.Match) -> str:
        return self._float_to_text(match.group(0))

    def _replace_years(self, match: re.Match) -> str:
        if not NUM2WORDS_AVAILABLE:
            return match.group(0)

        num_str = match.group('num')
        god_word_raw = match.group('god')
        god_word = god_word_raw.lower()

        try:
            ordinal_text = num2words(int(num_str), to='ordinal', lang='ru')
        except:
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
        # Находим одиночную заглавную русскую букву с точкой и возможными пробелами после неё
        pattern = re.compile(r'\b([А-ЯЁ])\.(\s*)')
        
        def replace_callback(m: re.Match) -> str:
            letter = m.group(1)
            spaces = m.group(2)
            
            # Получаем фонетический вариант буквы из словаря
            replacement = self._INITIALS_MAP.get(letter, letter)
            
            # Анализируем контекст после найденного совпадения в исходной строке
            full_str = m.string
            end_idx = m.end()
            remaining = full_str[end_idx:].strip()
            
            # 1. Если следом идет еще один инициал (например, "С." после "А." в "А.С.")
            # принудительно ставим один пробел для разделения при произношении
            if re.match(r'^[А-ЯЁ]\.', remaining):
                return replacement + " "
                
            # 2. Если дальше ничего нет, либо только знаки препинания конца предложения,
            # возвращаем слово с точкой на конце, чтобы сохранить структуру предложения для TTS
            if not remaining or re.match(r'^[)\s\]}»"”’\-—–.!?;:]+$', remaining):
                return replacement + "." + spaces
                
            # 3. В остальных случаях возвращаем замену с сохранением исходных пробелов
            return replacement + spaces

        return pattern.sub(replace_callback, text)

    def normalize(self, text: str) -> str:

        text = self.abbr_clean_pattern.sub(r'\1', text)

        # 0. Наречия
        for pattern, replacement in self.adverb_fixes.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # 1. Плюсы в числах
        text = self._replace_plus_sign(text)

        # 2. Ударения (fix.txt) И Ёфикация (yo.txt)
        if self.stress_map or (self.use_yo and self.yo_map):
            text = re.sub(r'[а-яА-ЯёЁ-]+', self._apply_fix_match, text)

        # 2.5. Обработка инициалов (например: В. Ц. -> Вэ Цэ)
        text = self._replace_initials(text)

        # 3. Проценты
        text = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', self._replace_percentages, text)
        
        # 4. Года (например: в 1961 году, 1930 годами)
        text = self.year_pattern.sub(self._replace_years, text)
        
        # 5. Оставшиеся дроби
        text = re.sub(r'\b\d+[.,]\d+\b', self._replace_floats, text)

        # 6. Автоматическое ударение для одиночных А и О перед запятой в начале
        text = re.sub(r'^([—–«"\s-]*)([АО])(?=,)', r'\1\2' + '\u0301', text)
        
        return text