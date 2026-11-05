from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher import choose_port, main


class LauncherTests(unittest.TestCase):
    def test_existing_platform_is_reused_without_bind(self):
        with patch('launcher.running_platform', return_value={'build':'background-controls-v1'}), patch('launcher.socket.socket') as sock:
            self.assertEqual(choose_port(8877), (8877, True))
            sock.assert_not_called()

    def test_foreign_occupied_port_falls_back(self):
        candidate = MagicMock()
        candidate.__enter__.return_value = candidate
        candidate.bind.side_effect = [OSError('occupied'), None]
        with patch('launcher.running_platform', return_value=None), patch('launcher.socket.socket', return_value=candidate):
            self.assertEqual(choose_port(8877), (8878, False))

    def test_no_browser_reuse_exits_normally(self):
        with patch('launcher.choose_port', return_value=(8877, True)), patch('launcher.webbrowser.open') as browser:
            main(['--no-browser'])
            browser.assert_not_called()
