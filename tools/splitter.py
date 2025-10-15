import os
import re
from pydub import AudioSegment
from pydub.silence import split_on_silence

# === ОСНОВНЫЕ НАСТРОЙКИ ===
# Добавьте сюда все файлы, которые нужно обработать, в нужном порядке.
FILES_TO_PROCESS = [
    #"Nravstvennye_pisma_002.mp3",
    #"Nravstvennye_pisma_003.mp3",
    "g.wav"
]

OUTPUT_DIR = "output_fragments"
MIN_LEN_MS = 4000   # Минимальная длина (5 секунд = 5000 мс)
MAX_LEN_MS = 9000  # Максимальная длина (11 секунд = 11000 мс)

# Настройки для определения тишины (могут требовать подстройки)
MIN_SILENCE_LEN = 500
SILENCE_THRESH = -48
KEEP_SILENCE = 270

# Настройки выходного файла
OUTPUT_FRAME_RATE = 22050
OUTPUT_SAMPLE_WIDTH = 2  # 2 байта = 16 бит
# ==========================

def get_start_index(directory: str) -> int:
    """
    Сканирует папку, находит максимальный номер файла и возвращает следующий.
    Например, если есть 042.wav, вернет 43. Если папка пуста, вернет 1.
    """
    if not os.path.exists(directory):
        print(f"Папка '{directory}' не найдена, нумерация начнется с 1.")
        return 1

    max_num = 0
    # Регулярное выражение для поиска файлов вида "001.wav", "123.wav" и т.д.
    pattern = re.compile(r'^(\d{3,})\.wav$')

    print(f"Сканирование папки '{directory}' для определения начального номера...")
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num

    start_index = max_num + 1
    print(f"Нумерация начнется с {start_index:03d}.wav")
    return start_index

def process_audio(input_file: str, start_index: int) -> int:
    """
    Обрабатывает один аудиофайл и возвращает следующий доступный индекс для нумерации.
    """
    if not os.path.exists(input_file):
        print(f"Ошибка: Файл {input_file} не найден. Пропускаем.")
        return start_index

    print(f"Загрузка файла {input_file}...")
    try:
        audio = AudioSegment.from_mp3(input_file)
    except Exception as e:
        print(f"Ошибка при загрузке MP3 '{input_file}'. Убедитесь, что FFmpeg установлен. Ошибка: {e}")
        return start_index

    print("Анализ файла и поиск пауз...")
    raw_chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_LEN,
        silence_thresh=SILENCE_THRESH,
        keep_silence=KEEP_SILENCE
    )

    print(f"Найдено {len(raw_chunks)} фрагментов, разделенных паузами.")
    print(f"Обработка фрагментов под требования длины ({MIN_LEN_MS/1000}-{MAX_LEN_MS/1000} сек)...")

    processed_chunks = []
    buffer_chunk = AudioSegment.empty()

    for chunk in raw_chunks:
        # Пропускаем слишком длинные фрагменты, как и раньше
        if len(chunk) > MAX_LEN_MS:
            if len(buffer_chunk) > MIN_LEN_MS:
                processed_chunks.append(buffer_chunk)
            buffer_chunk = AudioSegment.empty()
            print(f"Внимание: Фрагмент длиной {len(chunk)/1000:.2f}с превышает лимит, но сохранен целиком.")
            processed_chunks.append(chunk)
            continue

        # Если буфер пустой, просто начинаем его с текущего фрагмента
        if not buffer_chunk:
            buffer_chunk = chunk
            continue
        
        # === НОВАЯ УЛУЧШЕННАЯ ЛОГИКА ===
        # Если добавление нового фрагмента не превысит максимум
        if len(buffer_chunk) + len(chunk) <= MAX_LEN_MS:
            buffer_chunk += chunk
        # Если превысит...
        else:
            # ...проверяем, достаточно ли длинен текущий буфер для сохранения
            if len(buffer_chunk) >= MIN_LEN_MS:
                # Да, он соответствует минимуму. Сохраняем его.
                processed_chunks.append(buffer_chunk)
                # Начинаем новый буфер с текущего фрагмента
                buffer_chunk = chunk
            else:
                # Нет, буфер слишком короткий. Вынужденно добавляем фрагмент,
                # жертвуя максимальной длиной ради соблюдения минимальной.
                buffer_chunk += chunk
    
    # === НОВАЯ ЛОГИКА ОБРАБОТКИ "ХВОСТА" ===
    if len(buffer_chunk) > 0:
        # Если последний буфер слишком короткий И есть куда его присоединить
        if len(buffer_chunk) < MIN_LEN_MS and processed_chunks:
            print(f"Короткий остаток ({len(buffer_chunk)/1000:.2f}с) присоединен к предыдущему фрагменту.")
            # Добавляем его к последнему сохраненному фрагменту
            processed_chunks[-1] += buffer_chunk
        else:
            # В противном случае сохраняем как есть (если он не короче минимума или если он единственный)
            processed_chunks.append(buffer_chunk)

    print(f"Будет создано {len(processed_chunks)} файлов для {input_file}.")
    print("Экспорт файлов...")

    for i, chunk in enumerate(processed_chunks):
        # Дополнительная проверка на случай, если какой-то фрагмент все же оказался короче
        if len(chunk) < MIN_LEN_MS:
             print(f"ПРЕДУПРЕЖДЕНИЕ: Файл {start_index + i:03d}.wav будет короче минимума ({len(chunk)/1000:.2f}c).")

        file_index = start_index + i
        filename = f"{file_index:03d}.wav"
        filepath = os.path.join(OUTPUT_DIR, filename)

        chunk = chunk.set_frame_rate(OUTPUT_FRAME_RATE).set_sample_width(OUTPUT_SAMPLE_WIDTH)

        print(f"Сохранение {filename} (длина: {len(chunk)/1000:.2f}с)")
        chunk.export(filepath, format="wav")

    return start_index + len(processed_chunks)

def main():
    # Создаем папку для выходных файлов, если её нет
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # Определяем, с какого номера начинать нумерацию
    current_index = get_start_index(OUTPUT_DIR)

    # Последовательно обрабатываем каждый файл из списка
    for file in FILES_TO_PROCESS:
        print(f"\n{'='*15} НАЧАЛО ОБРАБОТКИ: {file} {'='*15}")
        current_index = process_audio(file, current_index)
        print(f"{'='*15} ЗАВЕРШЕНИЕ ОБРАБОТКИ: {file} {'='*15}")

    print("\nВсе файлы обработаны!")

if __name__ == "__main__":
    main()