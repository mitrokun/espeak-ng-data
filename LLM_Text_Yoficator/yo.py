import os
# Подавляем системный спам gRPC
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"

import re
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel

from google import genai
from google.genai import types

# ================= НАСТРОЙКИ =================
API_KEY = "ВАШ КЛЮЧ ДЛЯ GEMINI"
MODEL_NAME = "gemini-3.1-flash-lite-preview" 
BATCH_SIZE = 20
MAX_CONTEXT_LEN = 300  # Лимит длины предложения
DISPLAY_WINDOW = 4     # Количесто слов вокруг целевого в выводе

# Буфер: 1 батч на экране + N запросов в фоне
PREFETCH_QUEUE = 4 

DICT_FILE = "yox.txt"
# =============================================

client = genai.Client(api_key=API_KEY)

class ReplacementDecision(BaseModel):
    id: int
    replacement: str

class BatchResponse(BaseModel):
    results: list[ReplacementDecision]

def load_dictionary(filepath):
    abs_dict = {}
    ambig_dict = {}
    
    print(f"Загрузка словаря из {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                is_ambiguous = line.endswith('?')
                word_yo = line.rstrip('?')
                word_e = word_yo.replace('ё', 'е').replace('Ё', 'Е')
                
                if is_ambiguous:
                    ambig_dict[word_e] = word_yo
                else:
                    abs_dict[word_e] = word_yo
                    
        print(f"Словарь загружен: {len(abs_dict)} автозамен, {len(ambig_dict)} спорных.")
    except FileNotFoundError:
        print(f"\n[ВНИМАНИЕ] Файл словаря {filepath} не найден. Работа только через нейросеть.")
        
    return abs_dict, ambig_dict

def restore_case(original_word: str, target_yo_word: str) -> str:
    if original_word.isupper(): return target_yo_word.upper()
    elif original_word.istitle(): return target_yo_word.capitalize()
    else: return target_yo_word.lower()

def get_sentence_context(text: str, start: int, end: int) -> str:
    left_bound = max(
        text.rfind('.', 0, start),
        text.rfind('!', 0, start),
        text.rfind('?', 0, start),
        text.rfind('…', 0, start),
        text.rfind('\n', 0, start)
    )
    left_bound = left_bound + 1 if left_bound != -1 else 0
    
    right_bound = -1
    for punct in['.', '!', '?', '…', '\n']:
        idx = text.find(punct, end)
        if idx != -1:
            if right_bound == -1 or idx < right_bound:
                right_bound = idx
    right_bound = right_bound + 1 if right_bound != -1 else len(text)
    
    if start - left_bound > MAX_CONTEXT_LEN: left_bound = start - MAX_CONTEXT_LEN
    if right_bound - end > MAX_CONTEXT_LEN: right_bound = end + MAX_CONTEXT_LEN
    
    left_part = text[left_bound:start].lstrip()
    right_part = text[end:right_bound].rstrip()
    target = text[start:end]
    
    return f"{left_part}<TARGET>{target}</TARGET>{right_part}"

def shrink_for_user(ctx_str: str, window=DISPLAY_WINDOW) -> str:
    """Оставляет только N слов вокруг тега <TARGET> для визуального вывода."""
    words = ctx_str.split()
    target_idx = next((i for i, w in enumerate(words) if "<TARGET>" in w), None)
    if target_idx is None: 
        return ctx_str
    
    start = max(0, target_idx - window)
    end = target_idx + window + 1
    
    snippet = " ".join(words[start:end])
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(words) else ""
    return f"{prefix}{snippet}{suffix}"

def fetch_llm_response(batch_requests: list, retries=4) -> dict:
    prompt = (
        "Ты профессиональный корректор русского языка. В предоставленном JSON массиве находятся предложения. "
        "В каждом предложении есть целевое слово, обернутое в теги <TARGET>...</TARGET>. "
        "В поле `variants` даны два варианта написания этого слова (через 'е' и через 'ё'). "
        "Твоя задача — определить по смыслу предложения, какой из вариантов правильный. "
        "Верни JSON массив с выбранными словами в строгом соответствии с регистром исходного слова.\n\n"
        f"{json.dumps(batch_requests, ensure_ascii=False)}"
    )

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BatchResponse,
                    temperature=0.0
                )
            )
            return json.loads(response.text)
        except Exception as e:
            if attempt < retries - 1:
                # Пауза на случай Rate Limits (429 Too Many Requests)
                time.sleep(3 + attempt * 2) 
            else:
                print(f"\n[Ошибка API] Батч пропущен: {e}")
                return {"results":[]}

def apply_and_save(original_text: str, replacements: list, output_file: str, silent=False):
    if not replacements:
        return
        
    replacements.sort(key=lambda x: x[0], reverse=True)
    
    modified_text = original_text
    for start, end, new_word in replacements:
        modified_text = modified_text[:start] + new_word + modified_text[end:]
        
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(modified_text)
        
    if not silent:
        print(f"\nГотово! Результат сохранен в {output_file} (замен: {len(replacements)})")

