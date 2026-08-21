import re

class VseYoficator:
    """
    Правиловый модуль контекстной ёфикации слова "все" -> "всё".
    Быстрый, без внешних зависимостей.
    """

    CMP_WORDS = {
        'больше', 'меньше', 'быстрее', 'медленнее', 'дальше', 'ближе', 'раньше', 
        'позже', 'труднее', 'сложнее', 'страшнее', 'тяжелее', 'глубже', 'холодней', 
        'холоднее', 'мрачнее', 'яростнее', 'проще', 'лучше', 'хуже', 'свежее', 'выше', 
        'жестче', 'зачетнее', 'тревожнее', 'громче', 'тише', 'ярче', 'чаще', 'реже',
        'сильнее', 'сильней', 'слабее', 'старше', 'моложе', 'круче', 'четче', 'чётче'
    }

    NEUTER_PREDICATES = {
        'было', 'стало', 'изменилось', 'кончилось', 'произошло', 'завершилось',
        'ясно', 'готово', 'нормально', 'четко', 'реально', 'сводится', 'может',
        'устроено', 'обстояло', 'выходило', 'напоминало', 'выглядело', 'чувствовала', 
        'шэрится', 'отлично', 'предсказуемо', 'под', 'зря', 'хорошо', 'просто', 'сложно',
        'понятно', 'очевидно', 'известно', 'важно', 'страшно',
        'надо', 'нужно', 'необходимо', 'должно', 'нельзя', 'пора', 'гораздо'
    }

    POSSESSIVE_PRONOUNS = {'ее', 'её', 'его', 'их', 'свои', 'мои', 'твои', 'наши', 'ваши'}

    def __init__(self):

        self.RE_HAS_VSE = re.compile(r'\bвсе\b', re.IGNORECASE)

        SAFE_ADVERBS_A = r'(?:вчера|всегда|иногда|сюда|туда|тогда|пока|снова|сполна|сперва|навсегда|никогда|два|три|себя|тебя|меня)'
        PLURAL_ENDINGS = r'(?:ы|и|а|я|ые|ие|их|ых|ым|им|ыми|ими|ами|ями|ах|ях|ними)'

        # ФАЗА 1: Строгая защита "ВСЕ" (Е)
        self.RE_KTO = re.compile(r'\b([Вв]се)(?=\s*,\s*кто\b)', re.IGNORECASE)
        self.RE_NUMERALS = re.compile(r'\b([Вв]се)(?=\s+(?:два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\b)', re.IGNORECASE)
        self.RE_THEY_AFTER = re.compile(r'\b([Вв]се)(?=\s+(?:мы|вы|они)\b)', re.IGNORECASE)
        self.RE_STO = re.compile(r'\bна\s+([Вв]се)\s+сто\b', re.IGNORECASE)

        self.RE_ALL_PLURAL_DETERMINERS = re.compile(
            r'\b([Вв]се)(?=\s+(?:эти|те|остальные|прочие|как\s+один|до\s+одного|до\s+единого)\b)',
            re.IGNORECASE
        )

        self.RE_POSSESSIVE_TRIPLET = re.compile(
            r'\b([Вв]се)\s+(ее|её|его|их|свои|мои|твои|наши|ваши)\s+([а-яА-ЯёЁ-]+)', 
            re.IGNORECASE
        )

        # ФАЗА 2: Строгая защита "ВСЁ" (Ё)
        self.RE_CHTO = re.compile(
            r'\b([Вв]се)(?=\s*,\s*(?:что|чего|чем|чему|чём|ком|(?:о|в|к|за|из-за|ради)\s+(?:что|чего|чем|чём))\b)', 
            re.IGNORECASE
        )

        self.RE_PHRASES = re.compile(
            r'\b([Вв]се)(?=\s+(?:'
            r'равно|время|еще|ещё|такое|и\s+вся|'
            r'дочиста|дотла|воскресенье|'
            r'утро|лето|детство' 
            r')\b)',
            re.IGNORECASE
        )

        self.RE_THIS_THAT = re.compile(r'\b([Вв]се)(?=\s+(?:это|то)\b)', re.IGNORECASE)
        self.RE_TAKIS = re.compile(r'\b([Вв]се)(?=\s*-\s*таки\b)', re.IGNORECASE)
        self.RE_IDELE = re.compile(r'\b([Вв]се)(?=\s+и\s+дело\b)', re.IGNORECASE)
        self.RE_DELO = re.compile(r'\b([Вв]се)(?=\s+дело\b)', re.IGNORECASE)
        
        # Интеграция концов предложений ("И это все?", "вот и все.", "прости за все")
        self.RE_END_VSE = re.compile(
            r'\b(вот\s+и\s+|на\s+этом\s+|пока\s+|прости\s+за\s+|это\s+|вот\s+|и\s+|а\s+)([Вв]се)(?=\s*(?:[.!?…»"”)]|$))', 
            re.IGNORECASE
        )

        # ФАЗА 3: Контекстные регулярки
        # Интеграция "это все" + контекст
        self.RE_ETO_VSE = re.compile(
            r'\b(?:(я|ты|мы|вы|он|она|они)\s+)?(это|вот)\s+([Вв]се)\s+([а-яА-ЯёЁ-]+)', 
            re.IGNORECASE
        )

        self.RE_SINGULAR_PRONOUN_VSE = re.compile(
            r'\b(я|ты|он|она|оно|мне|тебе|ему|ей)\s+([Вв]се)'
            r'(?=\s+(?:'
            r'про|в|во|на|за|об|о|от|до|с|со|по|к|ко|из|у|под|над|для|без|'
            r'это|то|так|там|тут|здесь|туда|сюда|оттуда|потому|поэтому|'
            r'совсем|сразу|вдруг|опять|снова|никак|уже|просто|же|очень|вполне|наконец|еще|ещё|не|ни|'
            r'[а-яА-ЯёЁ]*(?:ю|у|ешь|ишь|ет|ит|л|ло|лся|лось)'
            r')\b)', 
            re.IGNORECASE
        )

        self.RE_VSE_ZHE_CONTEXT = re.compile(r'\b([Вв]се)\s+же\s+([а-яА-ЯёЁ-]+)', re.IGNORECASE)
        self.RE_DUAL_IDIOMS = re.compile(
            r'\b([Вв]се)\s+(в\s+порядке|как\s+обычно|как\s+всегда|по-прежнему|по-старому|по-новому)\b', 
            re.IGNORECASE
        )

        self.RE_PREV_VERB_SINGULAR = re.compile(
            rf'\b([а-яА-ЯёЁ]+(?:ло|лось|ет|ёт|ит|ал|ял|ил|ел|ул|ла|лась|ется|ётся|ится|ть|ти|чь|ешь|ишь))'
            rf'(?:\s+(?:бы|б|он|она|оно|я|ты|совсем|сразу|вообще|абсолютно|просто|уже|еще|ещё|никак|почти|наконец|же)){{0,3}}'
            rf'\s+([Вв]се)'
            rf'(?!\s+(?!{SAFE_ADVERBS_A}\b)[а-яА-ЯёЁ]+{PLURAL_ENDINGS}\b)\b', 
            re.IGNORECASE
        )

        self.RE_PREV_VERB_PLURAL_PRONOUN = re.compile(
            rf'\b(мы|вы|они|я|ты|он|она|оно)\s+(?:[а-яА-ЯёЁ]+\s+){{0,2}}'
            rf'([а-яА-ЯёЁ]+(?:ли|лись|ют|ут|ят|ат|ете|ите|ем|ём|им))'
            rf'(?:\s+(?:совсем|сразу|вообще|абсолютно|просто|уже|еще|ещё|почти|же)){{0,2}}'
            rf'\s+([Вв]се)'
            rf'(?!\s+(?!{SAFE_ADVERBS_A}\b)[а-яА-ЯёЁ]+{PLURAL_ENDINGS}\b)\b', 
            re.IGNORECASE
        )

        self.RE_NEXT_WORD = re.compile(r'\b([Вв]се)\s+([а-яА-ЯёЁ-]+)')
        
        self.RE_END_VERB_PLURAL = re.compile(r'(ют|ут|ят|ат|ли|ете|ите|ем|ём|им|ются|утся|ятся|атся|лись)$', re.IGNORECASE)
        self.RE_END_VERB_SINGULAR = re.compile(r'(ет|ёт|ит|ется|ётся|ится|тся|лось|лась|ло|ла|ал|ял|ил|ел|ул|ол|ть|йти|чь|ешь|ешься|ишь|ишься)$', re.IGNORECASE)
        self.RE_END_PLURAL_NOUN = re.compile(PLURAL_ENDINGS + r'$', re.IGNORECASE)
        self.RE_END_COMPARATIVE = re.compile(r'(че|ше|ще|ее|ей)$', re.IGNORECASE)
        self.RE_END_OE = re.compile(r'(ое|ее)$', re.IGNORECASE)
        self.RE_END_NO = re.compile(r'(то|но)$', re.IGNORECASE)

    @staticmethod
    def _make_yo_prot(m: re.Match) -> str:
        word = m.group(1) if m.lastindex else m.group(0)
        return 'Всё__YO__' if word[0].isupper() else 'всё__YO__'

    @staticmethod
    def _make_e_prot(m: re.Match) -> str:
        word = m.group(1) if m.lastindex else m.group(0)
        return 'Все__E__' if word[0].isupper() else 'все__E__'

    def _fix_dual_idioms(self, match: re.Match, full_text: str) -> str:
        start_pos = match.start()
        prefix = full_text[:start_pos].strip()
        vse_w, idiom = match.group(1), match.group(2)
        is_cap = vse_w[0].isupper()
        yo = 'Всё__YO__' if is_cap else 'всё__YO__'
        e = 'Все__E__' if is_cap else 'все__E__'

        # Убрали \s+ перед концевым символом $
        if re.search(r'\b(мы|вы|они|люди|многие|гости)$', prefix, re.IGNORECASE):
            return f"{e} {idiom}"
        
        return f"{yo} {idiom}"

    def _fix_vse_zhe(self, match: re.Match, full_text: str) -> str:
        start_pos = match.start()
        prefix = full_text[:start_pos].strip()
        vse_word, next_word = match.group(1), match.group(2)
        
        is_cap = vse_word[0].isupper()
        yo = 'Всё__YO__' if is_cap else 'всё__YO__'
        e = 'Все__E__' if is_cap else 'все__E__'

        if self.RE_END_VERB_PLURAL.search(next_word.lower()):
            if not prefix or prefix[-1] in '.!?\n':
                return f"{e} же {next_word}"
            
        return f"{yo} же {next_word}"

    def _fix_singular_pronoun_vse(self, match: re.Match) -> str:
        pronoun = match.group(1)
        vse_w = match.group(2)
        # Бережно сохраняем табы и пробелы между словами
        spaces = match.string[match.start(1)+len(pronoun):match.start(2)]
        yo = 'Всё__YO__' if vse_w[0].isupper() else 'всё__YO__'
        return f"{pronoun}{spaces}{yo}"

    def _fix_possessive_triplet(self, match: re.Match, full_text: str) -> str:
        start_pos = match.start()
        prefix = full_text[:start_pos].strip()
        vse_w, poss_w, noun_w = match.group(1), match.group(2), match.group(3)
        
        is_cap = vse_w[0].isupper()
        yo = 'Всё__YO__' if is_cap else 'всё__YO__'
        e = 'Все__E__' if is_cap else 'все__E__'

        poss_l, noun_l = poss_w.lower(), noun_w.lower()

        # Защита конструкций "это все мои хиты/проблемы" -> Ё
        if re.search(r'\b(это|вот)$', prefix, re.IGNORECASE):
            return f"{yo} {poss_w} {noun_w}"

        if poss_l in {'свои', 'мои', 'твои', 'наши', 'ваши'}:
            return f"{e} {poss_w} {noun_w}"

        if self.RE_END_PLURAL_NOUN.search(noun_l) or re.search(r'(я|и|ы|ния|тия)$', noun_l):
            return f"{e} {poss_w} {noun_w}"
        
        return f"{yo} {poss_w} {noun_w}"

    def _fix_eto_vse(self, match: re.Match) -> str:
        pronoun, prefix, vse_w, next_w = match.group(1), match.group(2), match.group(3), match.group(4)
        next_l = next_w.lower()
        
        is_cap = vse_w[0].isupper()
        yo = 'Всё__YO__' if is_cap else 'всё__YO__'
        e = 'Все__E__' if is_cap else 'все__E__'

        full_prefix = f"{pronoun} {prefix}" if pronoun else prefix

        # Личное местоимение + это все -> 100% Ё ("я это все понимаю", "ты это все убери")
        if pronoun:
            return f"{full_prefix} {yo} {next_w}"

        # Глагол множественного числа -> Е ("Это все заметили", "Это все знают")
        # Исключение: связки были/стали ("Это всё были пустые слова")
        if self.RE_END_VERB_PLURAL.search(next_l) and next_l not in {'были', 'стали'}:
            return f"{full_prefix} {e} {next_w}"

        # Указательные местоимения мн.ч. -> Е ("Это все те, кому хорошо")
        if next_l in {'те', 'эти', 'такие', 'какие', 'люди', 'они', 'мы', 'вы'}:
            return f"{full_prefix} {e} {next_w}"

        # Глагол ед.ч. или конкретные маркеры -> Ё ("Это все усложнит", "Что это все значит?")
        if self.RE_END_VERB_SINGULAR.search(next_l) or next_l == 'значит':
            return f"{full_prefix} {yo} {next_w}"

        # По умолчанию (сон, лирика, пустые сплетни, тот, та, тоже, он, она, слова) -> Ё
        return f"{full_prefix} {yo} {next_w}"

    def _analyze_next(self, match: re.Match) -> str:
        vse_w, next_w = match.group(1), match.group(2)
        next_l = next_w.lower()
        is_cap = vse_w[0].isupper()
        yo = 'Всё__YO__' if is_cap else 'всё__YO__'
        e = 'Все__E__' if is_cap else 'все__E__'

        if next_l in self.POSSESSIVE_PRONOUNS:
            return match.group(0)

        if self.RE_END_VERB_PLURAL.search(next_l):
            return f"{e} {next_w}"

        if self.RE_END_VERB_SINGULAR.search(next_l):
            return f"{yo} {next_w}"

        if next_l in self.CMP_WORDS or self.RE_END_COMPARATIVE.search(next_l):
            return f"{yo} {next_w}"

        if next_l in self.NEUTER_PREDICATES or self.RE_END_NO.search(next_l):
            return f"{yo} {next_w}"

        if self.RE_END_OE.search(next_l) and next_l != 'все':
            return f"{yo} {next_w}"

        if self.RE_END_PLURAL_NOUN.search(next_l):
            return f"{e} {next_w}"

        return match.group(0)

    def process(self, text: str) -> str:
        if not self.RE_HAS_VSE.search(text):
            return text

        # ФАЗА 1: Защита "ВСЕ" (Е)
        text = self.RE_KTO.sub(self._make_e_prot, text)
        text = self.RE_NUMERALS.sub(self._make_e_prot, text)
        text = self.RE_THEY_AFTER.sub(self._make_e_prot, text)
        text = self.RE_STO.sub(lambda m: f"на {'все__E__' if m.group(1)[0].islower() else 'Все__E__'} сто", text)
        text = self.RE_ALL_PLURAL_DETERMINERS.sub(self._make_e_prot, text)
        text = self.RE_POSSESSIVE_TRIPLET.sub(lambda m: self._fix_possessive_triplet(m, text), text)

        # ФАЗА 2: Защита "ВСЁ" (Ё)
        text = self.RE_CHTO.sub(self._make_yo_prot, text)
        text = self.RE_PHRASES.sub(self._make_yo_prot, text)
        text = self.RE_THIS_THAT.sub(self._make_yo_prot, text)
        text = self.RE_TAKIS.sub(self._make_yo_prot, text)
        text = self.RE_IDELE.sub(self._make_yo_prot, text)
        text = self.RE_DELO.sub(self._make_yo_prot, text)
        
        # Мощное правило для конструкций, упирающихся в знаки препинания ("И это все?")
        text = self.RE_END_VSE.sub(
            lambda m: f"{m.group(1)}{'Всё__YO__' if m.group(2)[0].isupper() else 'всё__YO__'}", 
            text
        )

        # ФАЗА 3: Контекстные правила
        text = self.RE_DUAL_IDIOMS.sub(lambda m: self._fix_dual_idioms(m, text), text)
        text = self.RE_VSE_ZHE_CONTEXT.sub(lambda m: self._fix_vse_zhe(m, text), text)
        
        # Интеллектуальный анализатор конструкций "это все [слово]"
        text = self.RE_ETO_VSE.sub(self._fix_eto_vse, text)

        text = self.RE_SINGULAR_PRONOUN_VSE.sub(self._fix_singular_pronoun_vse, text)

        # Поиск по глаголам до слова
        text = self.RE_PREV_VERB_SINGULAR.sub(
            lambda m: f"{m.group(1)}{text[m.start(1)+len(m.group(1)):m.start(2)]}{'Всё__YO__' if m.group(2)[0].isupper() else 'всё__YO__'}", 
            text
        )
        text = self.RE_PREV_VERB_PLURAL_PRONOUN.sub(
            lambda m: f"{m.group(1)}{text[m.start(1)+len(m.group(1)):m.start(3)]}{'Всё__YO__' if m.group(3)[0].isupper() else 'всё__YO__'}", 
            text
        )

        # Поиск по зависимости от следующего слова
        text = self.RE_NEXT_WORD.sub(self._analyze_next, text)

        # ФАЗА 4: Разморозка маркеров
        text = text.replace('все__E__', 'все').replace('Все__E__', 'Все')
        text = text.replace('всё__YO__', 'всё').replace('Всё__YO__', 'Всё')

        return text