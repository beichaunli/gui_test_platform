"""Exercise the actual running service, including its worker environment."""
import argparse
import json
import time
import urllib.request
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8877)
parser.add_argument('--actions', type=int, default=35)
parser.add_argument('--interaction', choices=['background','foreground'], default='background')
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
session = json.load(urllib.request.urlopen(base+'/api/session'))
raw = {'executable':session['demo'],'actions':args.actions,'rounds':1,'seconds':400,'timeout':12,'interval':.3,'depth':4,'interaction':args.interaction}
request = urllib.request.Request(base+'/api/tasks',data=json.dumps(raw).encode(),headers={'Content-Type':'application/json','X-Test-Token':session['token']})
task = json.load(urllib.request.urlopen(request))['id']
deadline = time.monotonic()+440
while time.monotonic() < deadline:
    item = json.load(urllib.request.urlopen(base+'/api/tasks/'+task))
    if item['status'] not in ('queued','running','stopping'):
        break
    time.sleep(.4)
runtime=item['runtime']
print('TASK',task,item['status'],'actions',runtime.get('actions'),'states',len(runtime.get('graph',{}).get('states',[])),
      'events',item['event_count'],'deduplicated',runtime.get('graph',{}).get('deduplicated'))
print('INTERACTION',item['config'].get('interaction'))
for key,feature in runtime.get('features',{}).items():
    print(key,feature['status'],feature['events'])
events=json.load(urllib.request.urlopen(base+'/api/tasks/'+task+'/events'))
for event in events:
    if event['status'] not in ('executed','skipped'):
        print('OBSERVATION',event['seq'],event['label'],event['status'],event.get('reason'),event.get('check'))
assert item['status']=='completed',runtime.get('message')
assert item['config'].get('interaction')==args.interaction, '服务仍为旧版，请重启服务'
assert runtime.get('issues')==0, runtime.get('message')
assert sum(f['status']=='检查通过' for f in runtime['features'].values())>=12
print('Rich GUI traversal through actual HTTP worker: VERIFIED')
