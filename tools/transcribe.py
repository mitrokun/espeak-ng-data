import os
import csv
import onnx_asr
import re
import argparse
import difflib
from tqdm import tqdm

# --- НАСТРОЙКИ ПО УМОЛЧАНИЮ ---
DEFAULT_INPUT_FOLDER = "output_fragments"
DEFAULT_OUTPUT_FILENAME = "metadata.csv" # Имя файла внутри папки

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
print("Загрузка моделей GigaAM...")
model_ctc = onnx_asr.load_model("gigaam-v3-ctc")
model_e2e = onnx_asr.load_model("gigaam-v3-e2e-rnnt")

def is_number_or_roman(word):
    clean = re.sub(r'[^\w]', '', word)
    if any(c.isdigit() for c in clean):
        return True
    if re.fullmatch(r'[IVXLCDM]+', clean):
        return True
    return False

def clean_word(word):
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
            if any(is_number_or_roman(w) for w in e2e_chunk) and ctc_chunk:
                punc_match = re.search(r'[^\w\s]+$', e2e_chunk[-1])
                if punc_match:
                    ctc_chunk[-1] += punc_match.group()
                if e2e_chunk[0][0].isupper():
                    ctc_chunk[0] = ctc_chunk[0].capitalize()
                final_tokens.extend(ctc_chunk)
            else:
                final_tokens.extend(e2e_chunk)
        elif tag == 'insert':
            final_tokens.extend(ctc_words[j1:j2])
        elif tag == 'delete':
            final_tokens.extend(e2e_words[i1:i2])

    result = " ".join(final_tokens)
    result = re.sub(r'[₽$€%]', '', result)
    result = re.sub(r'([—–])', r' \1 ', result)
    result = re.sub(r'\s+([,.!?])', r'\1', result)
    result = " ".join(result.split())
    
    return result

def main():
    parser = argparse.ArgumentParser(description="Распознавание аудио в папке.")
    parser.add_argument("-i", "--input", type=str, default=DEFAULT_INPUT_FOLDER, 
                        help=f"Папка с аудио (по умолчанию: {DEFAULT_INPUT_FOLDER})")
    parser.add_argument("-o", "--output", type=str, default=DEFAULT_OUTPUT_FILENAME, 
                        help=f"Имя CSV файла внутри целевой папки (по умолчанию: {DEFAULT_OUTPUT_FILENAME})")
    
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Ошибка: Папка '{args.input}' не найдена!")
        return

    # Формируем полный путь к CSV файлу ВНУТРИ целевой папки
    full_output_path = os.path.join(args.input, args.output)

    # Поиск и сортировка файлов
    wav_files = [f for f in os.listdir(args.input) if f.endswith(".wav")]
    # Сортировка по числам в именах (чтобы 2.wav было перед 10.wav)
    wav_files.sort(key=lambda f: int(re.sub(r'\D', '', f) if re.sub(r'\D', '', f) else 0))

    if not wav_files:
        print(f"В папке '{args.input}' не найдено .wav файлов.")
        return

    print(f"Обработка {len(wav_files)} файлов...")
    print(f"Результат будет сохранен в: {full_output_path}")

    with open(full_output_path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='|')

        for filename in tqdm(wav_files, desc="Распознавание"):
            file_path = os.path.join(args.input, filename)
            
            try:
                raw_ctc = model_ctc.recognize(file_path)
                raw_e2e = model_e2e.recognize(file_path)
                
                raw_ctc = " ".join(raw_ctc.split())
                raw_e2e = " ".join(raw_e2e.split())
                
                result = merge_transcriptions(raw_ctc, raw_e2e)

                # В CSV записываем только имя файла и текст
                writer.writerow([filename, result])
                
            except Exception as e:
                print(f"\nОшибка в файле {filename}: {e}")

    print(f"\nЗавершено! Файл создан: {full_output_path}")

if __name__ == "__main__":
    main()