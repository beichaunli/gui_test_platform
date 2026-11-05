from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
target = root / '.gui_deps' / f'py{sys.version_info.major}{sys.version_info.minor}'
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', '--target', str(target), '-r', str(root / 'requirements-gui.txt')])
