import tkinter as tk
from tkinter import messagebox
import wave
import array
import os
import time
import threading
import sounddevice as sd

class FastAudioCutter:
    def __init__(self, parent=None, file_to_open=None, on_close_callback=None, on_save_callback=None):
        if parent:
            self.root = tk.Toplevel(parent)
            self.root.transient(parent)
            self.root.grab_set() 
        else:
            self.root = tk.Tk()

        self.root.title("Fast Cutter")
        self.on_close_callback = on_close_callback
        self.on_save_callback = on_save_callback # Новый колбэк для обновления главной волнограммы

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

        # --- ИНТЕРФЕЙС ---
        self.canvas = tk.Canvas(self.root, bg="#1e1e1e", height=120, highlightthickness=0)
        self.canvas.pack(fill=tk.X, expand=False, padx=20, pady=20)

        self.bottom_frame = tk.Frame(self.root, bg="#131313")
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 20))

        self.btn_container = tk.Frame(self.bottom_frame, bg="#131313")
        self.btn_container.pack()

        btn_style = {"fg": "white", "relief": tk.FLAT, "padx": 20, "pady": 8, "font": ("Segoe UI", 10, "bold"), "activebackground": "#444444", "activeforeground": "white", "cursor": "hand2"}

        tk.Button(self.btn_container, text="[I]ns", command=self.insert_silence, bg="#482f4c", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="D[e]l", command=self.delete_selection, bg="#560000", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[S]ave", command=self.save_changes_only, bg="#3e3e3e", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[C]ut", command=self.auto_cut_silence, bg="#522a13", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text="[G]asp", command=self.kill_current_gasp, bg="#114444", **btn_style).pack(side=tk.LEFT, padx=4)
        tk.Button(self.btn_container, text=" ▶/⏹ ", command=self.toggle_playback, bg="#2c3e50", **btn_style).pack(side=tk.LEFT, padx=4)                  
        tk.Button(self.btn_container, text="Cancel", command=self.on_window_close, bg="#560000", **btn_style).pack(side=tk.LEFT, padx=4)

        self.info_text = "Space: Play | W: Close | G/Q: Gasp Kill | C: AutoCut | I/V: Silence | E/Del: Delete | Z: Undo"
        self.lbl_info = tk.Label(self.root, text=self.info_text, bg="#131313", fg="#26A269", font=("Arial", 9))
        self.lbl_info.pack(side=tk.BOTTOM, pady=5)

        self.canvas.bind("<Button-1>", self.on_mousedown)
        self.canvas.bind("<B1-Motion>", self.on_mousemove)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouseup)
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

        # Используем Keycodes (Win, Linux, Mac) + English Keysym
        is_z = kc in (90, 52, 6)  or ks == 'z'      # Undo
        is_s = kc in (83, 39, 1)  or ks == 's'      # Save
        is_w = kc in (87, 25, 13) or ks == 'w'      # Close
        is_q = kc in (81, 24, 16) or ks == 'q'      # Gasp (дубль для G)
        is_e = kc in (69, 26, 14) or ks == 'e'      # Delete
        is_c = kc in (67, 54, 8)  or ks == 'c'      # Auto Cut
        is_i = kc in (73, 86, 34, 46) or ks in ('i', 'v') # Insert (I или V)
        is_g = kc in (71, 34, 5)  or ks == 'g'      # Gasp (оригинал)
        is_space = ks == 'space' or kc == 32        # Playback

        if is_z: self.undo()
        elif is_s: self.save_changes_only()
        elif is_w: self.on_window_close()
        elif is_q or is_g: self.kill_current_gasp() # Теперь и Q, и G режут вздохи
        elif is_e: self.delete_selection()
        elif is_c: self.auto_cut_silence()
        elif is_i: self.insert_silence()
        elif is_space: self.toggle_playback()
        else: return
        
        return "break"

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
            
            # Обновляем путь к файлу при перелистывании!
            self.file_path = path 
            
            self.cursor_pos = self.playback_origin = 0
            self.sel_start = self.sel_end = None
            self.history.clear()
            self.lbl_info.config(text=f"File: {os.path.basename(path)} | {self.SAMPLE_RATE}Hz | Length: {self.get_duration_str()}", fg="#26A269")
            self.draw_wave()
        except Exception as e:
            self.lbl_info.config(text=f"Error: {e}", fg="#e74c3c")

    def draw_wave(self):
        self.canvas.delete("all")
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 1 or not self.samples: return
        
        mid, scale, step = h/2, (h/2)/32768, max(1, len(self.samples)//w)
        total_samples = len(self.samples)
        total_seconds = total_samples / self.SAMPLE_RATE

        if self.sel_start is not None and self.sel_end is not None:
            x1, x2 = (self.sel_start / total_samples) * w, (self.sel_end / total_samples) * w
            self.canvas.create_rectangle(x1, 0, x2, h, fill="#2c3e50", outline="")
            self.canvas.create_text((x1 + x2) / 2, 20, text=f"{(abs(self.sel_end - self.sel_start) / self.SAMPLE_RATE) * 1000:.0f} ms", fill="#00FFCC", font=("Consolas", 10, "bold"))

        t, grid_step = 0, 0.1 if total_seconds < 2 else 0.5 if total_seconds < 5 else 1.0
        while t <= total_seconds:
            tx = (t * self.SAMPLE_RATE / total_samples) * w
            self.canvas.create_line(tx, h-20, tx, h, fill="#444444")
            if t > 0: self.canvas.create_text(tx, h-10, text=f"{t:.1f}s", fill="#666666", font=("Arial", 7))
            t += grid_step

        pts = []
        for x in range(w):
            chunk = self.samples[x*step : (x+1)*step]
            if not chunk: break
            pts.extend([x, mid - max(chunk)*scale, x, mid - min(chunk)*scale])
        if len(pts) >= 4: self.canvas.create_line(pts, fill="#26A269")
        cx = (self.cursor_pos / total_samples) * w
        self.canvas.create_line(cx, 0, cx, h, fill="#e74c3c", width=2, tags="cursor")

    def toggle_playback(self, event=None):
        if self.is_playing: self.stop_audio()
        else: self.start_audio()
        return "break"

    def start_audio(self):
        if self.is_playing or not self.samples: return
        
        # Если курсор в самом конце (или очень близко к нему), начинаем сначала
        if self.cursor_pos >= len(self.samples) - 1: 
            self.cursor_pos = 0
            
        self.is_playing = True
        self.playback_origin = self.cursor_pos
        threading.Thread(target=self._play_thread, daemon=True).start()
        self.update_ui_loop()

    def _play_thread(self):
        with self.audio_lock:
            curr = self.cursor_pos
            try:
                self.stream = sd.RawOutputStream(samplerate=self.SAMPLE_RATE, channels=self.n_channels, dtype='int16', blocksize=self.CHUNK)
                self.stream.start()
                while self.is_playing and curr < len(self.samples):
                    end = min(curr + self.CHUNK, len(self.samples))
                    data = self.samples[curr:end].tobytes()
                    try:
                        if self.stream and self.stream.active: 
                            self.stream.write(data)
                        curr = end
                        # Если поток прервали извне (is_playing=False), курсор больше не двигаем
                        if self.is_playing:
                            self.cursor_pos = curr
                    except: break
            except: pass
            finally:
                if self.stream:
                    try: self.stream.stop(); self.stream.close()
                    except: pass
                    self.stream = None
                
                # Если доиграли до конца естественным путем (нас не прерывали)
                if curr >= len(self.samples) and self.is_playing:
                    self.cursor_pos = len(self.samples)
                
                self.is_playing = False
                self.root.after(0, self.draw_wave)

    def stop_audio(self, return_to_origin=True):
        if not self.is_playing: return
        self.is_playing = False
        
        # Ждем завершения потока
        timeout = 0
        while self.audio_lock.locked() and timeout < 20:
            time.sleep(0.005)
            timeout += 1
            
        # Возвращаем курсор на старт ТОЛЬКО если нас об этом попросили
        if return_to_origin:
            self.cursor_pos = self.playback_origin
            
        self.draw_wave()

    def update_ui_loop(self):
        if not self.is_playing: return
        w = self.canvas.winfo_width()
        if len(self.samples) > 0:
            cx = (self.cursor_pos / len(self.samples)) * w
            self.canvas.coords("cursor", cx, 0, cx, self.canvas.winfo_height())
        self.root.after(16, self.update_ui_loop)

    def _push_history(self):
        self.history.append((array.array('h', self.samples), self.cursor_pos, self.playback_origin))
        if len(self.history) > 10: self.history.pop(0)

    def undo(self):
        if not self.history: return
        self.samples, self.cursor_pos, self.playback_origin = self.history.pop()
        self.sel_start = self.sel_end = None
        self.draw_wave()
        self.lbl_info.config(text=f"Undo Done | Length: {self.get_duration_str()}", fg="#FFCC00")

    def insert_silence(self):
        if not self.samples: return
        self._push_history()
        silence = array.array('h', [0] * int(0.05 * self.SAMPLE_RATE))
        self.samples = self.samples[:self.cursor_pos] + silence + self.samples[self.cursor_pos:]
        self.cursor_pos += len(silence)
        self.draw_wave()
        self.lbl_info.config(text=f"Silence inserted | Length: {self.get_duration_str()}", fg="#FFCC00")
        
    def delete_selection(self):
        if self.sel_start is None or self.sel_end is None: return
        self._push_history() 
        s, e = min(self.sel_start, self.sel_end), max(self.sel_start, self.sel_end)
        self.samples = self.samples[:s] + self.samples[e:]
        self.cursor_pos = s
        self.sel_start = self.sel_end = None
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
            self.draw_wave()
            self.lbl_info.config(text=f"Gasp killed | Length: {self.get_duration_str()}", fg="#FFCC00")

    def auto_cut_silence(self):
        if not self.samples or len(self.samples) < 100: return
        
        # 1. БЕЗОПАСНЫЕ ИНДЕКСЫ
        # Если курсор в самом конце (len), сдвигаем его на 1 сэмпл назад для расчетов
        curr = min(self.cursor_pos, len(self.samples) - 1)
        if curr < 0: curr = 0

        # 2. ПОИСК ГРАНИЦ ТИШИНЫ
        s, e = curr, curr
        
        # Идем назад, пока тишина (и пока не упремся в начало или в громкий звук)
        while s > 0 and abs(self.samples[s]) < self.THRESHOLD: 
            s -= 1
        
        # Идем вперед, пока тишина (и пока не упремся в конец или в громкий звук)
        while e < len(self.samples) - 1 and abs(self.samples[e]) < self.THRESHOLD: 
            e += 1

        # Настройки зазоров
        current_gap_ms = self.GAP_MS  # Стандартный (55 мс)
        edge_gap_ms = 25             # Зазор для краев (25 мс)
        
        mid_gap = int(current_gap_ms / 1000 * self.SAMPLE_RATE)
        edge_gap = int(edge_gap_ms / 1000 * self.SAMPLE_RATE)

        # 3. ОПРЕДЕЛЯЕМ ТОЧКИ РЕЗА (cf -> ct)
        # Если тишина упирается в начало файла
        if s == 0:
            cf = edge_gap
        else:
            cf = s + mid_gap
            
        # Если тишина упирается в конец файла
        if e >= len(self.samples) - 1:
            ct = len(self.samples) - edge_gap
        else:
            ct = e - mid_gap

        # 4. ПРОВЕРКА И КОРРЕКЦИЯ (ATTEMPTS)
        # Если зазоры слишком большие и ct оказался левее cf, пробуем уменьшить
        for attempt in range(3):
            if ct > cf:
                # ВЫПОЛНЯЕМ РЕЗ
                self._push_history()
                cut_len = ct - cf
                
                self.samples = self.samples[:cf] + self.samples[ct:]
                
                # Корректируем курсор (если он был в вырезанной части или после нее)
                if self.cursor_pos > cf:
                    self.cursor_pos = max(cf, self.cursor_pos - cut_len)
                
                # То же самое для точки начала воспроизведения
                if self.playback_origin > cf:
                    self.playback_origin = max(cf, self.playback_origin - cut_len)

                self.draw_wave()
                self.lbl_info.config(
                    text=f"Cut {cut_len/self.SAMPLE_RATE:.3f}s silence | Total: {self.get_duration_str()}", 
                    fg="#FFCC00"
                )
                return
            
            # Уменьшаем зазоры для следующей попытки
            mid_gap = int(mid_gap / 2)
            edge_gap = max(0, edge_gap - int(10 / 1000 * self.SAMPLE_RATE))
            # Пересчитываем cf и ct с новыми зазорами
            cf = (s + mid_gap) if s > 0 else edge_gap
            ct = (len(self.samples) - edge_gap) if e >= len(self.samples)-1 else (e - mid_gap)

        self.lbl_info.config(text="Nothing to cut (silence too short or threshold low)", fg="#FF5555")

    def save_changes_only(self):
        if not self.file_path or not self.samples: return
        try:
            with wave.open(self.file_path, 'wb') as wf:
                wf.setnchannels(self.n_channels); wf.setsampwidth(2); wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes(self.samples.tobytes())
            self.lbl_info.config(text=f"File Saved Successfully! | {self.get_duration_str()}", fg="#FFCC00")
            
            # Уведомляем главное окно, что файл на диске изменился
            if self.on_save_callback:
                self.on_save_callback()
                
        except Exception as e: messagebox.showerror("Error", str(e))

    def on_mousedown(self, event):
        if not self.samples: return
        # Останавливаем аудио, запрещая возврат на точку старта
        self.stop_audio(return_to_origin=False)
        
        self.cursor_pos = self.sel_start = int((event.x / self.canvas.winfo_width()) * len(self.samples))
        self.sel_end = None
        self.draw_wave()

    def on_mousemove(self, event):
        if not self.samples: return
        self.sel_end = int((max(0, min(event.x, self.canvas.winfo_width())) / self.canvas.winfo_width()) * len(self.samples))
        self.draw_wave()

    def on_mouseup(self, event):
        if self.sel_end is not None and abs(self.sel_end - self.sel_start) < (self.SAMPLE_RATE * 0.02):
            self.sel_start = self.sel_end = None
        self.draw_wave()