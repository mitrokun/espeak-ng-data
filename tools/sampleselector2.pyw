import os
import re
import time
import queue
import asyncio
import threading
import pygame
import tkinter as tk
from tkinter import filedialog, messagebox

# --- ИМПОРТЫ ДЛЯ ГРАФИКОВ И ОС ---
import wave
import array
import platform
import subprocess

from fast_cutter import FastAudioCutter

# --- ИМПОРТЫ ДЛЯ TTS ---
try:
    import sounddevice as sd
    from wyoming.client import AsyncTcpClient
    from wyoming.tts import (
        Synthesize,  
        SynthesizeStart,
        SynthesizeChunk,
        SynthesizeStop,
        SynthesizeVoice,
        SynthesizeStopped
    )
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    TTS_AVAILABLE = True
except ImportError as e:
    TTS_AVAILABLE = False
    print(f"Внимание: Модули для TTS не найдены ({e}). Функция синтеза будет отключена.")

# --- КОНСТАНТЫ TTS ---
AUDIO_STOP = object()
AUDIO_FINISHED = object()
SAMPLE_RATE = 22050
SAMPLE_WIDTH = 2
CHANNELS = 1
PLAYER_CHUNK_SIZE = 1024

if TTS_AVAILABLE:
    class AudioPlayer:
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
            stream = None
            
            while True:
                chunk_data = self.queue.get()
                
                if chunk_data is AUDIO_FINISHED:
                    if self.on_finished:
                        self.on_finished()
                    continue
                
                if chunk_data is AUDIO_STOP:
                    if stream:
                        stream.stop()
                        stream.close()
                        stream = None
                    continue
                
                if stream is None:
                    stream = sd.RawOutputStream(
                        samplerate=SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype='int16'
                    )
                    stream.start()
                
                total_len = len(chunk_data)
                cursor = 0
                
                while cursor < total_len:
                    if self.abort_flag.is_set(): break
                    self.resume_event.wait()
                    if self.abort_flag.is_set(): break

                    end = min(cursor + PLAYER_CHUNK_SIZE, total_len)
                    small_chunk = chunk_data[cursor:end]
                    
                    try:
                        if stream and stream.active:
                            stream.write(small_chunk)
                    except: break
                    
                    cursor = end

                if self.abort_flag.is_set():
                    if stream:
                        stream.stop()
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
            self.on_synthesis_complete = None

        def start_synthesis(self, text, voice_name, disable_streaming=False):
            self.stop()
            self.player.reset()
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_async_loop, 
                args=(text, voice_name, disable_streaming), 
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

        def _run_async_loop(self, text, voice_name, disable_streaming):
            try:
                asyncio.run(self._async_session(text, voice_name, disable_streaming))
            except Exception as e:
                print(f"Ошибка Async Loop: {e}")

        async def _async_session(self, text, voice_name, disable_streaming):
            try:
                async with AsyncTcpClient(self.host, self.port) as client:
                    voice = SynthesizeVoice(name=voice_name)
                    
                    if disable_streaming:
                        await client.write_event(Synthesize(text=text, voice=voice).event())
                        await self._read_events(client)
                    else:
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
            accumulated_bytes = bytearray()
            while not self._stop_event.is_set():
                event = await client.read_event()
                if event is None: break
                
                if AudioChunk.is_type(event.type):
                    chunk = AudioChunk.from_event(event)
                    accumulated_bytes.extend(chunk.audio)
                    self.audio_queue.put(chunk.audio)
                
                elif AudioStop.is_type(event.type):
                    self.audio_queue.put(AUDIO_FINISHED)
                    if self.on_synthesis_complete and len(accumulated_bytes) > 0:
                        self.on_synthesis_complete(bytes(accumulated_bytes))
                    break 
                
                elif SynthesizeStopped.is_type(event.type):
                    break


