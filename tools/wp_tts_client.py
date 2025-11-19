import asyncio
import queue
import threading
import sys
import re
import time
from typing import Optional

# --- Импорты из библиотеки Wyoming ---
try:
    from wyoming.client import AsyncTcpClient
    from wyoming.tts import (
        SynthesizeStart,
        SynthesizeChunk,
        SynthesizeStop,
        SynthesizeVoice,
        SynthesizeStopped
    )
    from wyoming.audio import AudioChunk, AudioStop
except ImportError:
    print("Ошибка: Не найдена библиотека wyoming.", file=sys.stderr)
    sys.exit(1)

# Аудио
try:
    import pyaudio
except ImportError:
    print("Ошибка: pip install pyaudio", file=sys.stderr)
    sys.exit(1)

# GUI
try:
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except ImportError:
    sys.exit(1)

try:
    from tkinterdnd2 import DND_TEXT, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# Константы
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10200
DEFAULT_VOICE = "ru_RU-sushko200-medium_epoch4389"
AUDIO_STOP = object()
SAMPLE_RATE = 22050
SAMPLE_WIDTH = 2
CHANNELS = 1
PLAYER_CHUNK_SIZE = 1024


class AudioPlayer:
    """
    Плеер с поддержкой мгновенного стопа и ПАУЗЫ.
    """
    def __init__(self, audio_queue, log_callback):
        self.queue = audio_queue
        self.log = log_callback
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
    def __init__(self, host, port, audio_queue, player, log_callback):
        self.host = host
        self.port = port
        self.audio_queue = audio_queue
        self.player = player
        self.log = log_callback
        self._thread = None
        self._stop_event = None
        self.start_time = 0

    def start_synthesis(self, text, voice_name):
        self.stop()
        self.player.reset()
        self.start_time = time.time()
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

    def set_pause(self, paused: bool):
        if paused: self.player.pause()
        else: self.player.resume()

    def _run_async_loop(self, text, voice_name):
        try:
            asyncio.run(self._async_session(text, voice_name))
        except Exception as e:
            self.log(f"Async Loop Error: {e}", "error")

    async def _async_session(self, text, voice_name):
        self.log(f"Подключение ({voice_name})...", "status")
        try:
            async with AsyncTcpClient(self.host, self.port) as client:
                voice = SynthesizeVoice(name=voice_name)
                await client.write_event(SynthesizeStart(voice=voice).event())
                
                send_task = asyncio.create_task(self._send_chunks(client, text))
                read_task = asyncio.create_task(self._read_events(client))
                
                await asyncio.gather(send_task, read_task)
                
        except (OSError, ConnectionRefusedError):
            self.log("Не удалось подключиться к серверу.", "error")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._stop_event.is_set():
                self.log(f"Ошибка сессии: {e}", "error")

    async def _send_chunks(self, client, text):
        chunks = re.split(r'([.,!?;:\n]+)', text)
        to_send = []
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
        total_bytes = 0
        first_chunk_received = False
        
        while not self._stop_event.is_set():
            event = await client.read_event()
            if event is None: break

            if AudioChunk.is_type(event.type):
                chunk = AudioChunk.from_event(event)
                if not first_chunk_received:
                    ttfa = (time.time() - self.start_time) * 1000
                    self.log(f"⚡ Старт звука: {ttfa:.0f} мс", "success")
                    first_chunk_received = True
                total_bytes += len(chunk.audio)
                self.audio_queue.put(chunk.audio)
            
            elif SynthesizeStopped.is_type(event.type):
                duration_sec = total_bytes / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
                self.log(f"✅ Готово. Размер: {total_bytes/1024:.1f} Кб ({duration_sec:.2f} сек)", "log")
                break


