"""An isolated GUI worker: bounded operations, faithful events, replay-based BFS."""
import argparse
import json
from pathlib import Path
import queue
import threading
import time
import traceback
import re

from common import atomic_json, dependencies, DesktopUnavailable, DESKTOP_HELP, is_desktop_error, BackgroundUnsupported
from driver import WindowsDriver
from exploration import Frontier, state_id
from reports import build
from storage import Store

from notepad_profile import FEATURES


class Stopped(Exception):
    pass


class Runner:
    def __init__(self, store, task, driver_class=WindowsDriver):
        self.store, self.task = store, task
        self.item = store.task(task)
        self.config = self.item['config']
        self.directory = store.directory(task)
        self.driver = driver_class(self.config, self.directory)
        self.frontier = Frontier(self.config['depth'])
        self.round = 0
        self.actions = 0
        self.attempts = 0
        self.recoveries = 0
        self.issues = 0
        self.frames = 0
        self.started = time.monotonic()
        self.last_preview = 0
        self.executing_category = 'test'
        self.skip_seen = set()
        self.features = {key: {'name': name, 'status': '未到达', 'events': []} for key, name in FEATURES.items()} if self.config['profile'] == 'notepad' else {}

    def substep(self, label, text, control, after=True):
        data = {'input': text, 'control': {'class': control.class_name(), 'id': control.control_id()}, 'category': 'recovery' if self.executing_category == 'recovery' else 'test_detail',
                'label': label, 'status': 'executed', 'round': self.round, 'reason': '复合文件操作的实际子步骤', 'check': '未验证功能正确性'}
        if after:
            shot = self.driver.snapshot()
            evidence = self.evidence(shot)
            data.update(after_image=evidence['image'], after_tree=evidence['tree'], window=shot['title'])
        self.store.event(self.task, **data)

    def stopping(self):
        return (self.directory / 'stop').exists()

    def checkpoint(self):
        if self.stopping():
            raise Stopped('用户主动停止；保留已执行步骤，未完成范围不计为通过')
        if time.monotonic()-self.started > self.config['seconds']:
            raise Stopped('达到总时长预算，保留部分执行结果')
        if sum(p.stat().st_size for p in (self.directory / 'evidence').glob('*') if p.is_file()) > 400*1024*1024:
            raise Stopped('证据达到 400 MiB 存储预算；停止而不静默丢弃正常记录')

    def bounded(self, function):
        result = queue.Queue()
        def execute():
            try:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    result.put((True, function()))
                finally:
                    pythoncom.CoUninitialize()
            except BaseException as exc:
                result.put((False, exc))
        threading.Thread(target=execute, daemon=True).start()
        deadline = time.monotonic()+self.config['timeout']
        while True:
            self.checkpoint()
            try:
                ok, value = result.get(timeout=.1)
                if ok:
                    return value
                raise value
            except queue.Empty:
                if time.monotonic() > deadline:
                    # Do not continue using a driver after an unknown in-flight operation.
                    raise TimeoutError('单步超过时间预算；终止本轮，避免继续执行未知状态的操作')

    def evidence(self, snapshot, label='', highlight=None):
        self.frames += 1
        base = f'evidence/{self.frames:05d}'
        (self.directory / (base+'.json')).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            self.driver.capture(self.directory / (base+'.png'), highlight)
            self.store.update(self.task, latest_image=base+'.png', latest_time=time.time())
            return {'image': base+'.png', 'tree': base+'.json', 'error': ''}
        except Exception as exc:
            return {'image': None, 'tree': base+'.json', 'error': str(exc)}

    def progress(self, **extra):
        self.store.update(self.task, round=self.round, actions=self.actions, attempts=self.attempts, recoveries=self.recoveries,
                          issues=self.issues, elapsed=round(time.monotonic()-self.started, 2),
                          graph=self.frontier.graph(), features=self.features, **extra)

    def log(self, label, category='system', status='executed', reason='', **data):
        return self.store.event(self.task, label=label, category=category, status=status,
                                reason=reason, round=self.round, check='未验证功能正确性', **data)

    def prepare(self, label='启动软件'):
        started = time.monotonic()
        self.bounded(self.driver.launch)
        shot, stable = self.wait_stable()
        evidence = self.evidence(shot)
        self.log(label, category='prepare' if self.actions == 0 else 'recovery', reason='只控制本任务新建进程；'+('初始界面已稳定' if stable else '初始界面未完全稳定'),
                 after_state=state_id(shot), after_image=evidence['image'], after_tree=evidence['tree'],
                 window=shot['title'], controls=len(shot['controls']), duration=time.monotonic()-started,
                 capture_error=evidence['error'])
        return shot

    def wait_stable(self):
        deadline = time.monotonic()+min(3, self.config['timeout'])
        last, stable, snapshot = None, 0, None
        while time.monotonic() < deadline:
            self.checkpoint()
            snapshot = self.bounded(self.driver.snapshot)
            sid = state_id(snapshot)
            stable = stable+1 if sid == last else 0
            last = sid
            if time.monotonic()-self.last_preview > 1:
                try:
                    self.driver.capture(self.directory / 'preview.png')
                    self.store.update(self.task, preview_time=time.time())
                except Exception:
                    pass
                self.last_preview = time.monotonic()
            if stable >= 1:
                return snapshot, True
            time.sleep(.2)
        return snapshot, False

    def check_feature(self, action, before, after):
        feature = action.get('feature')
        if not feature:
            return '未验证功能正确性', None
        if self.config['profile'] == 'notepad':
            from notepad_profile import check
            return check(action, before, after, self.directory)
        if action['kind'] == 'file_dialog':
            if action['save']:
                path = self.directory / 'output.txt'
                passed = path.exists() and path.read_text(encoding='utf-8-sig') == (self.directory / 'sample.txt').read_text(encoding='utf-8-sig')
                return '测试输出文件内容与原始样本一致' if passed else '未观察到预期输出文件及内容', passed
            passed = after.get('class') == 'Notepad++' and 'sample.txt' in after['title'] and after.get('editor_length', 0) > 0
            return '专用样本载入编辑器' if passed else '未确认样本成功载入', passed
        if feature == 'new':
            passed = after.get('tabs', 0) > before.get('tabs', 0)
            return '文档标签数量增加' if passed else '未观察到文档标签增加', passed
        if feature == 'edit':
            passed = after.get('editor_length', -1) > before.get('editor_length', -1)
            return '编辑器文本长度增加' if passed else '未观察到文本长度增加', passed
        if feature in ('open', 'save'):
            return '已到达文件对话框；等待后续提交验证', None
        title = after['title'].lower()
        passed = any(word in title for word in (('find', '查找') if feature == 'find' else ('go to', '转到', '跳转')))
        return '目标功能对话框出现（不代表全部业务正确）' if passed else '未观察到预期功能窗口', passed

    def perform(self, action, category='test'):
        self.checkpoint()
        start = time.monotonic()
        before = self.bounded(self.driver.snapshot)
        previous = self.evidence(before, highlight=action.get('rect'))
        status, reason, after = 'executed', '操作已执行', before
        check, passed = '未验证功能正确性', None
        error = None
        try:
            self.executing_category = category
            self.driver.on_step = self.substep
            self.bounded(lambda: self.driver.act(action))
            end = time.monotonic()+self.config['interval']
            while time.monotonic() < end:
                self.checkpoint()
                time.sleep(.05)
            after, stable = self.wait_stable()
            check, passed = self.check_feature(action, before, after)
            if not stable:
                reason = '操作已执行，但界面未在稳定等待期内稳定'
            if passed is False:
                status, reason = 'check_failed', check
            elif after.get('class') == '#32770':
                title = after.get('title', '')
                text = '\n'.join(c.get('name', '') for c in after.get('controls', []))
                if re.search(r'^(error|warning|错误|警告)', title, re.I) or re.search(r'无法打开|找不到文件|操作失败|access denied|cannot open|invalid input', text, re.I):
                    status, reason = 'notice', '软件提示：'+(text or title)[:800]+'；提示本身不证明软件缺陷'
        except Stopped as exc:
            status, reason, error = 'cancelled', str(exc), exc
        except TimeoutError as exc:
            status, reason, error = 'timeout', str(exc), exc
        except BackgroundUnsupported as exc:
            status, reason = 'skipped', str(exc)
        except Exception as exc:
            status, reason = 'operation_failed', str(exc)
            if is_desktop_error(exc):
                status, reason, error = 'environment_error', DESKTOP_HELP, DesktopUnavailable(DESKTOP_HELP)
                self.store.update(self.task, stop_reason='desktop_unavailable', environment_error=DESKTOP_HELP)
            try:
                after = self.bounded(self.driver.snapshot)
            except Exception:
                after = None
        following = self.evidence(after) if after else {'image': None, 'tree': None, 'error': '操作后窗口不可用'}
        item = self.store.event(self.task, round=self.round, category=category, label=action['label'], action=action,
                                interaction=self.config.get('interaction', 'foreground'),
                                before_state=state_id(before), after_state=state_id(after) if after else None,
                                window=before['title'], pid=before['pid'], status=status, reason=reason,
                                duration=round(time.monotonic()-start, 3), check=check,
                                before_image=previous['image'], after_image=following['image'],
                                before_tree=previous['tree'], after_tree=following['tree'],
                                capture_error='; '.join(filter(None, [previous['error'], following['error']])))
        if category == 'test' and status not in ('environment_error', 'skipped'):
            self.actions += 1
        elif category != 'test' and status != 'environment_error':
            self.recoveries += 1
        if status in ('timeout', 'check_failed', 'operation_failed', 'notice'):
            self.issues += 1
        feature = action.get('feature')
        if feature and status != 'environment_error' and category == 'test':
            entry = self.features[feature]
            entry['events'].append(item['seq'])
            # A later branch reaching an entry must not erase an earlier passed check.
            state = '检查通过' if passed is True else '检查失败' if passed is False else '已到达'
            if status in ('operation_failed', 'timeout'):
                state = '操作失败'
            elif status == 'skipped':
                state = '已排除（后台不支持）'
            if entry['status'] not in ('检查通过', '检查失败') or passed is not None:
                entry['status'] = state
        self.progress()
        if error:
            raise error
        return after, item

    def discover(self, snapshot, path):
        actions, skipped = self.bounded(lambda: self.driver.candidates(snapshot))
        fresh = state_id(snapshot) not in self.frontier.states
        sid = self.frontier.observe(snapshot, path, actions)
        if fresh:
            if len(actions) > 80:
                self.log('候选动作达到单状态上限', status='skipped', reason=f'本状态有 {len(actions)} 个候选，仅安排前 80 个；其余不计为已测试', before_state=sid)
            for excluded in skipped[:80]:
                key = (excluded['label'], excluded['reason'])
                if key not in self.skip_seen:
                    self.skip_seen.add(key)
                    self.log(excluded['label'], category='test', status='skipped', reason=excluded['reason'], action=excluded, before_state=sid)
        return sid

    def run(self):
        self.store.update(self.task, 'running', message='正在启动并检查可操作界面', started=time.time())
        (self.directory / 'settings').mkdir(exist_ok=True)
        (self.directory / 'sample.txt').write_text('GUI traversal test\nSecond line: 123\n', encoding='utf-8')
        (self.directory / 'unicode.txt').write_text('中文样本\nUTF-8 text: 世界\n123\n', encoding='utf-8')
        final, message = 'completed', '预算内测试完成；未执行和无法验证项见范围报告'
        try:
            dependencies()
            for round_number in range(1, self.config['rounds']+1):
                self.round = round_number
                self.frontier.queue.clear()
                self.frontier.seen.clear()
                self.frontier.expanded.clear()
                self.frontier.coverage_seen.clear()
                self.skip_seen.clear()
                root = self.prepare()
                actions, skipped = self.bounded(lambda: self.driver.candidates(root))
                if not actions:
                    raise ValueError('窗口已启动，但没有识别到可安全操作的控件或菜单；请尝试 UIA 后端或配置排除范围')
                self.log('接入检查完成', category='prepare', reason=f'识别到 {len(root["controls"])} 个可见控件、{len(actions)} 个候选动作',
                         after_state=state_id(root), controls=len(root['controls']))
                if self.config.get('probe'):
                    self.store.update(self.task, controls=root['controls'], candidates=actions[:30])
                    message = '接入检查通过；已关闭检查用实例，可以开始正式遍历'
                    break
                self.discover(root, [])
                count = 0
                while count < self.config['actions']:
                    self.checkpoint()
                    item = self.frontier.pop()
                    if not item:
                        message = '已配置候选场景遍历完成，剩余预算不再重复填充；不代表软件全部功能均被覆盖'
                        break
                    self.attempts += 1
                    needs_restore = count and state_id(self.bounded(self.driver.snapshot)) != item['state']
                    if needs_restore:
                        self.bounded(self.driver.close)
                        root = self.prepare('重启到初始状态以探索其他分支')
                    valid = True
                    for previous in (item['path'] if needs_restore else []):
                        _, recovery = self.perform(previous, 'recovery')
                        if recovery['status'] != 'executed':
                            valid = False
                            break
                    current = self.bounded(self.driver.snapshot)
                    if not valid or state_id(current) != item['state']:
                        self.log(item['action']['label'], status='skipped', reason='恢复路径未到达预期状态；不在错误界面执行该控件',
                                 expected_state=item['state'], actual_state=state_id(current))
                        count += 1
                        continue
                    after, event = self.perform(item['action'])
                    count += 1
                    if after and event['status'] == 'executed':
                        following = self.discover(after, item['path']+[item['action']])
                        self.frontier.transition(item['state'], following, item['action'], event['seq'])
                    self.progress(message='广度优先遍历中：每次操作及恢复均已记录', stage='exploring')
                self.bounded(self.driver.close)
            if self.issues:
                message += f'；观察到 {self.issues} 条需复核记录，未自动认定为漏洞'
        except Stopped as exc:
            final, message = 'stopped', str(exc)
            self.log('停止测试', status='cancelled', reason=message)
        except DesktopUnavailable as exc:
            final, message = 'failed', str(exc)
            self.log('桌面环境不可交互，停止后续调度', status='environment_error', reason=message)
        except Exception as exc:
            final, message = 'failed', str(exc)
            self.log('执行中断', status='environment_error' if self.actions == 0 else 'operation_failed', reason=message,
                     diagnostic=traceback.format_exc()[-5000:])
        finally:
            try:
                self.driver.close()
                self.log('关闭本任务测试实例', category='system', reason='仅结束创建时间匹配的本任务进程；不计为软件崩溃')
            except Exception as exc:
                self.log('清理实例失败', status='environment_error', reason=str(exc))
            self.progress(message=message, finished=time.time(), stage='finished')
            self.store.update(self.task, final)
            build(self.store, self.task)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('data')
    parser.add_argument('task')
    arguments = parser.parse_args()
    Runner(Store(arguments.data), arguments.task).run()
