"""A registered stop shortcut, without mouse hooks or injected input."""
import ctypes
from ctypes import wintypes
import sys
import threading
import time


class StopShortcut:
    def __init__(self, controller):
        self.controller = controller
        self.quit = threading.Event()
        self.started = threading.Event()
        self.available = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        self.started.wait(1)
        return self

    def trigger(self):
        task = self.controller.active
        if task:
            try:
                self.controller.stop(task)
            except ValueError:
                pass

    def run(self):
        if sys.platform != 'win32':
            self.started.set()
            return
        user = ctypes.windll.user32
        key_id = 0x4754
        self.available = bool(user.RegisterHotKey(None, key_id, 0x4003, 0x7B))
        self.started.set()
        if not self.available:
            return
        message = wintypes.MSG()
        try:
            while not self.quit.wait(.05):
                while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x0312 and message.wParam == key_id:
                        self.trigger()
        finally:
            user.UnregisterHotKey(None, key_id)
            self.available = False

    def close(self):
        self.quit.set()
        self.thread.join(1)
