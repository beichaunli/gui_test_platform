"""Real integration run against the bundled, unchanged Notepad++ executable."""
import argparse
import sys
import time

from common import DEMO
from server import Controller
from storage import Store
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--probe', action='store_true')
parser.add_argument('--stop', action='store_true')
parser.add_argument('--actions', type=int, default=24)
parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data' / 'verification')
parser.add_argument('--executable', type=Path, default=DEMO)
parser.add_argument('--argument', action='append', default=[])
args = parser.parse_args()
store = Store(args.data_dir)
controller = Controller(store)
task = controller.start({'executable': str(args.executable), 'args': args.argument, 'actions': args.actions, 'seconds': 300, 'timeout': 10, 'interval': .25}, probe=args.probe)
deadline = time.monotonic()+340
requested = False
while controller.active and time.monotonic() < deadline:
    item = store.task(task)
    if args.stop and item['runtime'].get('actions', 0) >= 2 and not requested:
        controller.stop(task)
        requested = True
    time.sleep(.25)
if controller.active:
    controller.stop(task)
    raise RuntimeError('Integration deadline exceeded')
item = store.task(task)
print('TASK', task, item['status'], item['runtime'].get('message'))
print('ACTIONS', item['runtime'].get('actions'), 'STATES', len(item['runtime'].get('graph', {}).get('states', [])), 'EVENTS', item['event_count'])
print('FEATURES', item['runtime'].get('features'))
for event in store.events(task, 0, 1000):
    if event['status'] not in ('executed', 'skipped'):
        print(event['seq'], event.get('category'), event.get('label'), event['status'], event.get('reason'), event.get('check'))
assert item['status'] == ('stopped' if args.stop else 'completed'), item
assert (store.directory(task) / 'report.html').exists()
assert (store.directory(task) / 'evidence.zip').exists()
assert any(event.get('after_image') for event in store.events(task, 0, 1000))
if not args.probe and not args.stop:
    assert item['runtime']['actions'] >= min(5, args.actions), item
    if item['config']['profile'] == 'notepad':
        assert len(item['runtime']['graph']['states']) >= 3, item
        assert len(item['runtime']['graph']['edges']) >= 3, item
        assert sum(f['status'] == '检查通过' for f in item['runtime']['features'].values()) >= 5, item
print('VERIFIED', store.directory(task))
