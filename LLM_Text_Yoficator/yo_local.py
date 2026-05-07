import os
import re
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

# ================= НАСТРОЙКИ =================
LOCAL_API_BASE = "http://localhost:8040/v1"
LOCAL_API_KEY = "lemonade"
MODEL_NAME = "user.gemma-4-E4B-it-GGUF" 

BATCH_SIZE = 25
MAX_WORDS_SIDE = 6       # Кол-во слов вокруг таргета для ллм
DISPLAY_WINDOW = 4       # Кол-во слов вокруг таргета для пользователя.
MAX_CHARS_SEARCH = 400   # Лимит длины одного предложения

# --- ВЫРАВНИВАНИЕ ВЫВОДА ---
ALIGN_TARGETS = True     # True: центрирование, False: по левому краю
# ---------------------------

PREFETCH_QUEUE = 2 
DICT_FILE = "yox.txt"
# =============================================

client = OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)

def load_dictionary(filepath):
    abs_dict, ambig_dict = {}, {}
    print(f"Загрузка словаря: {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                is_ambig = line.endswith('?')
                word_yo = line.rstrip('?')
                word_e = word_yo.replace('ё', 'е').replace('Ё', 'Е')
                if is_ambig: ambig_dict[word_e] = word_yo
                else: abs_dict[word_e] = word_yo
        print(f"Словарь: {len(abs_dict)} автозамен, {len(ambig_dict)} спорных.")
    except FileNotFoundError:
        print("[!] Словарь не найден. Работа только через LLM.")
    return abs_dict, ambig_dict

def restore_case(orig, target):
    if orig.isupper(): return target.upper()
    if orig.istitle(): return target.capitalize()
    return target.lower()

def get_sentence_context(text, start, end, max_words=MAX_WORDS_SIDE, max_chars=MAX_CHARS_SEARCH):
    left_slice_start = max(0, start - max_chars)
    left_text = text[left_slice_start:start]
    
    right_slice_end = min(len(text), end + max_chars)
    right_text = text[end:right_slice_end]
    
    left_matches = list(re.finditer(r'[.?!…\n]+["»]*', left_text))
    c_start = left_matches[-1].end() if left_matches else 0
    left_part = left_text[c_start:]
    
    right_match = re.search(r'[.?!…\n]+["»]*', right_text)
    c_end = right_match.end() if right_match else len(right_text)
    right_part = right_text[:c_end]
    
    left_words = left_part.split()
    right_words = right_part.split()
    
    if len(left_words) > max_words:
        left_words = left_words[-max_words:]
    if len(right_words) > max_words:
        right_words = right_words[:max_words]
        
    left_clean = " ".join(left_words)
    right_clean = " ".join(right_words)
    
    left_final = f"{left_clean} " if left_clean else ""
    right_final = f" {right_clean}" if right_clean else ""
    
    return f"{left_final}<TARGET>{text[start:end]}</TARGET>{right_final}"

def shrink_for_user(ctx_str, window=DISPLAY_WINDOW):
    words = ctx_str.split()
    target_idx = next((i for i, w in enumerate(words) if "<TARGET>" in w), None)
    if target_idx is None: return ctx_str
    
    start = max(0, target_idx - window)
    end = target_idx + window + 1
    
    snippet = " ".join(words[start:end])
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(words) else ""
    return f"{prefix}{snippet}{suffix}"

def fetch_llm_response(batch_requests, retries=3):
    system_prompt = (
        "Ты профессиональный корректор. Выбери правильный вариант (е/ё) по смыслу.\n"
        "ОТВЕЧАЙ СТРОГО JSON: {\"results\":[{\"id\":0, \"replacement\":\"слово\"}]}. "
        "Никаких рассуждений!"
    )
    user_prompt = json.dumps(batch_requests, ensure_ascii=False)

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                extra_body={
                    "reasoning_budget": 0, # Отключаем мышление
                    "chat_template_kwargs": {"enable_thinking": False}
                },
                temperature=0.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            if attempt < retries - 1: time.sleep(2)
            else: return {"results":[]}

def apply_and_save(original_text, replacements, output_file):
    if not replacements: return
    replacements.sort(key=lambda x: x[0], reverse=True)
    modified = original_text
    for s, e, new in replacements:
        modified = modified[:s] + new + modified[e:]
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(modified)

def process_text(input_file, output_file, start_batch=1):
    abs_dict, ambig_dict = load_dictionary(DICT_FILE)
    
    target_file = input_file
    # Логика подхвата прогресса: если старт со смещением, берем уже созданный файл _yo.txt
    if start_batch > 1 and os.path.exists(output_file):
        target_file = output_file
        print(f"\n[ИНФО] Флаг запуска со смещением (Батч {start_batch}). Читаем уже начатый файл: {target_file}")
    else:
        print(f"\nАнализ '{target_file}'...")

    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Ошибка файла: {e}"); return

    word_pattern = re.compile(r'[А-Яа-яЁё]+')
    auto_reps, ambig_matches = [],[]
    
    for m in word_pattern.finditer(text):
        word = m.group(0)
        # Приводим всё к 'е', чтобы найти слово в словаре, даже если оно уже заменено
        word_e_lower = word.lower().replace('ё', 'е')
        
        if word_e_lower in abs_dict:
            yo_variant = restore_case(word, abs_dict[word_e_lower])
            if word != yo_variant: # Добавляем, только если оно еще не заменено
                auto_reps.append((m.start(), m.end(), yo_variant))
        elif word_e_lower in ambig_dict:
            yo_variant = restore_case(word, ambig_dict[word_e_lower])
            base_e = restore_case(word, word_e_lower)
            ambig_matches.append({
                "match": m, 
                "current_in_text": word, # слово как оно есть сейчас в файле
                "base_e": base_e,        # чистый вариант через 'е'
                "yo_variant": yo_variant # вариант с 'ё'
            })

    print(f"Словарь: {len(auto_reps)} замен (новых). Спорных (всего в книге): {len(ambig_matches)}")
    apply_and_save(text, auto_reps, output_file)
    
    if not ambig_matches: return

    batches =[]
    for i in range(0, len(ambig_matches), BATCH_SIZE):
        chunk = ambig_matches[i:i+BATCH_SIZE]
        reqs =[]
        for j, item in enumerate(chunk):
            raw_context = get_sentence_context(text, item["match"].start(), item["match"].end())
            # Нормализуем для LLM, чтобы она видела чистую 'е' в контексте
            context = raw_context.replace(f"<TARGET>{item['current_in_text']}</TARGET>", f"<TARGET>{item['base_e']}</TARGET>")
            reqs.append({
                "id": j, 
                "context": context, 
                "variants": [item["base_e"], item["yo_variant"]]
            })
        batches.append({"items": chunk, "requests": reqs})

    total_batches = len(batches)
    start_idx = max(0, min(start_batch - 1, total_batches - 1))

    if start_idx > 0:
        print(f"\n[ИНФО] Пропускаем первые {start_idx} батчей. Они останутся нетронутыми в файле {output_file}.")

    try:
        input(f"\n[Готово] Нажмите Enter для запуска llm (Батч {start_idx + 1}/{total_batches}) [Ctrl+C для выхода]...")
    except KeyboardInterrupt:
        print("\n[Выход] Отмена пользователем."); return

    approved_reps =[]
    futures = {}

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Предзаполняем конвейер
            initial_fill = min(start_idx + PREFETCH_QUEUE, total_batches)
            for i in range(start_idx, initial_fill):
                futures[i] = executor.submit(fetch_llm_response, batches[i]["requests"])

            for i in range(start_idx, total_batches):
                if not futures[i].done():
                    print(f"\r\033[96m[~] Локальная модель генерирует ответ {i+1}/{total_batches}...\033[0m", end="", flush=True)

                res_data = futures[i].result()
                print("\r" + " " * 70 + "\r", end="")
                
                # Собираем ответы от LLM (в словарь для быстрого доступа)
                llm_results = {res.get("id", idx): res.get("replacement", "") for idx, res in enumerate(res_data.get("results",[]))}
                
                batch_state = []
                for idx, item in enumerate(batches[i]["items"]):
                    base_e = item["base_e"]
                    ctx = batches[i]["requests"][idx]['context']
                    user_view = shrink_for_user(ctx)
                    
                    proposed = llm_results.get(idx, base_e)
                    if not proposed: # Fallback если модель вернула пустую строку
                        proposed = base_e
                        
                    batch_state.append({
                        "base_e": base_e,
                        "yo_variant": item["yo_variant"],
                        "current_in_text": item["current_in_text"],
                        "choice": proposed,
                        "match": item["match"],
                        "user_view": user_view
                    })

                while True:
                    print(f"\n{'='*60}")
                    print(f"Батч {i+1}/{total_batches} (вхождения {i*BATCH_SIZE + 1}-{min((i+1)*BATCH_SIZE, len(ambig_matches))})")
                    
                    current_batch_replacements = []
                    lines_to_print =[]
                    max_left_len = 0
                    
                    for idx, state in enumerate(batch_state):
                        base_e = state["base_e"]
                        choice = state["choice"]
                        current_in_text = state["current_in_text"]
                        user_view = state["user_view"]
                        
                        left_part, sep, right_part = user_view.partition(f"<TARGET>{base_e}</TARGET>")
                        if len(left_part) > max_left_len: 
                            max_left_len = len(left_part)
                        
                        # Сохраняем физически только если выбор не равен тому, что сейчас в файле
                        if choice != current_in_text:
                            current_batch_replacements.append((state["match"].start(), state["match"].end(), choice))
                            
                        # is_rep используется для зеленой/красной подсветки
                        is_rep = (choice != base_e and 'ё' in choice.lower())
                        
                        lines_to_print.append({
                            "idx": idx + 1,
                            "left": left_part, 
                            "choice": choice, 
                            "right": right_part, 
                            "is_rep": is_rep
                        })
                    
                    # Отрисовка
                    for line in lines_to_print:
                        padding = " " * (max_left_len - len(line["left"])) if ALIGN_TARGETS else ""
                        target = f"\033[92m[{line['choice']}]\033[0m" if line["is_rep"] else f"\033[91m[{line['choice']}]\033[0m"
                        bullet = "•" if line["is_rep"] else "◦"
                        
                        print(f"{line['idx']:2d} | {bullet} {padding}{line['left']}{target}{line['right']}")

                    ans = input(f"\n[Замен в батче: {len(current_batch_replacements)}] [Enter - ок, q - выход, НОМЕР(А) - инверсия]: ").strip().lower()
                    
                    if ans == 'q': 
                        os._exit(0)
                    elif ans == '': 
                        break
                    else:
                        parts = ans.split()
                        if all(p.isdigit() for p in parts):
                            for p in parts:
                                row_idx = int(p) - 1
                                if 0 <= row_idx < len(batch_state):
                                    st = batch_state[row_idx]
                                    st["choice"] = st["base_e"] if st["choice"] == st["yo_variant"] else st["yo_variant"]
                        else:
                            print("\033[91m[!] Ошибка ввода. Введите номера через пробел.\033[0m")
                    
                approved_reps.extend(current_batch_replacements)
                apply_and_save(text, auto_reps + approved_reps, output_file)
                
                next_idx = i + PREFETCH_QUEUE
                if next_idx < total_batches:
                    futures[next_idx] = executor.submit(fetch_llm_response, batches[next_idx]["requests"])
                del futures[i]

    except KeyboardInterrupt:
        print("\n\n[Выход] Прервано пользователем (Ctrl+C). Изменения сохранены."); os._exit(0)

    print(f"\n{'='*40}\nОбработка завершена. Файл: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Интеллектуальная расстановка буквы Ё (Локальная LLM).")
    parser.add_argument("-b", "--book", type=str, required=True, help="Имя входного файла (с .txt или без)")
    parser.add_argument("-s", "--start", type=int, default=1, help="Номер батча, с которого начать (по умолчанию 1)")
    
    args = parser.parse_args()
    
    fname = args.book if args.book.endswith(".txt") else f"{args.book}.txt"
    out = f"{os.path.splitext(fname)[0]}_yo.txt"
    
    process_text(fname, out, start_batch=args.start)