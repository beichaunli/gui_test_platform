import ast
import hashlib
import json
import os
from pathlib import Path
import zipfile

from common import ROOT, PROJECT, DEMO, SOFTWARE_ROOT, ready

for directory, children, files in os.walk(ROOT):
    children[:] = [name for name in children if name not in ('.gui_deps', 'data', '__pycache__')]
    for name in files:
        if name.endswith('.py'):
            path = Path(directory) / name
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
for name in ('index.html', 'style.css', 'app.js'):
    assert (ROOT / 'static' / name).stat().st_size > 0
manifest = json.loads((SOFTWARE_ROOT / 'manifest.json').read_text(encoding='utf-8'))
for entry in manifest['files']:
    path = SOFTWARE_ROOT / entry['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], path
assert DEMO.exists()
with zipfile.ZipFile(SOFTWARE_ROOT / 'notepad-plus-plus-v8.8.7-source.zip') as archive:
    assert any(name.endswith('PowerEditor/src/Notepad_plus.cpp') for name in archive.namelist())
print('Source syntax, static files, bundled software and source hashes: OK')
print('Matching GUI dependencies:', ready())