class AudioCleaner:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Dataset Cleaner")
        self.root.attributes('-topmost', True)
        self.root.geometry("900x600") 
        self.root.minsize(450, 500)
        
        # --- ССЫЛКА НА АКТИВНЫЙ РЕДАКТОР ---
        self.active_editor = None

        # --- НАСТРОЙКИ ИНТЕРФЕЙСА ---
        self.FONT_SIZE = 16
        self.BG_COLOR = "#131313"
        self.FG_COLOR = "#26A269"
        self.FG_COLOR_TTS = "#2669A2" 
        self.FG_COLOR2 = "#135240"
        self.PANEL_BG = "#1e1e1e"
        self.ERR_COLOR = "#560000"
        
        # --- НАСТРОЙКИ СЕРВЕРА TTS ---
        self.TTS_HOST = "127.0.0.1"
        self.TTS_PORT = 10200
        self.TTS_DEFAULT_VOICE = "ru_RU-terra-medium_epoch5819"
        
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
        all_physical_files = {f for f in os.listdir(self.folder_path) if f.lower().endswith(extensions)}
        
        self.files =[]
        metadata_path = os.path.join(self.folder_path, "metadata.csv")
        
        if os.path.exists(metadata_path):
            for enc in ['utf-8', 'cp1251']:
                try:
                    with open(metadata_path, 'r', encoding=enc) as f:
                        for line in f:
                            parts = line.split('|', 1)
                            if parts:
                                file_id = parts[0].strip()
                                matched_file = None
                                if file_id in all_physical_files:
                                    matched_file = file_id
                                else:
                                    for f_name in all_physical_files:
                                        if os.path.splitext(f_name)[0] == file_id:
                                            matched_file = f_name
                                            break
                                
                                if matched_file and matched_file not in self.files:
                                    self.files.append(matched_file)
                    break
                except:
                    continue

        remaining = sorted(list(all_physical_files - set(self.files)))
        self.files.extend(remaining)

        self.index = 0
        self.current_file_path = None
        self._play_job = None
        self.current_text = ""
        
        self.current_pcm_orig = None
        self.current_pcm_tts = None
        self._resize_timer = None

        # --- ДАННЫЕ ДЛЯ АНИМАЦИИ ВОСПРОИЗВЕДЕНИЯ ---
        self.current_channel = None
        self.play_start_time = 0
        self.play_duration = 0
        self.is_animating = False
        self.is_paused = False
        self.pause_start_time = 0

        # --- ИНИЦИАЛИЗАЦИЯ TTS МЕНЕДЖЕРА ---
        self.is_tts_playing = False
        if TTS_AVAILABLE:
            self.tts_audio_queue = queue.Queue()
            self.tts_player = AudioPlayer(self.tts_audio_queue, self.on_tts_finished)
            self.tts_manager = AsyncWyomingManager(self.TTS_HOST, self.TTS_PORT, self.tts_audio_queue, self.tts_player)
            self.tts_manager.on_synthesis_complete = self.on_tts_bytes_ready
        else:
            self.tts_manager = None

        # --- ИНТЕРФЕЙС ---
        self.top_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.top_frame.pack(pady=(20, 5), fill=tk.X)
        self.top_frame.columnconfigure(0, weight=1, uniform="top")
        self.top_frame.columnconfigure(1, weight=0)
        self.top_frame.columnconfigure(2, weight=1, uniform="top")

        self.lbl_filename = tk.Label(self.top_frame, text="Готов к работе", 
                                     font=("Helvetica", self.FONT_SIZE, "bold"), 
                                     bg=self.BG_COLOR, fg=self.FG_COLOR)
        self.lbl_filename.grid(row=0, column=1)
        self.lbl_filename.bind("<Button-1>", self.manual_reload)
        self.lbl_filename.bind("<Button-3>", self.open_in_external_app)
        self.lbl_filename.config(cursor="hand2")

        self.btn_edit = tk.Button(self.top_frame, text="✎", command=self.open_editor, 
                                  bg=self.BG_COLOR, fg=self.FG_COLOR2, activebackground=self.BG_COLOR, 
                                  activeforeground=self.FG_COLOR, relief=tk.FLAT, 
                                  font=("Helvetica", 14), bd=0)
        self.btn_edit.grid(row=0, column=2, sticky="w", padx=10)

        # --- БЛОК ВОЛНОГРАММ ---
        self.wave_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.wave_frame.pack(fill=tk.X, padx=30, pady=5)
        
        self.canvas_orig = tk.Canvas(self.wave_frame, bg=self.PANEL_BG, height=45, highlightthickness=0)
        self.canvas_orig.pack(fill=tk.X, pady=(0, 5))
        self.canvas_orig.bind("<Button-1>", self.open_internal_editor)
        self.canvas_orig.config(cursor="hand2")

        self.canvas_tts = tk.Canvas(self.wave_frame, bg=self.PANEL_BG, height=45, highlightthickness=0)
        self.canvas_tts.pack(fill=tk.X)

        # --- БЛОК РАСШИФРОВКИ (ТЕКСТ) ---
        self.lbl_transcription = tk.Label(root, text="", 
                                          font=("Helvetica", self.FONT_SIZE), 
                                          bg=self.BG_COLOR, fg=self.FG_COLOR, justify="center")
        self.lbl_transcription.pack(pady=5, fill=tk.BOTH, expand=True)
        self.lbl_transcription.bind("<Double-Button-1>", self.open_editor)
        self.lbl_transcription.config(cursor="hand2")

        # --- БЛОК ПОЛЗУНКА ---
        self.slider_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.slider_frame.pack(fill=tk.X, padx=30, pady=10)
        self.slider_frame.columnconfigure(1, weight=1)

        self.btn_prev_arrow = tk.Button(self.slider_frame, text="❮", command=self.prev_file, 
                                        bg=self.PANEL_BG, fg=self.FG_COLOR2, 
                                        activebackground=self.FG_COLOR2, activeforeground=self.BG_COLOR, 
                                        relief=tk.FLAT, font=("Arial", 8), 
                                        width=5, bd=0, padx=2, pady=0, highlightthickness=0)
        self.btn_prev_arrow.grid(row=0, column=0)

        self.slider_var = tk.IntVar()
        self.slider = tk.Scale(self.slider_frame, from_=1, to=max(1, len(self.files)), orient=tk.HORIZONTAL,
                               variable=self.slider_var, command=self.on_slider_change,
                               bg=self.BG_COLOR, fg=self.FG_COLOR, troughcolor=self.PANEL_BG, 
                               activebackground=self.FG_COLOR, highlightthickness=0, bd=0,
                               sliderrelief=tk.FLAT, sliderlength=16, width=16, showvalue=0)
        self.slider.grid(row=0, column=1, sticky="nsew", padx=5)

        self.btn_next_arrow = tk.Button(self.slider_frame, text="❯", command=self.keep_and_next, 
                                        bg=self.PANEL_BG, fg=self.FG_COLOR2, 
                                        activebackground=self.FG_COLOR2, activeforeground=self.BG_COLOR, 
                                        relief=tk.FLAT, font=("Arial", 8), 
                                        width=5, bd=0, padx=2, pady=0, highlightthickness=0)
        self.btn_next_arrow.grid(row=0, column=2)

        self.replay_zone = tk.Frame(root, bg=self.BG_COLOR)
        self.replay_zone.pack(fill=tk.X)

        self.lbl_count = tk.Label(self.replay_zone, text=f"Файлов: {len(self.files)}", 
                                  font=("Arial", max(10, self.FONT_SIZE - 6)), 
                                  bg=self.BG_COLOR, fg=self.FG_COLOR2)
        self.lbl_count.pack(fill=tk.BOTH, pady=5) 

        self.replay_zone.bind("<Button-1>", self.toggle_play)
        self.replay_zone.bind("<Button-3>", self.manual_stop)        
        self.lbl_count.bind("<Button-1>", self.toggle_play)
        self.lbl_count.bind("<Button-3>", self.manual_stop)


        # --- TTS ПАНЕЛЬ ---
        self.tts_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.tts_frame.pack(fill=tk.X, padx=30, pady=5)
        self.tts_frame.columnconfigure(1, weight=1)

        tk.Label(self.tts_frame, text="Voice :", bg=self.BG_COLOR, fg=self.FG_COLOR2, 
                 font=("Segoe UI", max(10, self.FONT_SIZE - 2))).grid(row=0, column=0, sticky="w")

        self.voice_var = tk.StringVar(value=self.TTS_DEFAULT_VOICE)
        self.voice_entry = tk.Entry(self.tts_frame, textvariable=self.voice_var, bg=self.PANEL_BG, fg=self.FG_COLOR2, 
                                    insertbackground=self.FG_COLOR2, font=("Segoe UI", max(10, self.FONT_SIZE - 2)), 
                                    relief=tk.FLAT, bd=0, highlightthickness=0)
        self.fix_copy_paste(self.voice_entry)
        self.voice_entry.grid(row=0, column=1, sticky="nsew", padx=10)

        self.voice_entry.bind('<Return>', self.exit_entry_and_play)
        self.voice_entry.bind('<Escape>', lambda e: self.root.focus_set())
        self.btn_tts = tk.Button(self.tts_frame, text=" ▶ ", command=self.toggle_tts, 
                                 bg=self.PANEL_BG, fg=self.FG_COLOR2, activebackground=self.FG_COLOR2, 
                                 activeforeground=self.BG_COLOR, relief=tk.FLAT, 
                                 width=3, font=("Segoe UI", max(10, self.FONT_SIZE - 2), "bold"), bd=0)
        self.btn_tts.grid(row=0, column=2, sticky="ns")

        if not TTS_AVAILABLE:
            self.btn_tts.config(state=tk.DISABLED, text="TTS недоступен")
            self.voice_entry.config(state=tk.DISABLED)

        # --- НИЖНЯЯ ПАНЕЛЬ ---
        self.bottom_frame = tk.Frame(root, bg=self.BG_COLOR)
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
        self.bottom_frame.columnconfigure(0, weight=1, uniform="edges")
        self.bottom_frame.columnconfigure(1, weight=0)
        self.bottom_frame.columnconfigure(2, weight=1, uniform="edges")

        help_text = (
            "повторить ↓  |  ↑ удалить\n"
            "вернуться ←  |  → следующий\n"
            "правый ctrl  |  ▶/⏹"
        )
        self.lbl_help = tk.Label(self.bottom_frame, text=help_text, 
                                 font=("Consolas", max(10, self.FONT_SIZE - 2)), 
                                 bg=self.PANEL_BG, fg=self.FG_COLOR2, relief="sunken", 
                                 padx=10, pady=10, justify=tk.LEFT)
        self.lbl_help.grid(row=0, column=1)

        self.chk_frame = tk.Frame(self.bottom_frame, bg=self.BG_COLOR)
        self.chk_frame.grid(row=0, column=2, padx=10, sticky="nw")

        self.allow_delete_var = tk.BooleanVar(value=False) 
        self.chk_del = tk.Checkbutton(self.chk_frame, text="allow deletion", 
                                      variable=self.allow_delete_var,
                                      bg=self.BG_COLOR, fg=self.FG_COLOR2, 
                                      selectcolor=self.PANEL_BG,
                                      activebackground=self.BG_COLOR,
                                      activeforeground=self.FG_COLOR2,
                                      font=("Helvetica", max(10, self.FONT_SIZE - 4)),
                                      bd=0, highlightthickness=0)
        self.chk_del.pack(anchor="w", pady=(0, 2))

        self.disable_streaming_var = tk.BooleanVar(value=False)
        self.chk_stream = tk.Checkbutton(self.chk_frame, text="disable streaming", 
                                         variable=self.disable_streaming_var,
                                         bg=self.BG_COLOR, fg=self.FG_COLOR2, 
                                         selectcolor=self.PANEL_BG,
                                         activebackground=self.BG_COLOR,
                                         activeforeground=self.FG_COLOR2,
                                         font=("Helvetica", max(10, self.FONT_SIZE - 4)),
                                         bd=0, highlightthickness=0)
        self.chk_stream.pack(anchor="w")

        # --- БИНДИНГИ ---
        root.bind_all('<Up>', self.delete_and_next)
        root.bind_all('<Down>', self.toggle_play)
        root.bind_all('<space>', self.toggle_pause)
        root.bind_all('<Right>', self.keep_and_next)
        root.bind_all('<Left>', self.prev_file)

        root.bind_all('<Key>', self.handle_letter_keys)

        root.bind_all('<Control_R>', self.toggle_tts)
        root.bind_all('<0>', self.toggle_tts)
        root.bind('<Configure>', self.on_window_resize)

        if self.files:
            self.load_and_play()
        else:
            self.lbl_filename.config(text="В папке нет аудиофайлов!", fg=self.ERR_COLOR)
            self.slider.config(state=tk.DISABLED)

    def handle_letter_keys(self, event):
        if self.is_editing(event): return
        
        kc, ks = event.keycode, event.keysym.lower()
        
        # Маппинг: (Win, Linux, Mac, Keysym) -> Метод
        hotkeys = {
            (65, 38, 0, 'a'): self.prev_file,
            (68, 40, 2, 'd'): self.keep_and_next,
            (87, 25, 13, 'w'): self.open_internal_editor,
            (82, 27, 15, 'r'): self.open_editor,
            (70, 41, 3, 'f'): self.toggle_tts
        }

        for keys, action in hotkeys.items():
            if kc in keys or ks in keys:
                action()
                return "break"

    # --- УПРАВЛЕНИЕ ВОСПРОИЗВЕДЕНИЕМ (ОРИГИНАЛ) ---
    def stop_audio(self):
        """Полностью останавливает аудио оригинала и сбрасывает состояние"""
        if self.current_channel:
            self.current_channel.stop()
        pygame.mixer.stop()
        self.is_paused = False
        self.hide_playhead()

    def manual_stop(self, event=None):
        """Безусловная остановка всего (Оригинал + TTS) на правый клик"""
        if self.is_editing(event): return
        
        self.stop_audio()
        self.stop_tts()
        
        # Визуальный отклик (красный) — полная остановка
        self.lbl_filename.config(bg="#440000")
        self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def toggle_play(self, event=None):
        """Клавиша ВНИЗ или клик. Если играет < 2 сек - рестарт. Иначе - стоп."""
        if self.is_editing(event): return
        
        if self.is_tts_playing: 
            self.stop_tts()

        # Порог времени в секундах. Можете изменить под себя (например, 1.0 или 2.0)
        RESTART_THRESHOLD = 4.0  

        # Если канал есть и он занят (даже если на паузе)
        if self.current_channel and self.current_channel.get_busy():
            # Вычисляем реальное время воспроизведения с учетом возможной паузы
            if self.is_paused:
                elapsed = self.pause_start_time - self.play_start_time
            else:
                elapsed = time.time() - self.play_start_time

            if elapsed < RESTART_THRESHOLD:
                # Если прошло меньше заданного времени — начинаем заново
                self.play_audio()
                self.lbl_filename.config(bg="#004400") # Зеленый цвет (запуск)
            else:
                # Если прошло больше — останавливаем
                self.stop_audio()
                self.lbl_filename.config(bg="#440000") # Красный цвет (стоп)
        else:
            # Если звук не играет — просто запускаем
            self.play_audio()
            self.lbl_filename.config(bg="#004400") # Зеленый цвет (запуск)
            
        self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def toggle_pause(self, event=None):
        """ПРОБЕЛ. Пауза / Снятие с паузы. Если звук закончен - начинает сначала."""
        if self.is_editing(event): return
        
        # --- ЗАЩИТА: ЕСЛИ ОТКРЫТ РЕДАКТОР, ИГНОРИРУЕМ ПРОБЕЛ В ГЛАВНОМ ОКНЕ ---
        if self.active_editor:
            return
        
        if self.is_tts_playing: 
            self.stop_tts()

        # Проверяем, играет ли звук прямо сейчас (или стоит на паузе)
        if self.current_channel and self.current_channel.get_busy():
            if self.is_paused:
                # Снимаем с паузы
                self.current_channel.unpause()
                self.is_paused = False
                
                # Корректируем время начала, чтобы анимация не прыгала вперед
                time_spent_in_pause = time.time() - self.pause_start_time
                self.play_start_time += time_spent_in_pause
                
                # Перезапускаем цикл анимации
                self.animate_playhead()
            else:
                # Ставим на паузу
                self.current_channel.pause()
                self.is_paused = True
                self.pause_start_time = time.time()
                # Анимация остановится сама из-за проверки self.is_paused
        else:
            # Звук завершен или еще не начинался, запускаем сначала
            self.play_audio()

    def play_audio(self):
        if not self.current_file_path or not os.path.exists(self.current_file_path): 
            return
        try:
            self.stop_audio()
            
            self.current_pcm_orig = self.get_wav_pcm(self.current_file_path)
            self.render_waveforms()

            # --- ЕСЛИ РЕДАКТОР ОТКРЫТ, МЫ НЕ ВОСПРОИЗВОДИМ АУДИО В ГЛАВНОМ ОКНЕ ---
            if self.active_editor:
                return

            sound = pygame.mixer.Sound(self.current_file_path)
            self.play_duration = sound.get_length()
            self.play_start_time = time.time()
            self.is_animating = True
            self.is_paused = False
            
            self.current_channel = sound.play()
            self.animate_playhead()
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
            self.lbl_filename.config(fg=self.ERR_COLOR)

    # --- ЛОГИКА АНИМАЦИИ ВОСПРОИЗВЕДЕНИЯ ---
    def animate_playhead(self):
        # Если прервали анимацию или поставили на паузу - выходим из цикла
        if not self.is_animating or self.is_paused:
            return

        elapsed = time.time() - self.play_start_time
        
        if elapsed > self.play_duration:
            self.hide_playhead()
            return

        ratio = elapsed / self.play_duration
        width = self.canvas_orig.winfo_width()
        x = ratio * width

        self.canvas_orig.coords("playhead", x, 0, x, self.canvas_orig.winfo_height())
        self.canvas_orig.itemconfig("playhead", state=tk.NORMAL)

        # Планируем следующий кадр (~60 FPS)
        self.root.after(16, self.animate_playhead)

    def hide_playhead(self):
        self.is_animating = False
        self.is_paused = False
        if hasattr(self, 'canvas_orig') and self.canvas_orig:
            self.canvas_orig.itemconfig("playhead", state=tk.HIDDEN)


    def open_internal_editor(self, event=None):
        """Открывает внутренний редактор"""
        if self.is_editing(event): return 

        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return
            
        if self.active_editor:
            self.active_editor.root.focus_force()
            return
        
        self.stop_audio()
        # Передаем новый параметр on_save_callback
        self.active_editor = FastAudioCutter(
            self.root, 
            self.current_file_path, 
            self.on_editor_closed,
            on_save_callback=self.refresh_current_waveform # Передаем функцию обновления
        )

    def refresh_current_waveform(self):
        """Перечитывает файл с диска и обновляет фоновую волнограмму"""
        if self.current_file_path and os.path.exists(self.current_file_path):
            self.current_pcm_orig = self.get_wav_pcm(self.current_file_path)
            self.render_waveforms()

    def on_editor_closed(self):
        """Вызывается автоматически, когда редактор закрывается"""
        self.active_editor = None # Сбрасываем ссылку
        
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return
        self.current_pcm_orig = self.get_wav_pcm(self.current_file_path)
        self.render_waveforms()

    def open_in_external_app(self, event=None):
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return
        try:
            self.stop_audio()
            
            if platform.system() == 'Windows':
                os.startfile(self.current_file_path)
            elif platform.system() == 'Darwin':
                subprocess.call(('open', self.current_file_path))
            else:
                subprocess.call(('xdg-open', self.current_file_path))
        except Exception as e:
            print(f"Не удалось открыть файл: {e}")

    # --- ЛОГИКА ВОЛНОГРАММ ---
    def get_wav_pcm(self, filepath):
        try:
            with wave.open(filepath, 'rb') as wf:
                return wf.readframes(wf.getnframes())
        except Exception:
            return None 

    def render_waveforms(self):
        self._draw_single_waveform(self.canvas_orig, self.current_pcm_orig, self.FG_COLOR)
        self._draw_single_waveform(self.canvas_tts, self.current_pcm_tts, self.FG_COLOR_TTS)

    def _draw_single_waveform(self, canvas, pcm_bytes, color):
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        
        if width <= 1: 
            return

        half_height = height / 2.0
        canvas.create_line(0, half_height, width, half_height, fill="#333333")

        if pcm_bytes:
            try:
                samples = array.array('h', pcm_bytes)
                num_samples = len(samples)
                if num_samples > 0:
                    samples_per_pixel = max(1, num_samples // width)
                    scale = half_height / 32768.0 
                    coords =[]
                    
                    for x in range(width):
                        start = x * samples_per_pixel
                        end = start + samples_per_pixel
                        
                        chunk = samples[start:end]
                        if not chunk: 
                            break
                        
                        min_val = min(chunk)
                        max_val = max(chunk)
                        
                        y1 = half_height - (max_val * scale)
                        y2 = half_height - (min_val * scale)
                        
                        if int(y1) == int(y2): 
                            y2 += 1
                            
                        coords.extend([x, y1, x, y2])
                    
                    if len(coords) >= 4:
                        canvas.create_line(coords, fill=color, width=1)
                        
            except Exception as e:
                canvas.create_text(width/2, half_height, text="Ошибка чтения waveform", fill=self.ERR_COLOR)

        canvas.create_line(0, 0, 0, height, fill="#FFFFFF", width=1, tags="playhead", state=tk.HIDDEN)

    def on_tts_bytes_ready(self, pcm_bytes):
        self.current_pcm_tts = pcm_bytes
        self.root.after(0, self.render_waveforms)

    # --- ОСТАЛЬНАЯ ЛОГИКА ---
    def is_editing(self, event):
        if event and hasattr(event, 'widget'):
            if isinstance(event.widget, (tk.Entry, tk.Text)):
                return True
        return False

    def open_editor(self, event=None):
        if hasattr(self, 'editor') and self.editor.winfo_exists():
            self.editor.focus_force()
            return

        if not self.current_text and not self.current_file_path: return

        # 1. Определяем, какое окно сейчас активно (главное или аудио-редактор)
        parent_win = self.active_editor.root if self.active_editor else self.root

        self.editor = tk.Toplevel(parent_win)
        self.editor.title("Editor")
        
        # 2. Центрируем текст относительно активного окна
        parent_win.update_idletasks()
        p_w, p_h = parent_win.winfo_width(), parent_win.winfo_height()
        p_x, p_y = parent_win.winfo_x(), parent_win.winfo_y()
        width, height = 600, 250
        x = p_x + (p_w // 2) - (width // 2)
        y = p_y + (p_h // 2) - (height // 2)
        self.editor.geometry(f"{width}x{height}+{x}+{y}")

        self.editor.configure(bg=self.BG_COLOR)
        self.editor.transient(parent_win) # Привязываем к активному окну
        self.editor.grab_set() 

        # 3. Функция правильного закрытия с возвратом фокуса
        def close_editor(e=None):
            self.editor.destroy()
            parent_win.focus_force() # Возвращаем фокус родителю!

        self.editor.bind("<Escape>", close_editor)

        lbl = tk.Label(self.editor, text="Transcription:", bg=self.BG_COLOR, fg=self.FG_COLOR, font=("Helvetica", max(10, self.FONT_SIZE-4)))
        lbl.pack(pady=(8,0))

        txt = tk.Text(self.editor, font=("Helvetica", self.FONT_SIZE), bg=self.PANEL_BG, fg=self.FG_COLOR, 
                      wrap=tk.WORD, height=4, insertbackground=self.FG_COLOR, bd=0)
        self.fix_copy_paste(txt)
        txt.pack(padx=15, pady=10, fill=tk.BOTH, expand=True)
        txt.insert("1.0", self.current_text)
        txt.focus_set()

        btn_frame = tk.Frame(self.editor, bg=self.BG_COLOR)
        btn_frame.pack(pady=(0, 15), fill=tk.X, padx=15)

        def insert_stress(): txt.insert(tk.INSERT, "\u0301"); txt.focus_set()

        def insert_yo():
            try:
                if txt.tag_ranges("sel"): txt.delete("sel.first", "sel.last")
            except tk.TclError: pass
            txt.insert(tk.INSERT, "ё"); txt.focus_set()

        def insert_dash():
            try:
                if txt.tag_ranges("sel"): txt.delete("sel.first", "sel.last")
            except tk.TclError: pass
            txt.insert(tk.INSERT, " —")
            txt.focus_set()

        def insert_colon():
            try:
                if txt.tag_ranges("sel"): txt.delete("sel.first", "sel.last")
            except tk.TclError: pass
            txt.insert(tk.INSERT, ":")
            txt.focus_set()

        def insert_e():
            try:
                if txt.tag_ranges("sel"): txt.delete("sel.first", "sel.last")
            except tk.TclError: pass
            txt.insert(tk.INSERT, "э")
            txt.focus_set()

        # 4. Обновленная функция сохранения (содержит фикс с Enter из предыдущего ответа)
        def save_changes(event=None):
            new_text = txt.get("1.0", tk.END).strip()
            self.save_transcription(new_text)
            close_editor() # Закрываем правильным способом
            return "break"

        txt.bind('<Return>', save_changes)

        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(7, weight=1)

        btn_stress = tk.Button(btn_frame, text="Add U+0301", command=insert_stress,
                               bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                               activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4)))
        btn_stress.grid(row=0, column=1, padx=5)

        btn_e = tk.Button(btn_frame, text="э", command=insert_e,
                          bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                          activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4)), width=2) 
        btn_e.grid(row=0, column=2, padx=5)

        btn_dash = tk.Button(btn_frame, text="—", command=insert_dash,
                             bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                             activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4)), width=2) 
        btn_dash.grid(row=0, column=3, padx=5)

        btn_dash = tk.Button(btn_frame, text=":", command=insert_colon,
                             bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                             activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4)), width=2) 
        btn_dash.grid(row=0, column=4, padx=5)

        btn_yo = tk.Button(btn_frame, text="ё", command=insert_yo,
                           bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                           activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4)), width=2) 
        btn_yo.grid(row=0, column=5, padx=5)

        btn_save = tk.Button(btn_frame, text="Сохранить", command=save_changes,
                             bg=self.PANEL_BG, fg=self.FG_COLOR, activebackground=self.FG_COLOR,
                             activeforeground=self.BG_COLOR, relief=tk.FLAT, font=("Helvetica", max(10, self.FONT_SIZE - 4), "bold"))
        btn_save.grid(row=0, column=6, padx=5)

    def save_transcription(self, new_text):
        if not self.files or self.index >= len(self.files): return
        filename = self.files[self.index]
        stem = os.path.splitext(filename)[0]
        
        self.current_text = new_text
        self.lbl_transcription.config(text=f'"{new_text}"')
        
        if stem in self.metadata: self.metadata[stem] = new_text
        else: self.metadata[filename] = new_text

        metadata_path = os.path.join(self.folder_path, "metadata.csv")
        if os.path.exists(metadata_path):
            used_enc = 'utf-8'
            lines = []
            for enc in['utf-8', 'cp1251']:
                try:
                    with open(metadata_path, 'r', encoding=enc) as f: lines = f.readlines()
                    used_enc = enc; break
                except UnicodeDecodeError: continue

            if lines:
                new_lines =[]
                for line in lines:
                    parts = line.split('|', 1)
                    if len(parts) == 2:
                        row_id = parts[0].strip()
                        if row_id == filename or row_id == stem:
                            ending = "\r\n" if line.endswith("\r\n") else "\n"
                            line = f"{row_id}|{new_text}{ending}"
                    new_lines.append(line)
                
                with open(metadata_path, 'w', encoding=used_enc) as f: f.writelines(new_lines)

    def on_tts_finished(self):
        self.root.after(0, self.reset_tts_ui)

    def reset_tts_ui(self):
        if self.is_tts_playing:
            self.is_tts_playing = False
            self.btn_tts.config(text=" ▶ ", fg=self.FG_COLOR2)

    def load_metadata(self):
        metadata_path = os.path.join(self.folder_path, "metadata.csv")
        if os.path.exists(metadata_path):
            for enc in ['utf-8', 'cp1251']:
                try:
                    with open(metadata_path, 'r', encoding=enc) as f:
                        self.metadata.clear()
                        for line in f:
                            line = line.strip()
                            if not line: continue
                            parts = line.split('|', 1)
                            if len(parts) == 2: self.metadata[parts[0].strip()] = parts[1].strip()
                    break
                except UnicodeDecodeError: continue

    def on_window_resize(self, event):
        if event.widget == self.root:
            wrap_width = max(200, event.width - 40)
            self.lbl_filename.config(wraplength=wrap_width)
            self.lbl_transcription.config(wraplength=wrap_width)
            
            if self._resize_timer:
                self.root.after_cancel(self._resize_timer)
            self._resize_timer = self.root.after(200, self.render_waveforms)

    def fix_copy_paste(self, widget):
        def handle_command(event):
            if event.state & 0x4: 
                if event.keycode == 67: widget.event_generate("<<Copy>>"); return "break"
                elif event.keycode == 86: widget.event_generate("<<Paste>>"); return "break"
                elif event.keycode == 65: widget.event_generate("<<SelectAll>>"); return "break"
        widget.bind("<Control-KeyPress>", handle_command)

    def manual_reload(self, event=None):
        old_color = self.lbl_filename.cget("fg")
        self.lbl_filename.config(fg="#FFFFFF") 
        self.root.update_idletasks() 
        self.load_metadata()     
        self.update_ui_texts()   
        self.root.after(200, lambda: self.lbl_filename.config(fg=old_color))

    def stop_tts(self):
        if self.tts_manager and self.is_tts_playing:
            self.tts_manager.stop()
            self.is_tts_playing = False
            self.btn_tts.config(text=" ▶ ", fg=self.FG_COLOR2)

    def exit_entry_and_play(self, event=None):
        self.root.focus_set()
        self.toggle_tts()

    def toggle_tts(self, event=None):
        if self.is_editing(event): return
        if not self.tts_manager: return

        if self.is_tts_playing:
            self.stop_tts()
        else:
            if not self.current_text: return
            self.stop_audio()
                
            self.is_tts_playing = True
            self.btn_tts.config(text="⏹", fg=self.ERR_COLOR)
            
            disable_stream = self.disable_streaming_var.get()
            self.tts_manager.start_synthesis(
                self.current_text, 
                self.voice_var.get(), 
                disable_streaming=disable_stream
            )

    def on_slider_change(self, val):
        new_index = int(val) - 1
        if new_index == self.index: return
            
        self.index = new_index
        self.stop_tts()
        self.update_ui_texts()
        
        if self._play_job: self.root.after_cancel(self._play_job)
        self._play_job = self.root.after(300, self.play_audio)

    def load_and_play(self):
        self.stop_tts()
        if 0 <= self.index < len(self.files):
            self.slider_var.set(self.index + 1)
            self.update_ui_texts()
            self.play_audio()
        else:
            self.finish()

    def update_ui_texts(self):
        if len(self.files) == 0: return
            
        filename = self.files[self.index]
        self.current_file_path = os.path.join(self.folder_path, filename)
        
        # --- СИНХРОНИЗАЦИЯ С ОТКРЫТЫМ РЕДАКТОРОМ ---
        if self.active_editor:
            self.active_editor.load_file(self.current_file_path)

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

        self.current_pcm_tts = None 
        self.current_pcm_orig = self.get_wav_pcm(self.current_file_path)
        self.root.after(50, self.render_waveforms)


    def keep_and_next(self, event=None):
        if self.is_editing(event): return
        if self.index < len(self.files) - 1:
            self.index += 1
            self.load_and_play()
        else:
            self.lbl_filename.config(bg="#444400")
            self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def prev_file(self, event=None):
        if self.is_editing(event): return
        if self.index > 0:
            self.index -= 1
            self.load_and_play()
        else:
            self.lbl_filename.config(bg="#550000")
            self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))

    def delete_and_next(self, event=None):
        if self.is_editing(event): return
        if not self.allow_delete_var.get():
            self.lbl_filename.config(bg="#440000")
            self.root.after(200, lambda: self.lbl_filename.config(bg=self.BG_COLOR))
            return

        if self.current_file_path and os.path.exists(self.current_file_path):
            try:
                self.stop_tts()
                self.stop_audio()
                
                filename = self.files[self.index]
                stem = os.path.splitext(filename)[0]
                
                os.remove(self.current_file_path)
                
                metadata_path = os.path.join(self.folder_path, "metadata.csv")
                if os.path.exists(metadata_path):
                    used_enc = 'utf-8'
                    lines = []
                    for enc in['utf-8', 'cp1251']:
                        try:
                            with open(metadata_path, 'r', encoding=enc) as f: lines = f.readlines()
                            used_enc = enc; break
                        except UnicodeDecodeError: continue
                            
                    if lines:
                        new_lines =[]
                        for line in lines:
                            parts = line.split('|', 1)
                            if len(parts) > 0:
                                row_id = parts[0].strip()
                                if row_id == filename or row_id == stem: continue 
                            new_lines.append(line)
                            
                        with open(metadata_path, 'w', encoding=used_enc) as f: f.writelines(new_lines)
                        
                        self.metadata.pop(filename, None)
                        self.metadata.pop(stem, None)

                del self.files[self.index]
                self.slider.config(to=max(1, len(self.files)))
                
                if self.index >= len(self.files) and self.index > 0:
                    self.index -= 1
                
                self.load_and_play()
                        
            except PermissionError: messagebox.showerror("Ошибка", "Файл занят. Попробуйте нажать стоп или перезапустить.")
            except Exception as e: print(f"Ошибка удаления: {e}")

    def finish(self):
        self.stop_tts()
        self.stop_audio()
        
        self.lbl_filename.config(text="ВСЕ ФАЙЛЫ ОБРАБОТАНЫ!", fg=self.FG_COLOR)
        self.lbl_transcription.config(text="")
        self.lbl_count.config(text="")
        self.slider.config(state=tk.DISABLED)
        self.canvas_orig.delete("all")
        self.canvas_tts.delete("all")
        self.current_file_path = None
        self.root.unbind_all('<Up>')
        self.root.unbind_all('<Down>')
        self.root.unbind_all('<space>')
        self.root.unbind_all('<Right>')
        self.root.unbind_all('<Left>')
        self.root.unbind_all('<Key>')
        self.root.unbind_all('<Control_R>')
        self.root.unbind_all('<0>')

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioCleaner(root)
    root.mainloop()
