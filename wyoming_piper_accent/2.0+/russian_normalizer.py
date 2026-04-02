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
    _IS_LOADING = False

    def __init__(self, use_yo: bool = False):
        self.use_yo = use_yo
        self.yo_map = {}
        
        if not NUM2WORDS_AVAILABLE:
            _LOGGER.warning("Библиотека `num2words` не найдена. Преобразование чисел в текст будет недоступно.")

        # Загрузка словаря, если передан флаг --yo
        if self.use_yo:
            # Проверяем, не загружен ли словарь уже кем-то другим
            if RussianNormalizer._SHARED_YO_MAP is None:
                self._load_yo_dictionary()
                RussianNormalizer._SHARED_YO_MAP = self.yo_map
            else:
                # Просто берем уже готовую карту из памяти
                self.yo_map = RussianNormalizer._SHARED_YO_MAP

        # словарь работает в связке с правилами espeak-ng
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
                _LOGGER.warning(f"Файл {dict_path} не найден. Ёфикация отключена.")
                return

            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    word_yo = line.strip().lower()
                    if not word_yo:
                        continue
                    
                    # Создаем ключ (слово через 'е')
                    word_e = word_yo.replace('ё', 'е')
                    if word_e != word_yo:
                        self.yo_map[word_e] = word_yo
            
            _LOGGER.info(f"Словарь ёфикации загружен: {len(self.yo_map)} слов.")
        except Exception as e:
            _LOGGER.error(f"Ошибка загрузки словаря ё: {e}")

    def _yo_replace_match(self, match: re.Match) -> str:
        word = match.group(0)
        low_word = word.lower()
        
        if low_word in self.yo_map:
            rep = self.yo_map[low_word]
            # Сохраняем регистр
            new_word = rep.capitalize() if word[0].isupper() else rep
            
            # Логируем только если слово реально изменилось (е -> ё)
            if word != new_word:
                _LOGGER.debug(f"[YO] {word} -> {new_word}")
                
            return new_word
            
        return word

    def _get_noun_form(self, n: int, forms: list) -> str:
        """
        Универсальный подбор формы существительного.
        forms: ['процент', 'процента', 'процентов']
        """
        if 10 < n % 100 < 20: return forms[2]
        last = n % 10
        if last == 1: return forms[0]
        if 2 <= last <= 4: return forms[1]
        return forms[2]

    def _float_to_text(self, num_str: str, for_percent: bool = False) -> str:
        """
        Преобразует '5.5' -> 'пять и пять'.
        Если for_percent=True и это десятая доля (один знак), 
        используем мужской род ('один', 'два'), чтобы согласовать с 'процент'.
        """
        if not NUM2WORDS_AVAILABLE:
            return num_str.replace('.', ' и ').replace(',', ' и ')

        clean_num = num_str.replace(',', '.')
        try:
            parts = clean_num.split('.')
            if len(parts) != 2: return num_str
            
            int_part = int(parts[0])
            frac_part = int(parts[1])
            frac_part_str = parts[1]
            frac_len = len(frac_part_str)

            int_text = num2words(int_part, lang='ru')
            
            # --- СЛУЧАЙ 1: Разговорные десятые (36.6, 1.1%) ---
            if frac_len == 1:
                # Используем мужской род ('один', 'два')
                frac_text = num2words(frac_part, lang='ru')
                return f"{int_text} и {frac_text}"

            # --- СЛУЧАЙ 2: Точные сотые/тысячные (0.01) ---
            # Здесь всегда женский род ('одна', 'две'), так как доли - жен. рода
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
        
        # ДРОБНЫЕ ПРОЦЕНТЫ
        if '.' in num_str:
            parts = num_str.split('.')
            frac_part_str = parts[1]
            
            if len(frac_part_str) == 1:
                # 1.1% -> один и один процент
                # 1.5% -> один и пять процентов
                text_num = self._float_to_text(num_str, for_percent=True)
                frac_val = int(frac_part_str)
                word = self._get_noun_form(frac_val, ['процент', 'процента', 'процентов'])
                return f"{text_num} {word}"
            else:
                # 1.01% -> один и одна сотая процента
                text_num = self._float_to_text(num_str)
                return f"{text_num} процента"
        
        # ЦЕЛЫЕ ПРОЦЕНТЫ
        word = self._get_noun_form(int(num_str), ['процент', 'процента', 'процентов'])
        return f"{num_str} {word}"

    def _replace_floats(self, match: re.Match) -> str:
        """Замена обычных дробей (36.6)."""
        return self._float_to_text(match.group(0))

    def normalize(self, text: str) -> str:
        # 0. Исправляем наречия с дефисами ПЕРЕД остальной обработкой
        for pattern, replacement in self.adverb_fixes.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # 1. Ёфикация по словарю
        if self.use_yo and self.yo_map:
            # Регулярка ищет слова из кириллицы
            text = re.sub(r'[а-яА-ЯёЁ-]+', self._yo_replace_match, text)

        # 2. Проценты
        text = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', self._replace_percentages, text)
        
        # 3. Оставшиеся дроби
        text = re.sub(r'\b\d+[.,]\d+\b', self._replace_floats, text)
        
        return text