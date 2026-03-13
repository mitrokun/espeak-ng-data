############################################
#
# выбрать файл и задать нумерацию  
# python splitter.py -i my_audio.wav -s 10
# всё в папке, но начать нумерацию с 100:
# python splitter.py -s 100
#
#############################################

import os
import re
import argparse
from pydub import AudioSegment
from pydub.silence import split_on_silence

# ========= НАСТРОЙКИ =========
OUTPUT_DIR = "output_fragments"
MIN_LEN_MS = 4000
MAX_LEN_MS = 10000
MIN_SILENCE_LEN = 400
SILENCE_THRESH = -30
KEEP_SILENCE = 300
OUTPUT_FRAME_RATE = 22050
OUTPUT_SAMPLE_WIDTH = 2
# ==============================

def get_all_audio_files():
    """Находит все поддерживаемые аудиофайлы в текущей директории."""
    extensions = ('.mp3', '.wav', '.flac', '.ogg', '.m4a')
    return sorted([f for f in os.listdir('.') if f.lower().endswith(extensions)])

def get_start_index(directory: str, manual_index: int = None) -> int:
    """Определяет номер, с которого начнется сохранение файлов."""
    if manual_index is not None:
        return manual_index

    if not os.path.exists(directory):
        return 1

    max_num = 0
    pattern = re.compile(r'^(\d+)\.wav$')
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    return max_num + 1

def process_audio(input_file: str, start_index: int) -> int:
    if not os.path.exists(input_file):
        print(f"Ошибка: Файл {input_file} не найден.")
        return start_index

    print(f"\n--- Обработка: {input_file} ---")
    
    ext = os.path.splitext(input_file)[1][1:].lower()
    try:
        # Пытаемся загрузить файл (pydub сам определит формат, если установлен ffmpeg)
        audio = AudioSegment.from_file(input_file)
    except Exception as e:
        print(f"Ошибка при загрузке '{input_file}': {e}")
        return start_index

    raw_chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_LEN,
        silence_thresh=SILENCE_THRESH,
        keep_silence=KEEP_SILENCE
    )

    processed_chunks = []
    buffer_chunk = AudioSegment.empty()

    for chunk in raw_chunks:
        if len(chunk) > MAX_LEN_MS:
            if len(buffer_chunk) >= MIN_LEN_MS:
                processed_chunks.append(buffer_chunk)
                buffer_chunk = AudioSegment.empty()
            processed_chunks.append(chunk)
            continue

        if len(buffer_chunk) + len(chunk) <= MAX_LEN_MS:
            buffer_chunk += chunk
        else:
            if len(buffer_chunk) >= MIN_LEN_MS:
                processed_chunks.append(buffer_chunk)
                buffer_chunk = chunk
            else:
                buffer_chunk += chunk

    if len(buffer_chunk) > 0:
        if len(buffer_chunk) < MIN_LEN_MS and processed_chunks:
            processed_chunks[-1] += buffer_chunk
        else:
            processed_chunks.append(buffer_chunk)

    for i, chunk in enumerate(processed_chunks):
        file_index = start_index + i
        filename = f"{file_index:03d}.wav"
        filepath = os.path.join(OUTPUT_DIR, filename)

        chunk = chunk.set_frame_rate(OUTPUT_FRAME_RATE).set_sample_width(OUTPUT_SAMPLE_WIDTH).set_channels(1)
        chunk.export(filepath, format="wav")
        print(f"  Экспортировано: {filename} ({len(chunk)/1000:.2f} сек)")

    return start_index + len(processed_chunks)

def main():
    # Настройка парсера аргументов
    parser = argparse.ArgumentParser(description="Скрипт для нарезки аудио на сегменты для датасета.")
    parser.add_argument("-i", "--input", type=str, help="Путь к конкретному аудиофайлу. Если не указан, обработает все файлы в папке.")
    parser.add_argument("-s", "--start", type=int, help="Начальный порядковый номер для именования файлов (например, 4 -> 004.wav).")
    
    args = parser.parse_args()

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # Определяем список файлов
    if args.input:
        files_to_run = [args.input]
    else:
        files_to_run = get_all_audio_files()
        if not files_to_run:
            print("Аудиофайлы не найдены в текущей директории.")
            return

    # Определяем стартовый индекс
    current_index = get_start_index(OUTPUT_DIR, args.start)
    
    print(f"Начинаем нумерацию с: {current_index:03d}")
    print(f"Файлов к обработке: {len(files_to_run)}")

    for file in files_to_run:
        current_index = process_audio(file, current_index)

    print("\nГотово!")

if __name__ == "__main__":
    main()