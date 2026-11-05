"""Real owned Windows windows only. No global desktop coordinates or existing instances."""
import ctypes
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from common import dependencies, atomic_json, resolve_program, portable_path, PROJECT, BackgroundUnsupported


class WindowsDriver:
    def __init__(self, config, directory):
        dependencies()
        import psutil
        from pywinauto import Desktop
        self.psutil = psutil
        self.config = config
        self.directory = Path(directory)
        self.desktop = Desktop(backend=config['backend'])
        self.process = None
        self.owned = {}
        self.on_step = None
        self.launch_number = 0

    def launch(self):
        workspace = self.directory / 'workspace'
        workspace.mkdir(exist_ok=True)
        (workspace / 'sample.txt').write_text('GUI traversal test\nSecond line: 123\n', encoding='utf-8')
        args = [a.replace('{work}', str(workspace)).replace('{sample}', str(workspace / 'sample.txt')) for a in self.config['args']]
        args = [str((PROJECT / a).resolve()) if not a.startswith('-') and not Path(a).is_absolute()
                and (PROJECT / a).exists() else a for a in args]
        if self.config['profile'] == 'notepad':
            self.launch_number += 1
            settings = self.directory / 'settings' / f'instance-{self.launch_number:03d}'
            settings.mkdir(parents=True, exist_ok=True)
            args = ['-multiInst', '-nosession', '-noPlugin', '-settingsDir='+str(settings), str(self.directory / 'sample.txt')]
        startup = None
        if self.background:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 4  # SW_SHOWNOACTIVATE: request no initial foreground activation.
        self.process = subprocess.Popen([str(resolve_program(self.config['executable'])), *args], cwd=self.directory,
                                        startupinfo=startup,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.owned = {self.process.pid: self.psutil.Process(self.process.pid).create_time()}
        atomic_json(self.directory / 'owner.json', self.owned)
        return self.wait_window()

    def owned_pids(self):
        old = dict(self.owned)
        if self.process:
            try:
                parent = self.psutil.Process(self.process.pid)
                if parent.create_time() == self.owned.get(parent.pid):
                    for child in parent.children(recursive=True):
                        self.owned[child.pid] = child.create_time()
            except self.psutil.Error:
                pass
        if old != self.owned:
            atomic_json(self.directory / 'owner.json', self.owned)
        alive = []
        for pid, birth in list(self.owned.items()):
            try:
                if self.psutil.Process(pid).create_time() == birth:
                    alive.append(pid)
            except self.psutil.Error:
                pass
        return alive

    def windows(self):
        pids = self.owned_pids()
        found = []
        for pid in pids:
            for attempt in range(3):
                try:
                    candidates = self.desktop.windows(process=pid, visible_only=True)
                    for window in candidates:
                        try:
                            if window.is_visible() and window.rectangle().width() > 100:
                                found.append(window)
                        except Exception:
                            continue
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(.05)
        # Modal dialogs first, then the most recently foregrounded target window.
        found.sort(key=lambda w: (w.class_name() != '#32770', not w.is_enabled()))
        return found

    def wait_window(self):
        deadline = time.monotonic() + self.config['timeout']
        while time.monotonic() < deadline:
            windows = self.windows()
            if windows:
                return windows[0]
            if self.process.poll() is not None:
                code = self.process.returncode
                if code == 0:
                    raise RuntimeError('程序正常退出或转交现有实例，未接管用户原有窗口；请配置独立实例参数')
                raise RuntimeError(f'目标在出现窗口前退出，返回码 {code}')
            time.sleep(.15)
        raise TimeoutError('启动后没有找到属于新进程的可用窗口；检查权限和控件后端')

    def current(self):
        found = self.windows()
        if not found:
            code = self.process.poll() if self.process else None
            raise RuntimeError(f'目标窗口已退出，返回码 {code}；仅作退出观察，不自动确认漏洞')
        return found[0]

    @staticmethod
    def text(control):
        try:
            return control.window_text()[:500]
        except Exception:
            return ''

    def snapshot(self):
        window = self.current()
        responsive = ctypes.c_size_t()
        if not ctypes.windll.user32.SendMessageTimeoutW(window.handle, 0, 0, 0, 2, 700, ctypes.byref(responsive)):
            raise TimeoutError('目标窗口未响应探测消息')
        rect = window.rectangle()
        controls = []
        for control in window.descendants()[:350]:
            try:
                if not control.is_visible():
                    continue
                r = control.rectangle()
                info = control.element_info
                controls.append({'name': self.text(control), 'type': getattr(info, 'control_type', '') or control.friendly_class_name(),
                                 'class': control.class_name(), 'id': control.control_id(),
                                 'auto_id': getattr(info, 'automation_id', ''), 'enabled': control.is_enabled(),
                                 'rect': [r.left-rect.left, r.top-rect.top, r.width(), r.height()],
                                 'checked': control.get_check_state() if hasattr(control, 'get_check_state') else None})
            except Exception:
                continue
        sample = {'title': self.text(window), 'class': window.class_name(), 'rect': [rect.left, rect.top, rect.width(), rect.height()],
                  'controls': controls, 'pid': window.process_id(), 'hwnd': window.handle}
        if self.config['profile'] == 'notepad':
            main = next((w for w in self.windows() if w.class_name() == 'Notepad++'), None)
            if main:
                sample['main_title'] = self.text(main)
                sample['tabs'] = main.send_message(2031, 0, 0)
                editor = next((c for c in main.descendants() if c.class_name() == 'Scintilla' and c.is_visible()), None)
                if editor:
                    for name, message in {'length':2006, 'lines':2154, 'modified':2159, 'selection_start':2143,
                                          'selection_end':2145, 'zoom':2374, 'wrap':2269, 'eol':2030,
                                          'can_undo':2174, 'can_redo':2016, 'position':2008}.items():
                        sample['editor_'+name] = editor.send_message(message, 0, 0)
                    sample['editor_line'] = editor.send_message(2166, sample['editor_position'], 0)+1
                    content = editor.window_text()
                    sample['editor_text'] = content[:20000]
                    sample['editor_text_complete'] = len(content) <= 20000
        return sample

    def editor_length(self, window=None):
        window = window or self.current()
        for control in window.descendants():
            if control.class_name() == 'Scintilla' and control.is_visible():
                return control.send_message(2006, 0, 0)
        return -1

    def capture(self, path, highlight=None):
        import win32gui
        import win32ui
        from PIL import Image, ImageDraw
        window = self.current()
        hwnd = window.handle
        rect = win32gui.GetWindowRect(hwnd)
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        if not 0 < width <= 6000 or not 0 < height <= 4000:
            raise RuntimeError('窗口尺寸不支持截图')
        dc = win32gui.GetWindowDC(hwnd)
        source = win32ui.CreateDCFromHandle(dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        try:
            ok = ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2)
            if not ok:
                ok = ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 0)
            if not ok:
                raise RuntimeError('目标拒绝截图')
            img = Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
            if max(hi for lo, hi in img.getextrema()) == 0:
                raise RuntimeError('截图为空，不作为测试证据展示')
            if highlight:
                x, y, w, h = highlight
                ImageDraw.Draw(img).rectangle((x, y, x+w, y+h), outline='#f6a623', width=4)
            path = Path(path)
            temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
            img.save(temp, format='PNG')
            for attempt in range(10):
                try:
                    temp.replace(path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.025)
            return [width, height]
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory.DeleteDC()
            source.DeleteDC()
            win32gui.ReleaseDC(hwnd, dc)

    def excluded(self, name):
        terms = [t.strip().lower() for t in re.split('[,，\n]', self.config['excluded']) if t.strip()]
        return any(term in name.lower() for term in terms)

    def menu_actions(self, window, limit=100):
        actions = []
        if not hasattr(window, 'menu'):
            return actions
        try:
            def walk(menu, path, depth=0):
                if depth > 3:
                    return
                for item in menu.items():
                    text = item.text().split('\t')[0].replace('&', '').strip()
                    if not text:
                        continue
                    label = ' → '.join(path + [text])
                    sub = item.sub_menu()
                    if sub:
                        walk(sub, path + [text], depth+1)
                    elif item.is_enabled():
                        actions.append({'kind': 'menu', 'path': path + [text], 'label': label, 'excluded': self.excluded(label)})
            walk(window.menu(), [])
        except Exception:
            pass
        return actions[:limit]

    def candidates(self, snapshot):
        result, skipped = [], []
        if self.config['profile'] == 'notepad':
            from notepad_profile import candidates
            menus = self.menu_actions(self.current(), 600) if snapshot['class'] == 'Notepad++' else []
            for action in candidates(snapshot, self.config, self.directory, menus):
                if self.excluded(action['label']):
                    skipped.append({'label': action['label'], 'reason': '命中功能场景排除规则'})
                else:
                    result.append(action)
            return result, skipped
        # File dialog completion is scoped to this task, never a user-chosen arbitrary file.
        names = ' '.join(c['name'] for c in snapshot['controls'])
        if snapshot['class'] == '#32770' and any(c['class'] in ('Edit', 'ComboBoxEx32', 'ComboBox') for c in snapshot['controls']):
            save = bool(re.search('Save|保存|另存', snapshot['title'], re.I))
            opening = bool(re.search('Open|打开', snapshot['title'], re.I))
            if save or opening:
                result.append({'kind': 'file_dialog', 'save': save, 'path': portable_path(self.directory / ('output.txt' if save else 'sample.txt')),
                               'label': '保存到测试目录' if save else '打开专用样本', 'feature': 'save' if save else 'open'})
        for i, control in enumerate(snapshot['controls']):
            if not control['enabled']:
                continue
            name, kind = control['name'], control['type'].lower()
            cls = control['class'].lower()
            selector = {k: control[k] for k in ('name', 'class', 'id', 'auto_id')}
            base = {'selector': selector, 'rect': control['rect']}
            if self.excluded(name):
                skipped.append({'label': name, 'reason': '命中排除规则', **base})
                continue
            # Unconfigured file dialogs never navigate arbitrary filesystem controls.
            if snapshot['class'] == '#32770' and re.search('Open|Save|打开|保存|另存', snapshot['title'], re.I):
                continue
            if cls == 'scintilla':
                continue
            if kind in ('button', 'checkbox', 'radiobutton', 'hyperlink') or cls == 'button':
                if name and kind != 'groupbox' and not (kind == 'radiobutton' and control.get('checked')):
                    result.append({'kind': 'click', 'label': name, **base})
            elif kind == 'edit' or cls == 'edit':
                if not re.search('密码|password', name, re.I):
                    for text in dict.fromkeys([self.config['text'], '', '123']):
                        if text != name:
                            result.append({'kind': 'text', 'text': text, 'label': '输入：' + (name or '文本框') + ('（空值）' if not text else ''), **base})
            elif kind in ('tab', 'tabcontrol') or cls == 'systabcontrol32':
                try:
                    ctl = self.locate(selector)
                    for index in range(min(ctl.tab_count(), 12)):
                        result.append({'kind': 'tab', 'index': index, 'label': f'切换标签 {index+1}', **base})
                except Exception:
                    pass
            elif kind == 'combobox' or cls == 'combobox':
                try:
                    ctl = self.locate(selector)
                    for index in range(min(ctl.item_count(), 5)):
                        result.append({'kind': 'select', 'index': index, 'label': f'选择选项 {index+1}', **base})
                except Exception:
                    pass
        if snapshot['class'] != '#32770':
            for action in self.menu_actions(self.current()):
                if action.get('excluded'):
                    skipped.append({'label': action['label'], 'reason': '命中菜单排除规则'})
                else:
                    result.append(action)
        if snapshot['class'] == '#32770':
            result.append({'kind': 'keys', 'keys': '{ESC}', 'label': '关闭当前子窗口'})
        return result, skipped

    def locate(self, selector):
        found = []
        for ctl in self.current().descendants():
            try:
                if ctl.is_visible() and ctl.is_enabled() and ctl.class_name() == selector['class'] and ctl.control_id() == selector['id']:
                    if not selector.get('name') or self.text(ctl) == selector['name'] or selector['class'].lower() == 'edit':
                        found.append(ctl)
            except Exception:
                pass
        if len(found) != 1:
            raise ValueError(f'控件不再唯一匹配（{len(found)}个）：{selector}')
        return found[0]

    @property
    def background(self):
        return getattr(self, 'config', {}).get('interaction', 'foreground') == 'background'

    def press_button(self, control):
        if not self.background:
            control.click_input()
        elif self.config['backend'] == 'win32':
            control.click()  # WM_* messages; does not move the physical cursor.
        elif hasattr(control, 'invoke'):
            control.invoke()
        else:
            raise BackgroundUnsupported('此控件没有后台 Invoke 接口，已跳过；未切换到真实鼠标点击')

    def keys(self, window, keys):
        if not self.background:
            window.type_keys(keys, pause=.05, set_foreground=True)
            return
        import win32gui
        if keys == '{ESC}' and window.class_name() != 'Notepad++':
            win32gui.PostMessage(window.handle, 0x0010, 0, 0)  # WM_CLOSE
            return
        commands = {'^n':41001, '^o':41002, '^a':42007, '^z':42003, '^y':42004,
                    '^f':43001, '^h':43003, '^g':43004}
        if self.config['profile'] == 'notepad' and window.class_name() == 'Notepad++' and keys in commands:
            win32gui.PostMessage(window.handle, 0x0111, commands[keys], 0)  # WM_COMMAND
            return
        raise BackgroundUnsupported('该快捷键没有后台命令适配，已跳过；如需真实键盘可选择前台输入模式')

    def editor_text(self, control, text):
        if not self.background:
            control.set_focus()
            self.literal(control, text)
            return
        for character in text.replace('\r\n', '\n').replace('\r', '\n'):
            if character == '\n':
                control.send_message_timeout(0x0100, 13, 0, timeout=.5)
                control.send_message_timeout(0x0101, 13, 0, timeout=.5)
            else:
                raw = character.encode('utf-16-le')
                for offset in range(0, len(raw), 2):
                    value = int.from_bytes(raw[offset:offset+2], 'little')
                    control.send_message_timeout(0x0102, value, 0, timeout=.5)  # WM_CHAR

    def act(self, action):
        window = self.current()
        if not self.background:
            window.set_focus()
        kind = action['kind']
        if kind == 'keys':
            self.keys(window, action['keys'])
        elif kind == 'menu':
            if self.background:
                if self.config['backend'] != 'win32' or not hasattr(window, 'menu_item'):
                    raise BackgroundUnsupported('当前菜单没有后台命令接口，已跳过；未抢占鼠标')
                import win32gui
                # Candidate labels strip & accelerators and tab-delimited shortcuts.
                # menu_item only resolves the item; unlike select(), it does not focus.
                item = window.menu_item('->'.join(action['path']), exact=False)
                if not item.is_enabled():
                    raise BackgroundUnsupported('菜单当前不可用，未发送命令')
                win32gui.PostMessage(window.handle, 0x0111, item.item_id(), 0)
            else:
                window.menu_select('->'.join(action['path']))
        elif kind == 'editor':
            controls = [c for c in window.descendants() if c.class_name() == 'Scintilla' and c.is_visible()]
            if not controls:
                raise ValueError('编辑控件不可用')
            self.editor_text(controls[0], action['text'])
        elif kind == 'file_dialog':
            filename = action.get('filename') or ('output.txt' if action['save'] else 'sample.txt')
            if Path(filename).name != filename:
                raise ValueError('样本文件名必须位于任务目录内')
            path = self.directory / filename
            fields = [c for c in window.descendants() if c.class_name() == 'Edit' and c.is_visible() and c.is_enabled()]
            if not fields:
                raise ValueError('文件对话框未暴露文件名输入框')
            # Prefer standard filename control ID 1148 over search boxes.
            field = next((c for c in fields if c.control_id() in (1148, 1152)), fields[-1])
            field.set_edit_text(str(path))
            if self.on_step:
                self.on_step('填写文件名', str(path), field)
            buttons = [c for c in window.descendants() if c.class_name() == 'Button' and c.control_id() == 1 and c.is_visible() and c.is_enabled()]
            if len(buttons) != 1:
                raise ValueError('文件提交按钮不唯一，未执行提交')
            self.press_button(buttons[0])
            if self.on_step:
                self.on_step('点击保存' if action['save'] else '点击打开', str(path), buttons[0], after=False)
            deadline = time.monotonic()+min(4, self.config['timeout'])
            import win32gui
            while time.monotonic() < deadline and win32gui.IsWindow(window.handle) and win32gui.IsWindowVisible(window.handle):
                time.sleep(.1)
            if action['save']:
                confirmation = self.current()
                if re.search(r'Confirm Save|确认另存|确认保存', self.text(confirmation), re.I):
                    yes = [c for c in confirmation.descendants() if c.class_name() == 'Button' and c.control_id() == 6 and c.is_visible()]
                    if len(yes) == 1:
                        self.press_button(yes[0])
                        if self.on_step:
                            self.on_step('确认覆盖本任务输出文件', str(path), yes[0], after=False)
        elif kind == 'npp_dialog':
            self.notepad_dialog(action)
        else:
            ctl = self.locate(action['selector'])
            if kind == 'click':
                self.press_button(ctl)
            elif kind == 'text':
                ctl.set_edit_text(action['text'])
            elif kind in ('select', 'tab'):
                ctl.select(action['index'])
            else:
                raise ValueError('不支持的动作')

    def notepad_dialog(self, action):
        window = self.current()
        mode = action['mode']
        def control(control_id, classname=None):
            matches = [c for c in window.descendants() if c.control_id() == control_id and c.is_visible()
                       and (not classname or c.class_name() == classname)]
            if len(matches) != 1:
                raise ValueError(f'功能控件 {control_id} 不唯一，停止该步骤')
            return matches[0]
        def fill(control_id, value, classname):
            target = control(control_id, classname)
            if classname == 'ComboBox':
                edits = [c for c in target.descendants() if c.class_name() == 'Edit' and c.is_visible()]
                if len(edits) != 1:
                    raise ValueError('查找/替换组合框没有唯一文本输入子控件')
                target = edits[0]
            target.set_edit_text(value)
            if self.on_step:
                self.on_step('输入'+('目标行号' if mode == 'goto' else '查找/替换文本'), value, target)
        if mode == 'goto':
            fill(2001, '2', 'Edit')
            button = control(1, 'Button')
        else:
            fill(1601, '123' if mode == 'replace' else 'Second', 'ComboBox')
            if mode == 'replace':
                fill(1602, '456', 'ComboBox')
            normal = control(1625, 'Button')
            if not normal.get_check_state():
                self.press_button(normal)
            # The task's known search text is present in the loaded sample.
            button = control(1609 if mode == 'replace' else 1, 'Button')
        self.press_button(button)
        if self.on_step:
            self.on_step({'find':'执行查找下一处','replace':'执行全部替换','goto':'执行行跳转'}[mode], mode, button, after=False)
        time.sleep(.2)
        if mode in ('find', 'replace'):
            self.keys(window, '{ESC}')

    @staticmethod
    def literal(control, text):
        escaped = ''.join({'{': '{{}', '}': '{}}', '+': '{+}', '^': '{^}', '%': '{%}', '~': '{~}', '(': '{(}', ')': '{)}', '[': '{[}', ']': '{]}'}.get(c, c) for c in text)
        control.type_keys(escaped, with_spaces=True, with_newlines=True, pause=.02)

    def close(self):
        # Force only fresh, birth-time-verified processes; no user documents to save.
        for pid, birth in list(self.owned.items())[::-1]:
            try:
                process = self.psutil.Process(pid)
                if process.create_time() == birth:
                    process.kill()
                    process.wait(timeout=3)
            except self.psutil.Error:
                pass
