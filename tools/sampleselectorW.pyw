import os
import re
import time
import queue
import asyncio
import threading
import pygame
import tkinter as tk
from tkinter import filedialog, messagebox

# --- Импорты для TTS ---
try:
    import pyaudio
    from wyoming.client import AsyncTcpClient
    from wyoming.tts import (
        SynthesizeStart,
        SynthesizeChunk,
        SynthesizeStop,
        SynthesizeVoice,
        SynthesizeStopped
    )
    from wyoming.audio import AudioChunk
    TTS_AVAILABLE = True
except ImportError as e:
    TTS_AVAILABLE = False
    print(f"Внимание: Модули для TTS не найдены ({e}). Функция синтеза будет отключена.")

# --- КОНСТАНТЫ TTS ---
AUDIO_STOP = object()
AUDIO_FINISHED = object() # <--- Маркер окончания звука
SAMPLE_RATE = 22050
SAMPLE_WIDTH = 2
CHANNELS = 1
PLAYER_CHUNK_SIZE = 1024

if TTS_AVAILABLE:
    class AudioPlayer:
        # Добавили on_finished для коллбэка
        def __init__(self, audio_queue, on_finished=None):
            self.queue = audio_queue
            self.on_finished = on_finished
            self.abort_flag = threading.Event()
            self.resume_event = threading.Event()
            self.resume_event.set()
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

        def stop_immediate(self):
            self.abort_flag.set()
            self.resume_event.set()

        def reset(self):
            self.abort_flag.clear()
            self.resume_event.set()

        def pause(self):
            self.resume_event.clear()

        def resume(self):
            self.resume_event.set()

        def _worker(self):
            p = pyaudio.PyAudio()
            stream = None
            
            while True:
                chunk_data = self.queue.get()
                
                # Ловим маркер успешного окончания
                if chunk_data is AUDIO_FINISHED:
                    if self.on_finished:
                        self.on_finished()
                    continue
                
                if chunk_data is AUDIO_STOP:
                    if stream:
                        stream.stop_stream()
                        stream.close()
                        stream = None
                    continue
                
                if stream is None:
                    stream = p.open(format=pyaudio.paInt16, 
                                    channels=CHANNELS, 
                                    rate=SAMPLE_RATE, 
                                    output=True)
                
                total_len = len(chunk_data)
                cursor = 0
                
                while cursor < total_len:
                    if self.abort_flag.is_set(): break
                    self.resume_event.wait()
                    if self.abort_flag.is_set(): break

                    end = min(cursor + PLAYER_CHUNK_SIZE, total_len)
                    small_chunk = chunk_data[cursor:end]
                    
                    try:
                        if stream.is_active():
                            stream.write(small_chunk)
                    except: break
                    
                    cursor = end

                if self.abort_flag.is_set():
                    if stream:
                        stream.stop_stream()
                        stream.close()
                        stream = None


    class AsyncWyomingManager:
        def __init__(self, host, port, audio_queue, player):
            self.host = host
            self.port = port
            self.audio_queue = audio_queue
            self.player = player
            self._thread = None
            self._stop_event = None

        def start_synthesis(self, text, voice_name):
            self.stop()
            self.player.reset()
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_async_loop, 
                args=(text, voice_name), 
                daemon=True
            )
            self._thread.start()

        def stop(self):
            if self._stop_event:
                self._stop_event.set()
            self.player.stop_immediate()
            with self.audio_queue.mutex:
                self.audio_queue.queue.clear()
            self.audio_queue.put(AUDIO_STOP)

        def _run_async_loop(self, text, voice_name):
            try:
                asyncio.run(self._async_session(text, voice_name))
            except Exception as e:
                print(f"Ошибка Async Loop: {e}")

        async def _async_session(self, text, voice_name):
            try:
                async with AsyncTcpClient(self.host, self.port) as client:
                    voice = SynthesizeVoice(name=voice_name)
                    await client.write_event(SynthesizeStart(voice=voice).event())
                    
                    send_task = asyncio.create_task(self._send_chunks(client, text))
                    read_task = asyncio.create_task(self._read_events(client))
                    
                    await asyncio.gather(send_task, read_task)
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"Ошибка TTS сессии: {e}")

        async def _send_chunks(self, client, text):
            chunks = re.split(r'([.,!?;:\n]+)', text)
            to_send =[]
            current = ""
            for c in chunks:
                current += c
                if len(current) > 30 or (c and c in ".,!?;:\n"):
                    to_send.append(current)
                    current = ""
            if current: to_send.append(current)

            for chunk in to_send:
                if self._stop_event.is_set(): raise asyncio.CancelledError
                if not chunk.strip(): continue
                await client.write_event(SynthesizeChunk(text=chunk).event())
                await asyncio.sleep(0.01) 

            await client.write_event(SynthesizeStop().event())

        async def _read_events(self, client):
            while not self._stop_event.is_set():
                event = await client.read_event()
                if event is None: break
                if AudioChunk.is_type(event.type):
                    chunk = AudioChunk.from_event(event)
                    self.audio_queue.put(chunk.audio)
                elif SynthesizeStopped.is_type(event.type):
                    # Сообщаем плееру, что синтез полностью завершен
                    self.audio_queue.put(AUDIO_FINISHED)
                    break


