import os
import pygame
import tkinter as tk
from tkinter import filedialog, messagebox

class AudioCleaner:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Dataset Cleaner")
        self.root.geometry("520x320")
        
        # Фокус на окно для работы клавиш
        self.root.focus_force()
        
        # Инициализация звука
        try:
            pygame.mixer.init()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось инициализировать аудио: {e}")
            root.destroy()
            return

        # Выбор папки
        self.folder_path = filedialog.askdirectory(title="Выберите папку с датасетом")
        if not self.folder_path:
            root.destroy()
            return
            
        # Собираем файлы
        extensions = ('.wav', '.mp3', '.flac', '.ogg', '.m4a')
        self.files = [f for f in os.listdir(self.folder_path) if f.lower().endswith(extensions)]
        self.files.sort()
        self.index = 0
        self.current_file_path = None

        # --- Интерфейс ---
        self.lbl_filename = tk.Label(root, text="Готов к работе", font=("Helvetica", 12, "bold"), wraplength=500)
        self.lbl_filename.pack(pady=25)

        self.lbl_count = tk.Label(root, text=f"Файлов: {len(self.files)}", font=("Arial", 10), fg="gray")
        self.lbl_count.pack(pady=5)

        # Инструкция
        help_text = (
            "↑ (ВВЕРХ)  - УДАЛИТЬ и след.\n"
            "↓ (ВНИЗ)   - ПОВТОРИТЬ\n"
            "→ (ВПРАВО) - ОСТАВИТЬ и след.\n"
            "← (ВЛЕВО)  - ВЕРНУТЬСЯ назад"
        )
        self.lbl_help = tk.Label(root, text=help_text, font=("Consolas", 11), bg="#f0f0f0", relief="sunken", padx=10, pady=10)
        self.lbl_help.pack(side=tk.BOTTOM, pady=20)

        # --- Биндинг клавиш ---
        root.bind_all('<Up>', self.delete_and_next)
        root.bind_all('<Down>', self.replay)
        root.bind_all('<Right>', self.keep_and_next)
        root.bind_all('<Left>', self.prev_file) # Добавили клавишу влево

        # Запуск первого файла
        if self.files:
            self.load_and_play()
        else:
            self.lbl_filename.config(text="В папке нет аудиофайлов!")

    def load_and_play(self):
        if 0 <= self.index < len(self.files):
            filename = self.files[self.index]
            self.current_file_path = os.path.join(self.folder_path, filename)
            
            self.lbl_filename.config(text=filename, fg="black")
            self.lbl_count.config(text=f"Файл {self.index + 1} из {len(self.files)}")
            
            try:
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
                pygame.mixer.music.load(self.current_file_path)
                pygame.mixer.music.play()
            except Exception as e:
                print(f"Ошибка воспроизведения {filename}: {e}")
                self.lbl_filename.config(text=f"Ошибка: {filename}", fg="red")
        elif len(self.files) == 0:
             self.finish()
        else:
            # Если индекс вышел за пределы (например, в конце списка), просто стоим
            self.finish()

    def replay(self, event=None):
        """Стрелка ВНИЗ: Повторить"""
        if self.current_file_path:
            try:
                pygame.mixer.music.play()
                # Визуальный миг
                self.lbl_filename.config(bg="#e0e0ff")
                self.root.after(200, lambda: self.lbl_filename.config(bg="SystemButtonFace"))
            except Exception:
                pass

    def keep_and_next(self, event=None):
        """Стрелка ВПРАВО: Следующий"""
        if self.index < len(self.files) - 1:
            self.index += 1
            self.load_and_play()
        else:
            # Если это последний файл
            messagebox.showinfo("Инфо", "Это последний файл в списке.")

    def prev_file(self, event=None):
        """Стрелка ВЛЕВО: Предыдущий"""
        if self.index > 0:
            self.index -= 1
            self.load_and_play()
        else:
            # Визуальный сигнал, что дальше нельзя
            self.lbl_filename.config(bg="#ffcccc")
            self.root.after(200, lambda: self.lbl_filename.config(bg="SystemButtonFace"))

    def delete_and_next(self, event=None):
        """Стрелка ВВЕРХ: Удалить и следующий"""
        if self.current_file_path and os.path.exists(self.current_file_path):
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
                
                os.remove(self.current_file_path)
                print(f"Удален: {self.files[self.index]}")
                
                # Удаляем из списка
                del self.files[self.index]
                
                # Индекс не меняем (следующий файл сам "приехал" на это место)
                # Но если мы удалили ПОСЛЕДНИЙ файл в списке, надо откатиться назад
                if self.index >= len(self.files) and self.index > 0:
                    self.index -= 1
                
                self.load_and_play()
                        
            except PermissionError:
                messagebox.showerror("Ошибка", "Файл занят. Попробуйте нажать стоп или перезапустить.")
            except Exception as e:
                print(f"Ошибка удаления: {e}")

    def finish(self):
        pygame.mixer.music.stop()
        self.lbl_filename.config(text="ВСЕ ФАЙЛЫ ОБРАБОТАНЫ!", fg="green")
        self.lbl_count.config(text="")
        self.current_file_path = None
        self.root.unbind_all('<Up>')
        self.root.unbind_all('<Down>')
        self.root.unbind_all('<Right>')
        self.root.unbind_all('<Left>')

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioCleaner(root)
    root.mainloop()