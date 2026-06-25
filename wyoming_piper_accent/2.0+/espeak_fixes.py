"""Костыль для защиты хардкодных правил espeak от расстановки ударений."""
import re

_STRESS_MARK = "\u0301"

# Полный список защищаемых словосочетаний
ESPEAK_PHRASES = [
    "а потом", "но потом", "без вести", "в боку", "по тому",
    "в виду", "в глаза", "в города", "в другом", "в жару",
    "в мозгу", "в поту", "в снегу", "в ходу", "за бока",
    "из ведра", "из города", "из лесу", "на ходу", "как дела",
    "косая черта", "к тому", "к тому же", "мои глаза", "мало ли",
    "на боку", "на виду", "на свои места", "на душе", "на сорок",
    "ни слова", "одной стороны", "от души", "от числа", "о бока",
    "от руки", "с высоты", "с горы", "с луны", "самой собой",
    "самой себя", "самой себе", "самого себя", "самому себе"
    "у стены", "со стены", "на полу", "на ноги", "из стены",
    "ни мало", "к черту",
]

def _build_espeak_regex(phrases: list[str]) -> re.Pattern:
    # Задаем жесткие границы слов (работают даже если слово оканчивается знаком ударения)
    left_bound = r'(?<![а-яА-ЯёЁa-zA-Z])'
    right_bound = r'(?![а-яА-ЯёЁa-zA-Z])'
    
    patterns = []
    for phrase in phrases:
        parts = []
        for char in phrase:
            if char.isspace():
                parts.append(r'\s+')
            elif char == '-':
                parts.append(r'(?:\s*-\s*|-)')
            elif char.isalpha():
                # Разрешаем опциональный знак ударения после каждой буквы
                parts.append(re.escape(char) + f"(?:{_STRESS_MARK})?")
            else:
                parts.append(re.escape(char))
                
        patterns.append(f"{left_bound}{''.join(parts)}{right_bound}")

    return re.compile(f"({'|'.join(patterns)})", re.IGNORECASE)

_ESPEAK_REGEX = _build_espeak_regex(ESPEAK_PHRASES)

def remove_stress_for_espeak(text: str) -> str:
    """Находит защищенные фразы (с учетом проставленных ударений) и очищает их от ударений."""
    return _ESPEAK_REGEX.sub(lambda m: m.group(1).replace(_STRESS_MARK, ""), text)