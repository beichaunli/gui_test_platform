"""Pure BFS policy: paths are replayed from a fresh root, never guessed back clicks."""
from collections import deque
import hashlib
import json
import re


def state_id(snapshot):
    def text(value):
        value = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '<time>', value or '')
        return ' '.join(value.split())[:180]
    stable = {'title': text(snapshot.get('title')), 'class': snapshot.get('class'),
              'controls': [(c.get('type'), c.get('class'), '<dynamic-status>' if c.get('class') == 'msctls_statusbar32' else text(c.get('name')), c.get('id'), c.get('checked'))
                           for c in snapshot.get('controls', [])]}
    if snapshot.get('main_title'):
        stable['editor'] = {key: snapshot.get(key) for key in ('editor_length', 'editor_lines', 'editor_modified',
                            'editor_selection_start', 'editor_selection_end', 'editor_zoom', 'editor_wrap', 'editor_eol',
                            'editor_can_undo', 'editor_can_redo')}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


class Frontier:
    def __init__(self, depth):
        self.queue = deque()
        self.states = {}
        self.edges = []
        self.seen = set()
        self.expanded = set()
        self.coverage_seen = set()
        self.deduplicated = 0
        self.depth = depth

    def observe(self, snapshot, path, actions):
        sid = state_id(snapshot)
        fresh = sid not in self.states
        if fresh and len(self.states) < 120:
            self.states[sid] = {'id': sid, 'title': snapshot.get('title'), 'depth': len(path), 'controls': len(snapshot.get('controls', []))}
        if sid in self.states and sid not in self.expanded:
            self.expanded.add(sid)
            if len(path) < self.depth:
                for action in actions[:80]:
                    coverage = action.get('coverage_key')
                    if coverage and coverage in self.coverage_seen:
                        self.deduplicated += 1
                        continue
                    key = (sid, json.dumps(action, sort_keys=True, ensure_ascii=False))
                    if key not in self.seen:
                        self.seen.add(key)
                        if coverage:
                            self.coverage_seen.add(coverage)
                        self.queue.append({'state': sid, 'path': path, 'action': action})
        return sid

    def pop(self):
        return self.queue.popleft() if self.queue else None

    def transition(self, before, after, action, seq):
        self.edges.append({'from': before, 'to': after, 'action': action.get('label', action.get('kind')), 'event': seq})

    def graph(self):
        return {'states': list(self.states.values()), 'edges': self.edges, 'pending': len(self.queue), 'deduplicated': self.deduplicated}
