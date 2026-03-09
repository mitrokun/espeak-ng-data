import os
import csv
import onnx_asr
import re
from tqdm import tqdm
import difflib

# --- НАСТРОЙКИ ---
INPUT_FOLDER = "."
OUTPUT_FILE = "metadata.csv"
DEBUG_MODE = True 

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
print("Загрузка моделей...")
model_ctc = onnx_asr.load_model("gigaam-v3-ctc")
model_e2e = onnx_asr.load_model("gigaam-v3-e2e-ctc")

def is_number_or_roman(word):
    """Проверяет, содержит ли токен цифры или является ли римской цифрой"""
    clean = re.sub(r'[^\w]', '', word)
    if any(c.isdigit() for c in clean):
        return True
    if re.fullmatch(r'[IVXLCDM]+', clean):
        return True
    return False

def clean_word(word):
    """Очистка для сравнения"""
    return re.sub(r'[^\w]', '', word).lower()

def merge_transcriptions(text_ctc, text_e2e):
    ctc_words = text_ctc.split()
    e2e_words = text_e2e.split()
    
    ctc_match_base = [clean_word(w) for w in ctc_words]
    e2e_match_base = [clean_word(w) for w in e2e_words]
    
    matcher = difflib.SequenceMatcher(None, e2e_match_base, ctc_match_base)
    final_tokens = []
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            final_tokens.extend(e2e_words[i1:i2])
            
        elif tag == 'replace':
            e2e_chunk = e2e_words[i1:i2]
            ctc_chunk = ctc_words[j1:j2]
            
            # Если в E2E число/римское — берем CTC версию (прописью)
            if any(is_number_or_roman(w) for w in e2e_chunk):
                punc_match = re.search(r'[^\w\s]+$', e2e_chunk[-1])
                if punc_match:
                    ctc_chunk[-1] += punc_match.group()
                
                if e2e_chunk[0][0].isupper():
                    ctc_chunk[0] = ctc_chunk[0].capitalize()
                final_tokens.extend(ctc_chunk)
            else:
                # В остальных случаях берем E2E (Ё, регистр, орфография)
                final_tokens.extend(e2e_chunk)
                
        elif tag == 'insert':
            final_tokens.extend(ctc_words[j1:j2])
            
        elif tag == 'delete':
            final_tokens.extend(e2e_words[i1:i2])

    result = " ".join(final_tokens)
    
    # --- ПОСТ-ОБРАБОТКА (ЧИСТКА ТЕКСТА) ---
    
    # 1. Убираем знаки валют
    result = re.sub(r'[₽$€]', '', result)
    
    # 2. Исправляем тире: "Слово—слово" -> "Слово — слово"
    # Находим длинное или среднее тире и добавляем пробелы, если их нет
    result = re.sub(r'([^ ])([—–])', r'\1 \2', result) # пробел перед тире
    result = re.sub(r'([—–])([^ ])', r'\2 \2', result) # пробел после тире (опечатка исправлена ниже)
    # Корректная версия замены для тире:
    result = re.sub(r'([—–])', r' \1 ', result)
    
    # 3. Убираем лишние пробелы перед знаками препинания
    result = re.sub(r'\s+([,.!?])', r'\1', result)
    
    # 4. Схлопываем двойные пробелы, которые могли возникнуть после правок тире
    result = " ".join(result.split())
    
    return result

# --- ОСНОВНОЙ ЦИКЛ ---
wav_files = [f for f in os.listdir(INPUT_FOLDER) if f.endswith(".wav")]
print(f"Найдено файлов: {len(wav_files)}")

with open(OUTPUT_FILE, mode='w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f, delimiter='|')

    for filename in tqdm(wav_files, desc="Распознавание"):
        file_path = os.path.join(INPUT_FOLDER, filename)
        
        try:
            raw_ctc = model_ctc.recognize(file_path)
            raw_e2e = model_e2e.recognize(file_path)
            
            # Убираем лишние переносы и пробелы из сырых данных
            raw_ctc = " ".join(raw_ctc.split())
            raw_e2e = " ".join(raw_e2e.split())
            
            result = merge_transcriptions(raw_ctc, raw_e2e)
            
            if DEBUG_MODE:
                print(f"\nФайл: {filename}")
                print(f" CTC: {raw_ctc}")
                print(f" E2E: {raw_e2e}")
                print(f" RES: {result}")
                print("-" * 30)

            writer.writerow([filename, result])
            
        except Exception as e:
            print(f"\nОшибка в файле {filename}: {e}")

print(f"\nГотово! Результаты сохранены в {OUTPUT_FILE}")