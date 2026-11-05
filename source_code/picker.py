"""Native file picker, no guessed executable paths or browser upload copies."""
import tkinter as tk
from tkinter import filedialog
import sys

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
path = filedialog.askopenfilename(title='选择被测 Windows 图形程序', filetypes=[('Windows EXE', '*.exe')])
sys.stdout.buffer.write(path.encode('utf-8'))
root.destroy()
