from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from driver import WindowsDriver
from common import BackgroundUnsupported, validate, DEMO
from hotkey import StopShortcut
from server import LocalHTTPServer
from http.server import BaseHTTPRequestHandler


class NoPhysicalControl:
    handle = 123
    def set_focus(self): raise AssertionError('Background operation stole focus')
    def click_input(self): raise AssertionError('Background operation moved mouse')
    def type_keys(self, *args, **kwargs): raise AssertionError('Background operation sent global keys')
    def click(self): self.clicked = True
    def send_message_timeout(self, *args, **kwargs): self.messages.append(args)
    def class_name(self): return 'Scintilla'
    def is_visible(self): return True


class BackgroundTests(unittest.TestCase):
    def driver(self):
        driver = WindowsDriver.__new__(WindowsDriver)
        driver.config = {'profile':'notepad','backend':'win32','interaction':'background'}
        return driver

    def test_default_is_background_and_invalid_mode_rejected(self):
        self.assertEqual(validate({'executable':str(DEMO)})['interaction'], 'background')
        with self.assertRaises(ValueError):
            validate({'executable':str(DEMO),'interaction':'invalid'})

    def test_native_button_does_not_use_cursor(self):
        control = NoPhysicalControl()
        self.driver().press_button(control)
        self.assertTrue(control.clicked)

    def test_background_shortcuts_and_menu_do_not_focus_or_use_global_keyboard(self):
        posted = []
        control = NoPhysicalControl()
        control.class_name = lambda:'Notepad++'
        def resolve_menu(*args, **kwargs):
            self.assertFalse(kwargs.get('exact', False), 'Labels are accelerator-stripped')
            return SimpleNamespace(is_enabled=lambda:True,item_id=lambda:40001)
        control.menu_item = resolve_menu
        driver=self.driver()
        driver.current=lambda:control
        with patch.dict(sys.modules, {'win32gui':SimpleNamespace(PostMessage=lambda *args:posted.append(args))}):
            driver.act({'kind':'keys','keys':'^n'})
            driver.act({'kind':'menu','path':['File','New']})
            with self.assertRaises(BackgroundUnsupported):
                driver.act({'kind':'keys','keys':'^x'})
        self.assertEqual(posted, [(123,0x111,41001,0),(123,0x111,40001,0)])

    def test_editor_posts_unicode_to_control_without_global_input(self):
        control=NoPhysicalControl()
        control.messages=[]
        self.driver().editor_text(control, '中\nA')
        self.assertEqual(control.messages[0], (0x102,ord('中'),0))
        self.assertIn((0x100,13,0), control.messages)
        self.assertEqual(control.messages[-1], (0x102,ord('A'),0))

    def test_stop_shortcut_uses_existing_stop_path(self):
        called=[]
        controller=SimpleNamespace(active='a'*12,stop=lambda task:called.append(task))
        StopShortcut(controller).trigger()
        self.assertEqual(called,['a'*12])

    def test_second_server_cannot_share_a_live_port(self):
        first=LocalHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):
                LocalHTTPServer(first.server_address,BaseHTTPRequestHandler)
        finally:
            first.server_close()
