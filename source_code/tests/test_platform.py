"""Policy/storage/API tests. Fake windows here are labelled fixtures, never demo evidence."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import validate, DEMO
from exploration import Frontier, state_id
from reports import build
from server import handler
from storage import Store
from worker import Runner


def snapshot(title):
    return {'title': title, 'class': 'FixtureWindow', 'pid': 1, 'controls': [{'type': 'Button', 'class': 'Button', 'name': title, 'id': 1}]}


class FixtureDriver:
    def __init__(self, config, directory):
        self.directory = directory
        self.state = 'root'

    def launch(self):
        self.state = 'root'

    def snapshot(self):
        return snapshot(self.state)

    def candidates(self, current):
        paths = {'root': ['A', 'B'], 'A': ['C'], 'B': [], 'C': []}
        return [{'kind': 'fixture', 'label': name} for name in paths[self.state]], []

    def act(self, action):
        self.state = action['label']

    def capture(self, path, highlight=None):
        # Clearly synthetic test fixture; only created inside TemporaryDirectory.
        Path(path).write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=='))

    def close(self):
        pass


class Tests(unittest.TestCase):
    def test_frontier_is_breadth_first_and_deduplicates(self):
        frontier = Frontier(3)
        a, b, c = ({'kind': 'click', 'label': label} for label in ('A', 'B', 'C'))
        root = snapshot('root')
        frontier.observe(root, [], [a, b])
        self.assertEqual(frontier.pop()['action'], a)
        frontier.observe(snapshot('A'), [a], [c])
        self.assertEqual(frontier.pop()['action'], b)
        self.assertEqual(frontier.pop()['path'], [a])
        frontier.observe(root, [], [a, b])
        self.assertIsNone(frontier.pop())

    def test_state_ignores_geometry_but_keeps_function_text(self):
        one = snapshot('main')
        two = json.loads(json.dumps(one))
        two['controls'][0]['rect'] = [10, 20, 30, 40]
        self.assertEqual(state_id(one), state_id(two))
        two['controls'][0]['name'] = 'Different function'
        self.assertNotEqual(state_id(one), state_id(two))

    def test_depth_and_new_round_keep_graph(self):
        f = Frontier(1)
        a = {'kind': 'click', 'label': 'A'}
        f.observe(snapshot('root'), [], [a])
        f.pop()
        f.observe(snapshot('A'), [a], [a])
        self.assertIsNone(f.pop())
        f.expanded.clear()
        f.seen.clear()
        f.observe(snapshot('root'), [], [a])
        self.assertIsNotNone(f.pop())
        self.assertEqual(len(f.states), 2)

    def test_dynamic_statusbar_does_not_break_recovery_identity(self):
        a = snapshot('same window')
        a['controls'].append({'class': 'msctls_statusbar32', 'type': 'StatusBar', 'name': '0 / 5 items', 'id': 9})
        b = json.loads(json.dumps(a))
        b['controls'][-1]['name'] = '0 / 6 items'
        self.assertEqual(state_id(a), state_id(b))
        self.assertEqual(a['controls'][-1]['name'], '0 / 5 items')

    def test_validation_rejects_missing_exe_and_bad_budget(self):
        with self.assertRaises(ValueError):
            validate({'executable': 'missing.exe'})
        if DEMO.exists():
            with self.assertRaises(ValueError):
                validate({'executable': str(DEMO), 'actions': 0})
            self.assertEqual(validate({'executable': str(DEMO)})['profile'], 'notepad')

    def test_storage_reports_and_review_preserve_events(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({'rounds': 1})
            event = store.event(task, label='<script>alert(1)</script>', status='executed', reason='normal', category='test')
            store.note(task, event['seq'], 'reviewed', True)
            store.update(task, 'stopped', message='部分完成')
            report = build(store, task)
            self.assertEqual(report['events'][0]['status'], 'executed')
            self.assertTrue(report['events'][0]['review']['false_positive'])
            text = (store.directory(task) / 'report.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>alert(1)</script>', text)
            self.assertIn('&lt;script&gt;', text)
            self.assertTrue((store.directory(task) / 'evidence.zip').exists())
            for invalid in ('../outside', 'x'*12):
                with self.assertRaises(ValueError):
                    store.directory(invalid)

    def test_http_guards_and_event_pagination(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({})
            store.event(task, label='one', status='executed')
            class Idle:
                active = None
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, Idle(), 'private-token'))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertEqual(urllib.request.urlopen(base).status, 200)
                self.assertEqual(len(json.load(urllib.request.urlopen(base+f'/api/tasks/{task}/events'))), 1)
                self.assertEqual(json.load(urllib.request.urlopen(base+f'/api/tasks/{task}/events?after=1')), [])
                for path in ('/api/tasks', f'/files/{task}/../platform.sqlite'):
                    request = urllib.request.Request(base+path, data=b'{}') if path == '/api/tasks' else base+path
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request)
                    caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_runner_replays_paths_and_records_normal_steps(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({'profile': 'generic', 'depth': 3, 'timeout': 3, 'seconds': 30, 'rounds': 1, 'actions': 4, 'interval': .2})
            Runner(store, task, FixtureDriver).run()
            self.assertEqual(store.task(task)['status'], 'completed')
            events = store.events(task, 0, 1000)
            tests = [e['label'] for e in events if e['category'] == 'test' and e['status'] == 'executed']
            self.assertEqual(tests, ['A', 'B', 'C'])
            self.assertTrue(any(e['category'] == 'recovery' and e['label'] == 'A' for e in events))
            self.assertEqual(len(store.task(task)['runtime']['graph']['states']), 4)

    def test_stop_keeps_partial_report(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({'profile': 'generic', 'depth': 3, 'timeout': 3, 'seconds': 30, 'rounds': 1, 'actions': 20, 'interval': .2})
            (store.directory(task) / 'stop').touch()
            Runner(store, task, FixtureDriver).run()
            self.assertEqual(store.task(task)['status'], 'stopped')
            self.assertTrue((store.directory(task) / 'report.html').exists())
            self.assertTrue(any(e['status'] == 'cancelled' for e in store.events(task)))

    def test_fixture_failure_is_recorded_with_location_not_fake_source(self):
        class FailingFixture(FixtureDriver):
            def act(self, action):
                raise ValueError('labelled fixture failure')
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({'profile': 'generic', 'depth': 2, 'timeout': 3, 'seconds': 30, 'rounds': 1, 'actions': 1, 'interval': .2})
            Runner(store, task, FailingFixture).run()
            events = store.events(task, 0, 1000)
            failed = next(e for e in events if e['status'] == 'operation_failed')
            self.assertEqual(failed['action']['label'], 'A')
            self.assertEqual(failed['before_state'], state_id(snapshot('root')))
            self.assertIn('fixture failure', failed['reason'])
            self.assertTrue(failed['before_image'])
            self.assertNotIn('source_line', failed)

    def test_inactive_desktop_stops_first_failure_without_target_issues(self):
        class NoDesktop(FixtureDriver):
            def act(self, action):
                raise RuntimeError('There is no active desktop required for moving mouse cursor!')
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            task = store.create({'profile': 'generic', 'depth': 2, 'timeout': 3, 'seconds': 30, 'rounds': 1, 'actions': 30, 'interval': .2})
            Runner(store, task, NoDesktop).run()
            result = store.task(task)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['runtime']['stop_reason'], 'desktop_unavailable')
            self.assertEqual(result['runtime']['actions'], 0)
            self.assertEqual(result['runtime']['issues'], 0)
            actions = [e for e in store.events(task) if e.get('action')]
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]['status'], 'environment_error')
            self.assertIn('不是被测软件缺陷', result['runtime']['message'])


if __name__ == '__main__':
    unittest.main()