class AudioCleaner:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Dataset Cleaner")
        self.root.geometry("700x420")
        self.root.minsize(450, 420)
        
        # --- НАСТРОЙКИ ИНТЕРФЕЙСА ---
        self.FONT_SIZE = 16
        self.BG_COLOR = "#131313"
        self.FG_COLOR = "#26A269"
        self.FG_COLOR2 = "#135240"
        self.PANEL_BG = "#1e1e1e"
        self.ERR_COLOR = "#cc0000"
        
        # --- НАСТРОЙКИ СЕРВЕРА TTS ---
        self.TTS_HOST = "127.0.0.1"
        self.TTS_PORT = 10200
        self.TTS_DEFAULT_VOICE = "ru_RU-sushkov-medium_epoch4849"
        
        self.root.configure(bg=self.BG_COLOR)
        self.root.focus_force()
        
        try:
            pygame.mixer.init()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось инициализировать аудио: {e}")
            root.destroy()
            return

        self.folder_path = filedialog.askdirectory(title="Выберите папку с датасетом")
        if not self.folder_path:
            root.destroy()
            return
            
        self.metadata = {}
        self.load_metadata()

        extensions = ('.wav', '.mp3', '.flac', '.ogg', '.m4a')
        self.files =[f for f in os.listdir(self.folder_path) if f.lower().endswith(extensions)]
        self.files.sort()
        self.index = 0
        self.current_file_path = None
        self._play_job = None
        self.current_text = ""

        # --- ИНИЦИАЛИЗАЦИЯ TTS МЕНЕДЖЕРА ---
        self.is_tts_playing = False
        if TTS_AVAILABLE:
            self.tts_audio_queue = queue.Queue()
            # Передаем self.on_tts_finished для обратной связи
            self.tts_player = AudioPlayer(self.tts_audio_queue, self.on_tts_finished)
            self.tts_manager = AsyncWyomingManager(self.TTS_HOST, self.TTS_PORT, self.tts_audio_queue, self.tts_player)
        else:
            self.tts_manager = None

        # --- ИНТЕРФЕЙС ---
        self.lbl_filename = tk.Label(root, text="Готов к работе", 
                                     font=("Helvetica", self.FONT_SIZE, "bold"), 
                                     bg=self.BG_COLOR, fg=self.FG_COLOR)
        self.lbl_filename.pack(pady=(20, 5), fill=tk.X)

        self.lbl_transcription = tk.Label(root, text="", 
                                          font=("Helvetica", self.FONT_SIZE), 
                                          bg=self.BG_COLOR, fg=self.FG_COLOR, justify="center")
        self.lbl_transcription.pack(pady=10, fill=tk.BOTH, expand=True)

        self.slider_var = tk.IntVar()
        self.slider = tk.Scale(root, from_=1, to=max(1, len(self.files)), orient=tk.HORIZONTAL,
                               variable=self.slider_var, command=self.on_slider_change,
                               bg=self.BG_COLOR, fg=self.FG_COLOR, troughcolor=self.PANEL_BG, 
                               activebackground=self.FG_COLOR, highlightthickness=0, bd=0,
                               sliderrelief=tk.FLAT, sliderlength=16, width=16, showvalue=0)
        self.slider.pack(fill=tk.X, padx=30, pady=5)

        self.lbl_count = tk.Label(root, text=f"Файлов: {len(self.files)}", 
                                  font=("Arial", max(10, self.FONT_SIZE - 4)), 
                                  bg=self.BG_COLOR, fg=self.FG_COLOR2)
        self.lbl_count.pack(pady=5)

        # --- TTS ПАНЕЛЬ ---
        self.tts_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.tts_frame.pack(fill=tk.X, padx=30, pady=5)
        
        # Настраиваем колонку с полем ввода, чтобы она растягивалась
        self.tts_frame.columnconfigure(1, weight=1)

        # 1. Метка (Voice)
        tk.Label(self.tts_frame, text="Voice :", bg=self.BG_COLOR, fg=self.FG_COLOR2, 
                 font=("Helvetica", max(10, self.FONT_SIZE - 2))).grid(row=0, column=0, sticky="w")

        # 2. Поле ввода (Entry)
        self.voice_var = tk.StringVar(value=self.TTS_DEFAULT_VOICE)
        self.voice_entry = tk.Entry(self.tts_frame, textvariable=self.voice_var, bg=self.PANEL_BG, fg=self.FG_COLOR2, 
                                    insertbackground=self.FG_COLOR2, font=("Helvetica", max(10, self.FONT_SIZE - 2)), 
                                    relief=tk.FLAT, bd=0, highlightthickness=0)
        # sticky="nsew" растягивает поле во все стороны
        self.voice_entry.grid(row=0, column=1, sticky="nsew", padx=10)

        # 3. Кнопка (Button)
        self.btn_tts = tk.Button(self.tts_frame, text=" ▶ ", command=self.toggle_tts, 
                                 bg=self.PANEL_BG, fg=self.FG_COLOR2, activebackground=self.FG_COLOR2, 
                                 activeforeground=self.BG_COLOR, relief=tk.FLAT, 
                                 font=("Helvetica", max(10, self.FONT_SIZE - 2), "bold"), bd=0)
        # sticky="ns" заставляет кнопку растянуться строго по высоте строки (как поле ввода)
        self.btn_tts.grid(row=0, column=2, sticky="ns")

        if not TTS_AVAILABLE:
            self.btn_tts.config(state=tk.DISABLED, text="TTS недоступен")
            self.voice_entry.config(state=tk.DISABLED)

        # --- ПОДСКАЗКИ ---
        help_text = (
            "повторить ↓  |  ↑ удалить\n"
            "вернуться ←  |  → следущюий\n"
            "пробел       |  ▶/⏹"
        )
        self.lbl_help = tk.Label(root, text=help_text, 
                                 font=("Consolas", max(10, self.FONT_SIZE - 2)), 
                                 bg=self.PANEL_BG, fg=self.FG_COLOR2, relief="sunken", padx=10, pady=10,
                                 justify=tk.LEFT)
        self.lbl_help.pack(side=tk.BOTTOM, pady=20)

        # --- БИНДИНГИ ---
        root.bind_all('<Up>', self.delete_and_next)
        root.bind_all('<Down>', self.replay)
        root.bind_all('<Right>', self.keep_and_next)
        root.bind_all('<Left>', self.prev_file)
        root.bind_all('<space>', self.toggle_tts)
        root.bind('<Configure>', self.on_window_resize)

        if self.files:
            self.load_and_play()
        else:
            self.lbl_filename.config(text="В папке нет аудиофайлов!", fg=self.ERR_COLOR)
            self.slider.config(state=tk.DISABLED)

    # --- НОВЫЕ ФУНКЦИИ ДЛЯ СБРОСА КНОПКИ ---
    def on_tts_finished(self):
        """Вызывается из фонового аудио-потока, когда плеер закончил играть"""
        self.root.after(0, self.reset_tts_ui)

    def reset_tts_ui(self):
        """Возвращает кнопку в исходное состояние"""
        if self.is_tts_playing:
            self.is_tts_playing = False
            self.btn_tts.config(text=" ▶ ", fg=self.FG_COLOR2)

    # --- ОСТАЛЬНАЯ ЛОГИКА ---
    def load_metadata(self):
        metadata_path = os.path.join(self.folder_path, "metadata.csv")
        if os.path.exists(metadata_path):
            for enc in['utf-8', 'cp1251']:
                try:
                    with open(metadata_path, 'r', encoding=enc) as f:
                        self.metadata.clear()
                        for line in f:
                            line = line.strip()
                            if not line: continue
                            parts = line.split('|', 1)
                            if len(parts) == 2:
                                self.metadata[parts[0].strip()] = parts[1].strip()
                    break
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    print(f"Ошибка чтения metadata.csv: {e}")
                    break

    def on_window_resize(self, event):
        if event.widget == self.root:
            wrap_width = max(200, event.width - 40)
            self.lbl_filename.config(wraplength=wrap_width)
            self.lbl_transcription.config(wraplength=wrap_width)

    def stop_tts(self):
        if self.tts_manager and self.is_tts_playing:
            self.tts_manager.stop()
            self.is_tts_playing = False
            self.btn_tts.config(text=" ▶ ", fg=self.FG_COLOR2)

    def toggle_tts(self, event=None):
        if event and hasattr(event, 'widget') and isinstance(event.widget, tk.Entry):
            return

        if not self.tts_manager:
            return

        if self.is_tts_playing:
            self.stop_tts()
        else:
            if not self.current_text:
                return
            
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
                
            self.is_tts_playing = True
            self.btn_tts.config(text=" ⏹ ", fg=self.ERR_COLOR)
            self.tts_manager.start_synthesis(self.current_text, self.voice_var.get())

    def on_slider_change(self, val):
        new_index = int(val) - 1
        if new_index == self.index:
            return
            
        self.index = new_index
        self.stop_tts()
        self.update_ui_texts()
        
        if self._play_job:
            self.root.after_cancel(self._play_job)
        self._play_job = self.root.after(300, self.play_audio)

    def load_and_play(self):
        self.stop_tts()
        if 0 <= self.index < len(self.files):
            self.slider_var.set(self.index + 1)
            self.update_ui_texts()
            self.play_audio()
        elif len(self.files) == 0:
            self.finish()
        else:
            self.finish()

    def update_ui_texts(self):
        if len(self.files) == 0:
            return
            
        filename = self.files[self.index]
        self.current_file_path = os.path.join(self.folder_path, filename)
        
        stem = os.path.splitext(filename)[0]
        transcription = self.metadata.get(filename) or self.metadata.get(stem)
        
        if transcription:
            self.current_text = transcription
            self.lbl_transcription.config(text=f'"{transcription}"')
        else:
            self.current_text = ""
            self.lbl_transcription.config(text="[Нет расшифровки]")
        
        self.lbl_filename.config(text=filename, fg=self.FG_COLOR)
        self.lbl_count.config(text=f"Файл {self.index + 1} из {len(self.files)}")

    def play_audio(self):
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
            pygame.mixer.music.load(self.current_file_path)
            pygame.mixer.music.play()
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
            self.lbl_filename.config(fg=self.ERR_COLOR)

    def replay(self, event=None):
        self.stop_tts()
        if self.current_file_path:
            try:
                pygame.mixer.music.play()
                self.lbl_filename.config(bg="#004400") 
                self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))
            except Exception:
                pass

    def keep_and_next(self, event=None):
        if self.index < len(self.files) - 1:
            self.index += 1
            self.load_and_play()
        else:
            self.lbl_filename.config(bg="#444400")
            self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def prev_file(self, event=None):
        if self.index > 0:
            self.index -= 1
            self.load_and_play()
        else:
            self.lbl_filename.config(bg="#550000")
            self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def delete_and_next(self, event=None):
        if self.current_file_path and os.path.exists(self.current_file_path):
            try:
                self.stop_tts()
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
                
                os.remove(self.current_file_path)
                print(f"Удален: {self.files[self.index]}")
                
                del self.files[self.index]
                self.slider.config(to=max(1, len(self.files)))
                
                if self.index >= len(self.files) and self.index > 0:
                    self.index -= 1
                
                self.load_and_play()
                        
            except PermissionError:
                messagebox.showerror("Ошибка", "Файл занят. Попробуйте нажать стоп или перезапустить.")
            except Exception as e:
                print(f"Ошибка удаления: {e}")

    def finish(self):
        self.stop_tts()
        pygame.mixer.music.stop()
        self.lbl_filename.config(text="ВСЕ ФАЙЛЫ ОБРАБОТАНЫ!", fg=self.FG_COLOR)
        self.lbl_transcription.config(text="")
        self.lbl_count.config(text="")
        self.slider.config(state=tk.DISABLED)
        self.current_file_path = None
        self.root.unbind_all('<Up>')
        self.root.unbind_all('<Down>')
        self.root.unbind_all('<Right>')
        self.root.unbind_all('<Left>')
        self.root.unbind_all('<space>')

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioCleaner(root)
    root.mainloop()