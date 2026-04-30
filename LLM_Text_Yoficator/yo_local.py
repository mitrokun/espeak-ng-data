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

BATCH_SIZE = 15
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

def process_text(input_file, output_file):
    abs_dict, ambig_dict = load_dictionary(DICT_FILE)
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Ошибка файла: {e}"); return

    print(f"Анализ '{input_file}'...")
    word_pattern = re.compile(r'[А-Яа-яЁё]+')
    auto_reps, ambig_matches = [],[]
    
    for m in word_pattern.finditer(text):
        word = m.group(0)
        if 'ё' in word.lower(): continue
        w_low = word.lower()
        if w_low in abs_dict:
            auto_reps.append((m.start(), m.end(), restore_case(word, abs_dict[w_low])))
        elif w_low in ambig_dict:
            ambig_matches.append({"match": m, "orig": word, "yo": restore_case(word, ambig_dict[w_low])})

    print(f"Словарь: {len(auto_reps)} замен. Спорных: {len(ambig_matches)}")
    apply_and_save(text, auto_reps, output_file)
    
    if not ambig_matches: return

    try:
        input("\n[Готово] Нажмите Enter для запуска llm (Ctrl+C для выхода)...")
    except KeyboardInterrupt:
        print("\n[Выход] Отмена пользователем."); return

    batches = []
    for i in range(0, len(ambiguous_matches := ambig_matches), BATCH_SIZE):
        chunk = ambiguous_matches[i:i+BATCH_SIZE]
        reqs = [{"id": j, "context": get_sentence_context(text, item["match"].start(), item["match"].end()), 
                 "variants": [item["orig"], item["yo"]]} for j, item in enumerate(chunk)]
        batches.append({"items": chunk, "requests": reqs})

    approved_reps = []
    futures = {}

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            initial_batches = min(PREFETCH_QUEUE, len(batches))
            for i in range(initial_batches):
                futures[i] = executor.submit(fetch_llm_response, batches[i]["requests"])

            for i in range(len(batches)):
                if not futures[i].done():
                    print(f"\r\033[96m[~] Локальная модель генерирует ответ {i+1}/{len(batches)}...\033[0m", end="", flush=True)

                res_data = futures[i].result()
                print("\r" + " " * 70 + "\r", end="")
                
                print(f"\n{'='*60}")
                print(f"Батч {i+1}/{len(batches)} (вхождения {i*BATCH_SIZE + 1}-{min((i+1)*BATCH_SIZE, len(ambiguous_matches))})")
                
                batch_reps = []
                lines_to_print = []
                max_left_len = 0
                
                for idx, res in enumerate(res_data.get("results",[])):
                    req_id = res.get("id", idx)
                    if req_id >= len(batches[i]["items"]): continue
                    
                    proposed = res.get("replacement", "")
                    item = batches[i]["items"][req_id]
                    ctx = batches[i]["requests"][req_id]['context']
                    
                    user_view = shrink_for_user(ctx)
                    left_part, sep, right_part = user_view.partition(f"<TARGET>{item['orig']}</TARGET>")
                    
                    if len(left_part) > max_left_len: max_left_len = len(left_part)
                    
                    is_rep = (item["orig"] != proposed and 'ё' in proposed.lower())
                    if is_rep: batch_reps.append((item["match"].start(), item["match"].end(), proposed))
                    
                    lines_to_print.append({
                        "left": left_part, 
                        "orig": item["orig"], 
                        "prop": proposed, 
                        "right": right_part, 
                        "is_rep": is_rep
                    })
                
                for line in lines_to_print:
                    # Выбираем способ выравнивания
                    padding = " " * (max_left_len - len(line["left"])) if ALIGN_TARGETS else ""
                    
                    target = f"\033[92m[{line['prop']}]\033[0m" if line["is_rep"] else f"\033[91m[{line['orig']}]\033[0m"
                    bullet = "•" if line["is_rep"] else "◦"
                    
                    print(f"{bullet} {padding}{line['left']}{target}{line['right']}")

                ans = input(f"\n[Замен: {len(batch_reps)}] Принять? [Enter - да, q - выход]: ").strip().lower()
                if ans == 'q': os._exit(0)
                    
                approved_reps.extend(batch_reps)
                apply_and_save(text, auto_reps + approved_reps, output_file)
                
                next_idx = i + PREFETCH_QUEUE
                if next_idx < len(batches):
                    futures[next_idx] = executor.submit(fetch_llm_response, batches[next_idx]["requests"])
                del futures[i]

    except KeyboardInterrupt:
        print("\n\n[Выход] Прервано пользователем (Ctrl+C). Изменения сохранены."); os._exit(0)

    print(f"\n{'='*40}\nОбработка завершена. Файл: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--b", type=str, required=True, help="Имя файла")
    args = parser.parse_args()
    
    fname = args.b if args.b.endswith(".txt") else f"{args.b}.txt"
    out = f"{os.path.splitext(fname)[0]}_yo.txt"
    process_text(fname, out)