# --- GUI ---
class VoskLibraryClientGUI:
    def __init__(self, host, port, voice):
        self.host = host
        self.port = port
        self.voice = voice
        
        if DND_AVAILABLE:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
            
        self.root.title("Wyoming TTS Client")
        self.root.geometry("650x650")
        
        self.root.attributes('-topmost', True)
        self.log_queue = queue.Queue()
        self.audio_queue = queue.Queue()
        self.is_paused = False
        
        self.player = AudioPlayer(self.audio_queue, self.log_to_gui)
        self.wyoming_manager = AsyncWyomingManager(host, port, self.audio_queue, self.player, self.log_to_gui)

        self._init_ui()
        self.root.after(100, self._process_log_queue)

    def _init_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 1. Текст
        text_frame = ttk.LabelFrame(main_frame, text="Текст", height=200)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        self.text_area = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD)
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.text_area.bind("<Control-v>", self.handle_paste_event)
        self.text_area.bind("<Control-a>", self.select_all_text)
        if DND_AVAILABLE:
            self.text_area.drop_target_register(DND_TEXT)
            self.text_area.dnd_bind('<<Drop>>', self.handle_drop)

        # 2. ПЕРВАЯ СТРОКА УПРАВЛЕНИЯ
        row1_frame = ttk.Frame(main_frame)
        row1_frame.pack(fill=tk.X, pady=2)

        ttk.Button(row1_frame, text="Play 1", command=self.start_synthesis_1, width=8).pack(side=tk.LEFT, padx=(0, 2))
        self.btn_pause = ttk.Button(row1_frame, text="||", command=self.toggle_pause, width=4)
        self.btn_pause.pack(side=tk.LEFT, padx=2)
        ttk.Button(row1_frame, text="Stop", command=self.stop_synthesis, width=8).pack(side=tk.LEFT, padx=2)
        
        ttk.Label(row1_frame, text=" | ").pack(side=tk.LEFT)
        ttk.Button(row1_frame, text="Insert", command=self.paste_from_clipboard).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1_frame, text="Clear", command=self.clear_text).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1_frame, text=" | ").pack(side=tk.LEFT)
        
        ttk.Label(row1_frame, text="Voice 1:").pack(side=tk.LEFT, padx=(5, 2))
        self.voice_var_1 = tk.StringVar(value=self.voice)
        ttk.Entry(row1_frame, textvariable=self.voice_var_1).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 3. ВТОРАЯ СТРОКА УПРАВЛЕНИЯ
        row2_frame = ttk.Frame(main_frame)
        row2_frame.pack(fill=tk.X, pady=2)

        ttk.Button(row2_frame, text="Play 2", command=self.start_synthesis_2, width=8).pack(side=tk.LEFT, padx=(0, 2))
        
        # Спейсер, чтобы выровнять поле Voice 2 под Voice 1
        spacer = ttk.Label(row2_frame, text="                                                                                                ")
        spacer.pack(side=tk.LEFT, padx=2)

        ttk.Label(row2_frame, text="Voice 2:").pack(side=tk.LEFT, padx=(5, 2))
        self.voice_var_2 = tk.StringVar(value=self.voice)
        ttk.Entry(row2_frame, textvariable=self.voice_var_2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 4. Лог
        log_frame = ttk.LabelFrame(main_frame, text="Лог", height=150)
        log_frame.pack(fill=tk.X, pady=5)
        self.log_area = scrolledtext.ScrolledText(log_frame, height=8, state=tk.DISABLED)
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN).pack(side=tk.BOTTOM, fill=tk.X)

        self.log_area.tag_config("error", foreground="red")
        self.log_area.tag_config("success", foreground="green", font=("Segoe UI", 9, "bold"))
        self.log_area.tag_config("info", foreground="blue")

    # --- ЛОГИКА ---
    
    def start_synthesis_generic(self, voice_key):
        """Общий метод запуска"""
        text = self.text_area.get("1.0", tk.END).strip()
        if not text: return
        
        # Сброс UI
        self.is_paused = False
        self.btn_pause.config(text="||")
        
        # Получаем нужный голос
        voice_name = self.voice_var_1.get() if voice_key == 1 else self.voice_var_2.get()
        
        self.log_to_gui(f"Запуск (Голос {voice_key})...", "log")
        self.wyoming_manager.start_synthesis(text, voice_name)

    def start_synthesis_1(self):
        self.start_synthesis_generic(1)

    def start_synthesis_2(self):
        self.start_synthesis_generic(2)

    def stop_synthesis(self):
        self.wyoming_manager.stop()
        self.log_to_gui("СТОП.", "log")
        self.is_paused = False
        self.btn_pause.config(text="||")

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.wyoming_manager.set_pause(True)
            self.btn_pause.config(text="▶")
            self.log_to_gui("Пауза", "info")
        else:
            self.wyoming_manager.set_pause(False)
            self.btn_pause.config(text="||")
            self.log_to_gui("Продолжаем...", "info")

    # --- Хелперы ---
    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
            if text:
                self.text_area.delete(1.0, tk.END) 
                self.text_area.insert(tk.END, text)
        except: pass

    def handle_paste_event(self, event=None):
        try:
            text = self.root.clipboard_get()
            if text: event.widget.insert(tk.INSERT, text)
        except: pass
        return "break"

    def clear_text(self):
        self.text_area.delete(1.0, tk.END)

    def select_all_text(self, event=None):
        self.text_area.tag_add(tk.SEL, "1.0", tk.END)
        return "break"

    def handle_drop(self, event):
        self.text_area.delete("1.0", tk.END)
        data = event.data
        if data.startswith('{') and data.endswith('}'): data = data[1:-1]
        self.text_area.insert("1.0", data)

    def log_to_gui(self, msg, tag="normal"):
        self.log_queue.put((msg, tag))

    def _process_log_queue(self):
        try:
            while True:
                msg, tag = self.log_queue.get_nowait()
                if tag == "status":
                    self.status_var.set(msg)
                else:
                    self.log_area.config(state=tk.NORMAL)
                    self.log_area.insert(tk.END, f"{msg}\n", tag)
                    self.log_area.see(tk.END)
                    self.log_area.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._process_log_queue)


if __name__ == "__main__":
    app = VoskLibraryClientGUI(DEFAULT_HOST, DEFAULT_PORT, DEFAULT_VOICE)
    app.root.mainloop()