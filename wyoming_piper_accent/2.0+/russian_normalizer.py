import re
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False


class RussianNormalizer:

    _SHARED_YO_MAP = None  
    _SHARED_STRESS_MAP = None

    def __init__(self, use_yo: bool = False):
        self.use_yo = use_yo
        self.yo_map = {}
        self.stress_map = {}
        
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
        else:
            self.stress_map = RussianNormalizer._SHARED_STRESS_MAP

        self.adverb_fixes = {
            r'\bпо-моему\b': 'помоему',
            r'\bпо-твоему\b': 'потвоему',
            r'\bпо-своему\b': 'посвоему',
        }

    def _load_yo_dictionary(self):
        """Загрузка чистого словаря ёфикации."""
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
        """Загрузка пользовательских ударений из user.txt."""
        try:
            dict_path = Path(__file__).parent / "user.txt"
            if not dict_path.exists():
                return

            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    # 1. Убираем комментарии и лишние пробелы
                    line = line.split('#')[0].strip()
                    
                    # 2. Если после очистки строка пустая — пропускаем
                    if not line:
                        continue
                    
                    # 3. Проверяем наличие знака ударения
                    if '+' in line:
                        # Ключ — слово без плюса в нижнем регистре
                        word_clean = line.replace('+', '').lower()
                        self.stress_map[word_clean] = line.lower()
            
            _LOGGER.info(f"Словарь ударений загружен: {len(self.stress_map)} слов.")
        except Exception as e:
            _LOGGER.error(f"Ошибка загрузки словаря ударений: {e}")

    def _apply_fix_match(self, match: re.Match) -> str:
        """Универсальная замена с сохранением регистра."""
        word = match.group(0)
        low_word = word.lower()

        # 1. ПРОВЕРКА ЦЕЛОГО СЛОВА
        # Из пользовательского словаря (user.txt)
        if low_word in self.stress_map:
            return self._restore_case(word, self.stress_map[low_word])
        
        # Ёфикация (yo.txt), если включено
        if self.use_yo and low_word in self.yo_map:
            return self._restore_case(word, self.yo_map[low_word])

        # 2. При наличии дефиса
        if '-' in low_word:
            parts = low_word.split('-')
            new_parts = []
            changed = False
            
            for p in parts:
                # Проверка частей (только user.txt)
                if p in self.stress_map:
                    new_parts.append(self.stress_map[p])
                    changed = True
                else:
                    new_parts.append(p)
            
            if changed:
                return self._restore_case(word, '-'.join(new_parts))
                
        return word

    def _restore_case(self, original: str, replacement: str) -> str:
        """Переносит регистр с оригинала на замену (Учитывает ЗАГЛАВНЫЕ и С большой буквы)."""
        if original.isupper():
            return replacement.upper()
        if original[0].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement

    def _replace_plus_sign(self, text: str) -> str:

        text = re.sub(r'\s*\+\s*(?=\d)', ' плюс ', text)
        text = re.sub(r'(?<=[a-zA-Zа-яА-ЯёЁ])\+(?![a-zA-Zа-яА-ЯёЁ])', ' плюс', text)
        
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
                word = self._get_noun_form(frac_val, ['процент', 'процента', 'процентов'])
                return f"{text_num} {word}"
            else:
                text_num = self._float_to_text(num_str)
                return f"{text_num} процента"
        word = self._get_noun_form(int(num_str), ['процент', 'процента', 'процентов'])
        return f"{num_str} {word}"

    def _replace_floats(self, match: re.Match) -> str:
        return self._float_to_text(match.group(0))

    def normalize(self, text: str) -> str:
        # 0. Наречия
        for pattern, replacement in self.adverb_fixes.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # 1. Плюсы в числах
        text = self._replace_plus_sign(text)

        # 2. Ударения (fix.txt) И Ёфикация (yo.txt)
        # Объединяем поиск в один проход по словам для скорости
        if self.stress_map or (self.use_yo and self.yo_map):
            text = re.sub(r'[а-яА-ЯёЁ-]+', self._apply_fix_match, text)

        # 3. Проценты
        text = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', self._replace_percentages, text)
        
        # 4. Оставшиеся дроби
        text = re.sub(r'\b\d+[.,]\d+\b', self._replace_floats, text)
        
        return text