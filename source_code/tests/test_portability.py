"""Project-relative selection and runtime resolution must ignore the shell cwd."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import PROJECT, DEMO, validate, portable_path, resolve_program


class PortabilityTests(unittest.TestCase):
    def test_bundled_selection_is_stored_relative(self):
        config = validate({'executable': str(DEMO)})
        self.assertEqual(config['executable'], 'source_code/tested_software/notepad++/notepad++.exe')
        self.assertFalse(Path(config['executable']).is_absolute())
        self.assertEqual(resolve_program(config['executable']), DEMO.resolve())

    def test_resolve_from_unrelated_working_directory(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as unrelated:
            try:
                os.chdir(unrelated)
                config = validate({'executable': 'source_code/tested_software/notepad++/notepad++.exe'})
                self.assertEqual(config['profile'], 'notepad')
                self.assertEqual(resolve_program(config['executable']), DEMO.resolve())
                self.assertFalse(Path(config['executable']).is_absolute())
            finally:
                os.chdir(original)

    def test_project_artifact_paths_are_relative(self):
        value = portable_path(PROJECT/'source_code'/'data'/'task'/'sample.txt')
        self.assertEqual(value, 'source_code/data/task/sample.txt')
        self.assertEqual(portable_path('source_code/test_assets/input.txt'), 'source_code/test_assets/input.txt')