def process_text(input_file: str, output_file: str):
    abs_dict, ambig_dict = load_dictionary(DICT_FILE)
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        print(f"\n[ОШИБКА] Файл '{input_file}' не найден!")
        return

    print(f"\nАнализ текста '{input_file}'...")
    word_pattern = re.compile(r'[А-Яа-яЁё]+')
    
    auto_replacements =[]
    ambiguous_matches =[]
    
    for match in word_pattern.finditer(text):
        word = match.group(0)
        if 'ё' in word.lower():
            continue
            
        word_lower = word.lower()
        if word_lower in abs_dict:
            yo_variant = restore_case(word, abs_dict[word_lower])
            auto_replacements.append((match.start(), match.end(), yo_variant))
            continue
            
        if word_lower in ambig_dict:
            yo_variant = restore_case(word, ambig_dict[word_lower])
            ambiguous_matches.append({
                "match": match,
                "original": word,
                "yo_variant": yo_variant
            })

    print(f"Найдено абсолютных замен по словарю: {len(auto_replacements)}")
    print(f"Найдено спорных мест для нейросети: {len(ambiguous_matches)}")
    
    apply_and_save(text, auto_replacements, output_file, silent=True)
    
    if not ambiguous_matches:
        print(f"Готово! Результат сохранен в {output_file}")
        return

    # === ПАУЗА ПЕРЕД ЗАПУСКОМ API ===
    input("\n[Анализ завершен] Нажмите Enter, чтобы запустить запросы к Cloud API...")

    batches =[]
    for i in range(0, len(ambiguous_matches), BATCH_SIZE):
        batch_items = ambiguous_matches[i:i+BATCH_SIZE]
        batch_requests =[]
        for idx, item in enumerate(batch_items):
            m = item["match"]
            context = get_sentence_context(text, m.start(), m.end())
            batch_requests.append({
                "id": idx,
                "context": context,
                "variants": [item["original"], item["yo_variant"]]
            })
        batches.append({"items": batch_items, "requests": batch_requests})

    llm_approved_replacements =[]
    futures = {}

    print(f"\nЗапуск конвейера (Окно: {PREFETCH_QUEUE} батчей)...")

    # Здесь оставляем max_workers=PREFETCH_QUEUE, так как серверы Google хорошо параллелят запросы
    with ThreadPoolExecutor(max_workers=PREFETCH_QUEUE) as executor:
        
        # 1. Заполняем "стартовое окно" запросами к API
        initial_batches = min(PREFETCH_QUEUE, len(batches))
        for i in range(initial_batches):
            futures[i] = executor.submit(fetch_llm_response, batches[i]["requests"])

        # 2. Идем по всем батчам по очереди
        for i in range(len(batches)):
            
            if not futures[i].done():
                print(f"\r\033[96m[~] Ожидание ответа от API для батча {i+1}/{len(batches)}...\033[0m", end="", flush=True)

            # Получаем результат (если батч еще не готов - ждем)
            response_data = futures[i].result()
            
            # Стираем статус ожидания
            print("\r" + " " * 60 + "\r", end="")
            
            print(f"\n{'='*60}")
            print(f"Батч {i+1}/{len(batches)} (вхождения {i*BATCH_SIZE + 1}-{min((i+1)*BATCH_SIZE, len(ambiguous_matches))})")
            
            batch_replacements =[]
            
            for res in response_data.get("results", []):
                req_id = res["id"]
                proposed_word = res["replacement"]
                
                original_word = batches[i]["items"][req_id]["original"]
                ctx = batches[i]["requests"][req_id]['context']
                
                # Применяем короткую обрезку для пользователя
                user_view = shrink_for_user(ctx)
                
                if original_word != proposed_word and 'ё' in proposed_word.lower():
                    display_ctx = user_view.replace(f"<TARGET>{original_word}</TARGET>", f"\033[92m[{proposed_word}]\033[0m")
                    print(f"• {display_ctx}")
                    match = batches[i]["items"][req_id]["match"]
                    batch_replacements.append((match.start(), match.end(), proposed_word))
                else:
                    display_ctx = user_view.replace(f"<TARGET>{original_word}</TARGET>", f"\033[91m[{original_word}]\033[0m")
                    print(f"◦ {display_ctx}")

            ans = input(f"\n[Замен в батче: {len(batch_replacements)}] Принять? [Enter - да, q - выход]: ").strip().lower()
            
            if ans == 'q':
                print("\nПрерывание пользователем... Отмена фоновых задач.")
                for f in futures.values():
                    f.cancel()
                break
                
            llm_approved_replacements.extend(batch_replacements)
            apply_and_save(text, auto_replacements + llm_approved_replacements, output_file, silent=True)
            print(f"\033[90m[*] Прогресс сохранен...\033[0m")

            # 3. Добавляем в очередь следующий батч
            next_idx = i + PREFETCH_QUEUE
            if next_idx < len(batches):
                futures[next_idx] = executor.submit(fetch_llm_response, batches[next_idx]["requests"])

            del futures[i]

    print(f"\n{'='*60}")
    apply_and_save(text, auto_replacements + llm_approved_replacements, output_file, silent=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Интеллектуальная расстановка буквы Ё в текстах (Google Gemini).")
    parser.add_argument("--b", type=str, required=True, help="Имя текстового файла (например: --b 1984 или --b book.txt)")
    args = parser.parse_args()
    
    input_filename = args.b if args.b.endswith(".txt") else f"{args.b}.txt"
    base, ext = os.path.splitext(input_filename)
    output_filename = f"{base}_yo{ext}"
    
    process_text(input_filename, output_filename)