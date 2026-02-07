import re
import logging

_LOGGER = logging.getLogger(__name__)

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False


class RussianNormalizer:
    def __init__(self):
        if not NUM2WORDS_AVAILABLE:
            _LOGGER.warning("Библиотека `num2words` не найдена.")

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
        # 1. Проценты
        text = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', self._replace_percentages, text)
        
        # 2. Оставшиеся дроби
        text = re.sub(r'\b\d+[.,]\d+\b', self._replace_floats, text)
        
        return text