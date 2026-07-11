import sys
import ctypes
import tkinter as tk
from tkinter import messagebox
import wave
import array
import os
import time
import threading
import sounddevice as sd
import math

# --- ФУНКЦИЯ ДЛЯ ТЕМНОГО ЗАГОЛОВКА ---
def apply_dark_title_bar(window):
    """Принудительно включает темную тему для заголовка окна в Windows 10/11."""
    if sys.platform.startswith("win"):
        try:
            window.update()
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
            value = ctypes.c_int(1)
            
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 
                DWMWA_USE_IMMERSIVE_DARK_MODE, 
                ctypes.byref(value), 
                ctypes.sizeof(value)
            )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 
                DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 
                ctypes.byref(value), 
                ctypes.sizeof(value)
            )
        except Exception:
            pass

class FastAudioCutter:
    def __init__(self, parent=None, file_to_open=None, on_close_callback=None, on_save_callback=None, on_split_callback=None, on_merge_callback=None, on_merge_next_callback=None, on_merge_next_full_callback=None):
        if parent:
            self.root = tk.Toplevel(parent)
            self.root.transient(parent)
            self.root.grab_set() 
        else:
            self.root = tk.Tk()

        self.root.title("Fast Cutter X")
        
        apply_dark_title_bar(self.root)

        self.on_close_callback = on_close_callback
        self.on_save_callback = on_save_callback 
        self.on_split_callback = on_split_callback
        self.on_merge_callback = on_merge_callback            # Коллбэк для слияния с прошлым файлом
        self.on_merge_next_callback = on_merge_next_callback  # Коллбэк для слияния со следующим файлом
        self.on_merge_next_full_callback = on_merge_next_full_callback # Коллбэк для полноценного объединения N и N+1

        # --- ГЕОМЕТРИЯ ---
        width = 1000
        height = 250
        
        if parent:
            parent.update_idletasks()
            p_w = parent.winfo_width()
            p_x = parent.winfo_x()
            p_y = parent.winfo_y()
            x = p_x + (p_w // 2) - (width // 2)
            y = p_y - 20
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            self.root.geometry(f"{width}x{height}")

        self.root.configure(bg="#131313")
        
        # --- НАСТРОЙКИ ---
        self.THRESHOLD = 700  
        self.GAP_MS = 55      
        self.SAMPLE_RATE = 22050
        self.CHUNK = 1024 
        self.stream = None
        self.audio_lock = threading.Lock() 

        # --- СОСТОЯНИЯ ---
        self.file_path = file_to_open
        self.samples = array.array('h')
        self.n_channels = 1
        self.cursor_pos = 0       
        self.playback_origin = 0  
        self.sel_start = None     
        self.sel_end = None       
        self.is_playing = False
        self.history = [] 

        # Состояния зума
        self.zoom_start = 0
        self.zoom_end = None

        # --- ИНТЕРФЕЙС ---
        self.canvas = tk.Canvas(self.root, bg="#1e1e1e", height=120, highlightthickness=0)
        self.canvas.pack(fill=tk.X, expand=False, padx=20, pady=20)

        self.bottom_frame = tk.Frame(self.root, bg="#131313")
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 20))

        self.btn_container = tk.Frame(self.bottom_frame, bg="#131313")
        self.btn_container.pack()

        # takefocus=0 отключает захват стрелок кнопками, фокус всегда в окне
        btn_style = {"fg": "white", "relief": tk.FLAT, "padx": 15, "pady": 8, "font": ("Segoe UI", 10, "bold"), "activebackground": "#444444", "activeforeground": "white", "cursor": "hand2", "takefocus": 0}

        tk.Button(self.btn_container, text="[M]rg", command=self.trigger_merge_next_full, bg="#311946", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[I]ns", command=self.insert_silence, bg="#482f4c", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="D[e]l", command=self.delete_selection, bg="#560000", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[S]ave", command=self.save_changes_only, bg="#3e3e3e", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[X]prt", command=self.trigger_split, bg="#1c3c5c", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[C]ut", command=self.auto_cut_silence, bg="#522a13", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[G]asp", command=self.kill_current_gasp, bg="#114444", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text=" ▶/⏹ ", command=self.toggle_playback, bg="#2c3e50", **btn_style).pack(side=tk.LEFT, padx=4)                  
        tk.Button(self.btn_container, text="Cancel", command=self.on_window_close, bg="#560000", **btn_style).pack(side=tk.LEFT, padx=4)

        self.info_text = "Space: Play | RMB: Zoom In/Out | ⬅/➡: ±10ms | X: Split | B: Prepend | N: Append | G: Gasp | C: AutoCut | I/V: Silence | Del: Delete"
        self.lbl_info = tk.Label(self.root, text=self.info_text, bg="#131313", fg="#26A269", font=("Arial", 9))
        self.lbl_info.pack(side=tk.BOTTOM, pady=5)

        self.canvas.bind("<Button-1>", self.on_mousedown)
        self.canvas.bind("<B1-Motion>", self.on_mousemove)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouseup)
        self.canvas.bind("<Shift-Button-1>", self.on_shift_mousedown)
        self.canvas.bind("<Button-3>", self.toggle_zoom)
        self.root.bind("<KeyPress>", self.on_hotkey)
        self.root.bind("<Delete>", lambda e: self.delete_selection())
        self.root.bind("<Insert>", lambda e: self.insert_silence()) 
        self.root.bind("<Escape>", lambda e: self.on_window_close())
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        if self.file_path:
            self.root.after(200, lambda: self.load_file(self.file_path))
        self.root.after(100, self.root.focus_force)

    def on_hotkey(self, event):
        kc = event.keycode
        ks = event.keysym.lower()

        # 1. Сначала проверяем точечные системные клавиши (исключает конфликты кодов Windows)
        if ks == 'left': 
            self.move_cursor_ms(-10)
            return "break"
        elif ks == 'right': 
            self.move_cursor_ms(10)
            return "break"
        elif ks == 'space': 
            self.toggle_playback()
            return "break"

        # 2. Буквенные шорткаты
        is_z = kc in (90, 52, 6)  or ks == 'z'      
        is_s = kc in (83, 39, 1)  or ks == 's'      
        is_w = kc in (87, 25, 13) or ks == 'w'      
        is_q = kc in (81, 24, 16) or ks == 'q'      
        is_e = kc in (69, 26, 14) or ks == 'e'      
        is_c = kc in (67, 54, 8)  or ks == 'c'      
        is_i = kc in (73, 86, 34, 46) or ks in ('i', 'v') 
        is_g = kc in (71, 34, 5)  or ks == 'g'      
        is_x = kc in (88, 27, 7)  or ks == 'x'
        is_b = kc in (66, 56, 11) or ks in ('b', 'cyrillic_i', 'и')
        is_n = kc in (78, 57, 45) or ks in ('n', 'cyrillic_te', 'т')

        if is_z: self.undo()
        elif is_s: self.save_changes_only()
        elif is_w: self.on_window_close()
        elif is_q or is_g: self.kill_current_gasp() 
        elif is_e: self.delete_selection()
        elif is_c: self.auto_cut_silence()
        elif is_i: self.insert_silence()
        elif is_x: self.trigger_split()
        elif is_b:
            if self.on_merge_callback:
                self.on_merge_callback()
                return "break"
        elif is_n:
            if self.on_merge_next_callback:
                self.on_merge_next_callback()
                return "break"
        else: return
        
        return "break"

    def get_view_bounds(self):
        """Возвращает текущие границы отображения сэмплов с учетом зума."""
        if not self.samples: return 0, 0
        zs = 0 if self.zoom_start is None else self.zoom_start
        ze = len(self.samples) if self.zoom_end is None else self.zoom_end
        if ze > len(self.samples): ze = len(self.samples)
        if zs > ze: zs = 0; ze = len(self.samples)
        return zs, ze

    def reset_zoom(self):
        self.zoom_start = 0
        self.zoom_end = None

    def toggle_zoom(self, event):
        """Зум ±200мс вокруг точки клика ПКМ, либо возврат к полному масштабу."""
        if not self.samples: return
        if self.zoom_end is not None:
            self.reset_zoom()
        else:
            w = self.canvas.winfo_width()
            click_sample = int((event.x / w) * len(self.samples))
            half_window = int(0.200 * self.SAMPLE_RATE)
            self.zoom_start = max(0, click_sample - half_window)
            self.zoom_end = min(len(self.samples), click_sample + half_window)
        self.draw_wave()

    def move_cursor_ms(self, ms):
        if not self.samples: return
        self.stop_audio(return_to_origin=False)
        delta = int((ms / 1000.0) * self.SAMPLE_RATE)
        self.cursor_pos = max(0, min(len(self.samples), self.cursor_pos + delta))
        self.draw_wave()

    def on_window_close(self):
        self.stop_audio() 
        if self.on_close_callback: self.on_close_callback()
        self.root.destroy()

    def get_duration_str(self):
        if not self.samples or self.SAMPLE_RATE == 0: return "0.000s"
        return f"{len(self.samples) / self.SAMPLE_RATE:.3f}s"

    def load_file(self, path):
        self.stop_audio()
        try:
            with wave.open(path, 'rb') as wf:
                self.SAMPLE_RATE = wf.getframerate()
                self.n_channels = wf.getnchannels()
                self.samples = array.array('h', wf.readframes(wf.getnframes()))
            
            self.file_path = path 
            self.cursor_pos = self.playback_origin = 0
            self.sel_start = self.sel_end = None
            self.reset_zoom()
            self.history.clear()
            self.lbl_info.config(text=f"File: {os.path.basename(path)} | {self.SAMPLE_RATE}Hz | Length: {self.get_duration_str()}", fg="#26A269")
            self.draw_wave()
        except Exception as e:
            self.lbl_info.config(text=f"Error: {e}", fg="#e74c3c")

    def draw_wave(self):
        self.canvas.delete("all")
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 1 or not self.samples: return
        
        zs, ze = self.get_view_bounds()
        view_samples = ze - zs
        if view_samples <= 0: return

        mid, scale, step = h/2, (h/2)/32768, max(1, view_samples//w)
        total_seconds = view_samples / self.SAMPLE_RATE

        if self.sel_start is not None and self.sel_end is not None:
            s_start, s_end = min(self.sel_start, self.sel_end), max(self.sel_start, self.sel_end)
            x1, x2 = ((s_start - zs) / view_samples) * w, ((s_end - zs) / view_samples) * w
            self.canvas.create_rectangle(x1, 0, x2, h, fill="#2c3e50", outline="")
            
            tx_x = (max(x1, 0) + min(x2, w)) / 2
            if tx_x < 0 or tx_x > w: tx_x = w / 2
            self.canvas.create_text(tx_x, 20, text=f"{(s_end - s_start) / self.SAMPLE_RATE * 1000:.0f} ms", fill="#00FFCC", font=("Consolas", 10, "bold"))

        t_start = zs / self.SAMPLE_RATE
        t_end = ze / self.SAMPLE_RATE
        grid_step = 0.05 if total_seconds < 0.5 else 0.1 if total_seconds < 2 else 0.5 if total_seconds < 5 else 1.0
        
        t = math.ceil(t_start / grid_step) * grid_step
        while t <= t_end:
            tx = ((t * self.SAMPLE_RATE - zs) / view_samples) * w
            self.canvas.create_line(tx, h-20, tx, h, fill="#444444")
            fmt = f"{t:.2f}s" if grid_step < 0.1 else f"{t:.1f}s"
            self.canvas.create_text(tx, h-10, text=fmt, fill="#666666", font=("Arial", 7))
            t += grid_step

        view_data = self.samples[zs:ze]
        pts = []
        for x in range(w):
            chunk = view_data[x*step : (x+1)*step]
            if not chunk: break
            pts.extend([x, mid - max(chunk)*scale, x, mid - min(chunk)*scale])
        if len(pts) >= 4: self.canvas.create_line(pts, fill="#26A269")
        
        cx = ((self.cursor_pos - zs) / view_samples) * w
        self.canvas.create_line(cx, 0, cx, h, fill="#e74c3c", width=2, tags="cursor")

    def toggle_playback(self, event=None):
        if self.is_playing: self.stop_audio()
        else: self.start_audio()
        return "break"

    def start_audio(self):
        if self.is_playing or not self.samples: return
        
        # Если есть выделение, корректируем стартовую позицию
        if self.sel_start is not None and self.sel_end is not None:
            s_start = min(self.sel_start, self.sel_end)
            s_end = max(self.sel_start, self.sel_end)
            # Если курсор вне выделения или дошел до конца выделения, 
            # начинаем воспроизведение с начала выделенного сегмента
            if self.cursor_pos < s_start or self.cursor_pos >= s_end:
                self.cursor_pos = s_start
        else:
            # Если выделения нет, играем до конца файла
            if self.cursor_pos >= len(self.samples) - 1: 
                self.cursor_pos = 0
            
        self.is_playing = True
        self.playback_origin = self.cursor_pos
        threading.Thread(target=self._play_thread, daemon=True).start()
        self.update_ui_loop()

    def _play_thread(self):
        with self.audio_lock:
            # Определяем точку окончания воспроизведения
            if self.sel_start is not None and self.sel_end is not None:
                play_end = max(self.sel_start, self.sel_end)
            else:
                play_end = len(self.samples)

            curr = self.cursor_pos
            try:
                self.stream = sd.RawOutputStream(samplerate=self.SAMPLE_RATE, channels=self.n_channels, dtype='int16', blocksize=self.CHUNK)
                self.stream.start()
                # Воспроизводим, пока не достигнем play_end
                while self.is_playing and curr < play_end:
                    end = min(curr + self.CHUNK, play_end)
                    data = self.samples[curr:end].tobytes()
                    try:
                        if self.stream and self.stream.active: 
                            self.stream.write(data)
                        curr = end
                        if self.is_playing:
                            self.cursor_pos = curr
                    except: break
            except: pass
            finally:
                if self.stream:
                    try: self.stream.stop(); self.stream.close()
                    except: pass
                    self.stream = None
                
                # Если доиграли до конца участка, оставляем курсор на границе окончания
                if curr >= play_end and self.is_playing:
                    self.cursor_pos = play_end
                
                self.is_playing = False
                self.root.after(0, self.draw_wave)

    def stop_audio(self, return_to_origin=True):
        if not self.is_playing: return
        self.is_playing = False
        
        timeout = 0
        while self.audio_lock.locked() and timeout < 20:
            time.sleep(0.005)
            timeout += 1
            
        if return_to_origin:
            self.cursor_pos = self.playback_origin
            
        self.draw_wave()

    def update_ui_loop(self):
        if not self.is_playing: return
        w = self.canvas.winfo_width()
        if len(self.samples) > 0:
            zs, ze = self.get_view_bounds()
            view_samples = ze - zs
            cx = ((self.cursor_pos - zs) / view_samples) * w
            self.canvas.coords("cursor", cx, 0, cx, self.canvas.winfo_height())
        self.root.after(16, self.update_ui_loop)

    def _push_history(self):
        self.history.append((array.array('h', self.samples), self.cursor_pos, self.playback_origin))
        if len(self.history) > 10: self.history.pop(0)

    def undo(self):
        if not self.history: return
        self.samples, self.cursor_pos, self.playback_origin = self.history.pop()
        self.sel_start = self.sel_end = None
        self.reset_zoom()
        self.draw_wave()
        self.lbl_info.config(text=f"Undo Done | Length: {self.get_duration_str()}", fg="#FFCC00")

    def insert_silence(self):
        if not self.samples: return
        self._push_history()
        silence = array.array('h', [0] * int(0.05 * self.SAMPLE_RATE))
        self.samples = self.samples[:self.cursor_pos] + silence + self.samples[self.cursor_pos:]
        self.cursor_pos += len(silence)
        self.reset_zoom()
        self.draw_wave()
        self.lbl_info.config(text=f"Silence inserted | Length: {self.get_duration_str()}", fg="#FFCC00")
        
    def delete_selection(self):
        if self.sel_start is None or self.sel_end is None: return
        self._push_history() 
        s, e = min(self.sel_start, self.sel_end), max(self.sel_start, self.sel_end)
        self.samples = self.samples[:s] + self.samples[e:]
        self.cursor_pos = s
        self.sel_start = self.sel_end = None
        self.reset_zoom()
        self.draw_wave()
        self.lbl_info.config(text=f"Deleted selection | Length: {self.get_duration_str()}", fg="#FFCC00")

    def kill_current_gasp(self, event=None):
        if not self.samples: return
        self._push_history()
        SIL, SPCH = 180, 2500
        win = int(0.015 * self.SAMPLE_RATE) 
        step = max(1, int(win / 4))
        start = end = self.cursor_pos
        for i in range(self.cursor_pos, win, -step):
            if max(abs(s) for s in self.samples[i-win : i]) < SIL: break
            if max(abs(s) for s in self.samples[i-win : i]) > SPCH: break
            start = i
        for i in range(self.cursor_pos, len(self.samples) - win, step):
            if max(abs(s) for s in self.samples[i : i+win]) < SIL: break
            if max(abs(s) for s in self.samples[i : i+win]) > SPCH: break
            end = i
        if end > start:
            for j in range(start, end): self.samples[j] = 0
            self.reset_zoom()
            self.draw_wave()
            self.lbl_info.config(text=f"Gasp killed | Length: {self.get_duration_str()}", fg="#FFCC00")

    def auto_cut_silence(self):
        if not self.samples or len(self.samples) < 100: return
        
        curr = min(self.cursor_pos, len(self.samples) - 1)
        if curr < 0: curr = 0
        s, e = curr, curr
        while s > 0 and abs(self.samples[s]) < self.THRESHOLD: 
            s -= 1
        while e < len(self.samples) - 1 and abs(self.samples[e]) < self.THRESHOLD: 
            e += 1

        mid_gap_orig = int(self.GAP_MS / 1000 * self.SAMPLE_RATE) 
        edge_gap_orig = int(25 / 1000 * self.SAMPLE_RATE)         

        tail_gap = int(mid_gap_orig / 0.3) 
        pre_gap = mid_gap_orig 
        edge_gap = edge_gap_orig

        for attempt in range(3):
            cf = (s + tail_gap) if s > 0 else edge_gap
            ct = (len(self.samples) - edge_gap) if e >= len(self.samples)-1 else (e - pre_gap)

            if ct > cf:
                self._push_history()
                cut_len = ct - cf
                self.samples = self.samples[:cf] + self.samples[ct:]
                
                if self.cursor_pos > cf:
                    self.cursor_pos = max(cf, self.cursor_pos - cut_len)
                if self.playback_origin > cf:
                    self.playback_origin = max(cf, self.playback_origin - cut_len)

                self.reset_zoom()
                self.draw_wave()
                self.lbl_info.config(
                    text=f"Cut {cut_len/self.SAMPLE_RATE:.3f}s | Tail: {int(tail_gap/self.SAMPLE_RATE*1000)}ms", 
                    fg="#FFCC00"
                )
                return
            
            tail_gap //= 2
            pre_gap //= 2
            edge_gap //= 2

        self.lbl_info.config(text="Nothing to cut", fg="#FF5555")

    def save_changes_only(self):
        if not self.file_path or not self.samples: return
        try:
            with wave.open(self.file_path, 'wb') as wf:
                wf.setnchannels(self.n_channels); wf.setsampwidth(2); wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes(self.samples.tobytes())
            self.lbl_info.config(text=f"File Saved Successfully! | {self.get_duration_str()}", fg="#FFCC00")
            
            if self.on_save_callback:
                self.on_save_callback()
                
        except Exception as e: messagebox.showerror("Error", str(e))

    def trigger_split(self):
        """Передает в основную программу текущую позицию курсора и массив аудио"""
        if not self.samples or self.cursor_pos <= 100 or self.cursor_pos >= len(self.samples) - 100:
            self.lbl_info.config(text="Курсор слишком близко к краям для разреза!", fg="#FF5555")
            return
            
        if self.on_split_callback:
            self.stop_audio(return_to_origin=False)
            self.on_split_callback(self.cursor_pos, self.samples, self.n_channels, self.SAMPLE_RATE)

    def trigger_merge_next_full(self):
        """Запускает процесс полного слияния со следующим аудиофайлом"""
        if self.on_merge_next_full_callback:
            self.stop_audio(return_to_origin=False)
            self.on_merge_next_full_callback()

    def on_mousedown(self, event):
        if not self.samples: return
        self.stop_audio(return_to_origin=False)
        
        zs, ze = self.get_view_bounds()
        view_samples = ze - zs
        click_idx = zs + int((event.x / self.canvas.winfo_width()) * view_samples)
        
        self.cursor_pos = self.sel_start = max(0, min(len(self.samples), click_idx))
        self.sel_end = None
        self.draw_wave()

    def on_shift_mousedown(self, event):
        """Выделяет сегмент от текущей позиции красного курсора до места Shift-клика"""
        if not self.samples: return
        self.stop_audio(return_to_origin=False)
        
        # Переводим координаты клика по горизонтали в сэмплы
        zs, ze = self.get_view_bounds()
        view_samples = ze - zs
        click_idx = zs + int((event.x / self.canvas.winfo_width()) * view_samples)
        click_idx = max(0, min(len(self.samples), click_idx))
        
        # Начало выделения — старый курсор, конец — текущий клик
        self.sel_start = self.cursor_pos
        self.sel_end = click_idx
        self.draw_wave()

    def on_mousemove(self, event):
        if not self.samples: return
        zs, ze = self.get_view_bounds()
        view_samples = ze - zs
        x_clamped = max(0, min(event.x, self.canvas.winfo_width()))
        click_idx = zs + int((x_clamped / self.canvas.winfo_width()) * view_samples)
        
        self.sel_end = max(0, min(len(self.samples), click_idx))
        self.draw_wave()

    def on_mouseup(self, event):
        if self.sel_end is not None and abs(self.sel_end - self.sel_start) < (self.SAMPLE_RATE * 0.005):
            self.sel_start = self.sel_end = None
        self.draw_wave()