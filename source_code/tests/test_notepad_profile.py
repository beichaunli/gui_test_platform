"""Scenario generation/semantic checks, with no live desktop actions."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exploration import Frontier
from notepad_profile import candidates, check
from driver import WindowsDriver


MENUS = [{'label': 'File → Save As...', 'path': ['File', 'Save As...']},
         {'label': 'View → Word wrap', 'path': ['View', 'Word wrap']},
         {'label': 'View → Zoom → Zoom In', 'path': ['View', 'Zoom', 'Zoom In']},
         {'label': 'Edit → EOL → Unix (LF)', 'path': ['Edit', 'EOL', 'Unix (LF)']}]


def state(**extra):
    return {'title': 'sample.txt - Notepad++', 'main_title': 'sample.txt - Notepad++', 'class': 'Notepad++',
            'controls': [], 'editor_length': 38, 'editor_lines': 3, 'editor_modified': 0, **extra}


class NotepadTests(unittest.TestCase):
    def test_root_covers_varied_features_not_repeated_new_chain(self):
        config = {'text': 'test'}
        root = candidates(state(), config, Path('.'), MENUS)
        self.assertGreaterEqual(len({a['feature'] for a in root}), 12)
        blank = candidates(state(editor_length=0), config, Path('.'), MENUS)
        self.assertFalse(any(a['feature'] == 'new' for a in blank))
        self.assertTrue(any(a['feature'] == 'multiline' for a in blank))

    def test_semantic_dedup_keeps_distinct_scenarios_and_resets_each_round(self):
        frontier = Frontier(4)
        action = {'kind': 'keys', 'label': 'new', 'coverage_key': 'new:default'}
        frontier.observe(state(), [], [action])
        self.assertIsNotNone(frontier.pop())
        frontier.observe(state(title='new 1'), [action], [action, {**action, 'coverage_key': 'edit:blank'}])
        self.assertEqual(frontier.deduplicated, 1)
        self.assertEqual(frontier.pop()['action']['coverage_key'], 'edit:blank')
        self.assertIsNone(frontier.pop())
        frontier.expanded.clear()
        frontier.seen.clear()
        frontier.coverage_seen.clear()
        frontier.observe(state(), [], [action])
        self.assertIsNotNone(frontier.pop())

    def test_undo_redo_require_history(self):
        actions = candidates(state(), {'text': 'test'}, Path('.'), MENUS)
        self.assertFalse(any(a['feature'] in ('undo', 'redo') for a in actions))
        changed = candidates(state(editor_modified=1, editor_can_undo=1), {'text':'test'}, Path('.'), MENUS)
        self.assertTrue(any(a['feature'] == 'undo' for a in changed))
        self.assertFalse(any(a['feature'] == 'redo' for a in changed))

    def test_search_checks_real_selection_not_just_dialog(self):
        content = '中文 Second 123'
        start = len('中文 '.encode('utf-8'))
        after = {'editor_text':content,'editor_selection_start':start,'editor_selection_end':start+6}
        action = {'kind':'npp_dialog','feature':'find'}
        self.assertTrue(check(action, {}, after, Path('.'))[1])
        after['editor_selection_end'] = start
        self.assertFalse(check(action, {}, after, Path('.'))[1])
        self.assertIsNone(check({'kind':'keys','feature':'find'}, {}, after, Path('.'))[1])

    def test_save_uses_current_content_and_replace_checks_exact_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'output-edited.txt').write_text('Edited\n中文', encoding='utf-8')
            action={'kind':'file_dialog','save':True,'filename':'output-edited.txt','feature':'save'}
            self.assertTrue(check(action, {'editor_text':'Edited\r\n中文'}, {}, root)[1])
            self.assertFalse(check(action, {'editor_text':'Wrong'}, {}, root)[1])
        self.assertTrue(check({'kind':'npp_dialog','feature':'replace'}, {'editor_text':'123 123'}, {'editor_text':'456 456'}, Path('.'))[1])
        self.assertFalse(check({'kind':'npp_dialog','feature':'replace'}, {'editor_text':'123 123'}, {'editor_text':'456 123'}, Path('.'))[1])

    def test_find_uses_edit_inside_combobox_not_nonexistent_wrapper_method(self):
        class Control:
            def __init__(self, identity, classname, children=None):
                self.identity, self.classname = identity, classname
                self.children = children or []
                self.value = None
                self.clicked = False
            def control_id(self): return self.identity
            def class_name(self): return self.classname
            def is_visible(self): return True
            def descendants(self): return self.children
            def get_check_state(self): return 1
            def click_input(self): self.clicked = True
        class Edit(Control):
            def set_edit_text(self, value): self.value = value
        edit = Edit(1001, 'Edit')
        combo = Control(1601, 'ComboBox', [edit])
        button = Control(1, 'Button')
        normal = Control(1625, 'Button')
        class Window:
            def descendants(self): return [combo, edit, button, normal]
            def type_keys(self, keys, **kwargs): self.last_keys = keys
        window = Window()
        driver = WindowsDriver.__new__(WindowsDriver)
        driver.current = lambda: window
        driver.on_step = None
        driver.notepad_dialog({'mode':'find'})
        self.assertEqual(edit.value, 'Second')
        self.assertTrue(button.clicked)
        self.assertEqual(window.last_keys, '{ESC}')
