"""Bounded semantic branches for Notepad++; the scheduler remains generic BFS."""
import re
from common import portable_path

FEATURES = {
    'new': '新建文档', 'edit': '追加文本', 'multiline': '多行与 Unicode 输入',
    'selection': '全文选择', 'undo': '撤销编辑', 'redo': '重做编辑',
    'open': '打开普通文本', 'open_utf8': '打开 UTF-8 文本', 'save': '另存为与内容核对',
    'find': '查找并验证选区', 'replace': '替换并核对内容', 'goto': '跳转指定行',
    'wrap': '自动换行切换', 'zoom': '编辑器缩放', 'eol': '行尾格式转换',
}


def context(snapshot):
    if snapshot.get('editor_length') == 0:
        return 'blank'
    if snapshot.get('editor_modified'):
        return 'multiline' if snapshot.get('editor_lines', 0) >= 5 else 'edited'
    return 'saved' if 'output-' in snapshot.get('main_title', '') else 'baseline'


def candidates(snapshot, config, directory, menus):
    """Actions have semantic keys: identical scenarios are queued once each round."""
    ctx = context(snapshot)
    actions = []
    def add(kind, label, feature, variant='default', **data):
        actions.append({'kind': kind, 'label': label, 'feature': feature,
                        'coverage_key': feature+':'+variant, **data})
    def menu(pattern):
        return next((a for a in menus if not a.get('excluded') and re.search(pattern, a['label'], re.I)), None)
    if snapshot['class'] == 'Notepad++':
        if ctx == 'baseline':
            add('keys', '新建空白文档', 'new', keys='^n')
            add('editor', '追加普通测试文本', 'edit', text=config['text'])
            add('editor', '追加多行中文与特殊字符', 'multiline', text='\n中文测试 alpha 123\nSymbols: [] {} + %\n第三行 end')
            add('keys', '打开文件窗口', 'open', keys='^o')
            add('keys', '打开查找窗口', 'find', keys='^f')
            add('keys', '打开替换窗口', 'replace', keys='^h')
            add('keys', '打开行跳转窗口', 'goto', keys='^g')
            add('keys', '选择全部内容', 'selection', keys='^a')
            save = menu(r'Save As|另存为')
            if save:
                add('menu', '另存为原始文档', 'save', 'entry-baseline', path=save['path'])
            for feature, pattern, label in [('wrap', r'Word wrap|自动换行', '切换自动换行'),
                                             ('zoom', r'Zoom In|放大', '放大编辑器字号'),
                                             ('eol', r'Unix \(LF\)|UNIX/OSX|Unix.*LF', '转换为 Unix LF 行尾')]:
                selected = menu(pattern)
                if selected:
                    add('menu', label, feature, path=selected['path'])
        elif ctx == 'blank':
            add('editor', '在新文档中输入多行文本', 'multiline', 'new-document', text='New document\n中文内容\n123')
        if snapshot.get('editor_can_undo'):
            add('keys', '撤销刚才的编辑', 'undo', 'modified-document', keys='^z')
        if snapshot.get('editor_can_redo'):
            add('keys', '重做刚才撤销的编辑', 'redo', keys='^y')
        if ctx in ('edited', 'multiline', 'blank'):
            save = menu(r'Save As|另存为')
            if save:
                add('menu', '另存为'+{'edited': '编辑后文档', 'multiline': '多行文档', 'blank': '空白文档'}[ctx],
                    'save', 'entry-'+ctx, path=save['path'])
        if snapshot.get('editor_modified') and snapshot.get('editor_length', 0):
            add('keys', '选择编辑后的全部内容', 'selection', 'edited', keys='^a')
        return actions

    title = snapshot.get('title', '').lower()
    if re.search(r'open|打开', title):
        for feature, filename, label in [('open', 'sample.txt', '载入普通文本样本'),
                                          ('open_utf8', 'unicode.txt', '载入 UTF-8 中文样本')]:
            add('file_dialog', label, feature, 'submit', save=False, filename=filename,
                path=portable_path(directory / filename))
    elif re.search(r'save|另存|保存', title):
        filename = 'output-'+ctx+'.txt'
        add('file_dialog', '保存'+ctx+'文档并核对完整内容', 'save', 'submit-'+ctx,
            save=True, filename=filename, path=portable_path(directory / filename))
    elif re.search(r'replace|替换', title):
        add('npp_dialog', '将 123 替换为 456 并核对结果', 'replace', 'execute', mode='replace')
    elif re.search(r'find|查找', title):
        add('npp_dialog', '查找 Second 并核对实际选区', 'find', 'execute', mode='find')
    elif re.search(r'go to|转到|跳转', title):
        add('npp_dialog', '跳转到第 2 行并核对光标', 'goto', 'execute', mode='goto')
    # Dialog cancellation is a recovery edge, not another test repeated in every branch.
    return actions


def check(action, before, after, directory):
    feature = action.get('feature')
    normalize = lambda text: text.replace('\r\n', '\n').replace('\r', '\n')
    if action['kind'] == 'file_dialog':
        path = directory / action.get('filename', 'output.txt' if action['save'] else 'sample.txt')
        if not before.get('editor_text_complete', True) or not after.get('editor_text_complete', True):
            return '文本超过检查范围，仅记录操作', None
        if action['save']:
            expected = normalize(before.get('editor_text', ''))
            passed = path.exists() and path.read_text(encoding='utf-8-sig') == expected
            return '保存文件与操作前编辑内容一致' if passed else '保存文件未生成或内容不一致', passed
        expected = path.read_text(encoding='utf-8-sig')
        passed = path.name in after.get('main_title', '') and normalize(after.get('editor_text', '')) == expected
        return '样本文件完整载入编辑器' if passed else '未确认样本内容完整载入', passed
    if feature == 'new':
        passed = after.get('tabs', 0) > before.get('tabs', 0)
    elif feature == 'edit':
        passed = after.get('editor_length', -1) > before.get('editor_length', -1)
    elif feature == 'multiline':
        passed = after.get('editor_lines', 0) > before.get('editor_lines', 0) and '中文' in after.get('editor_text', '')
    elif feature == 'selection':
        passed = after.get('editor_selection_end', 0)-after.get('editor_selection_start', 0) == after.get('editor_length', -1) > 0
    elif feature in ('undo', 'redo'):
        change = after.get('editor_length', 0)-before.get('editor_length', 0)
        passed = change < 0 if feature == 'undo' else change > 0
    elif feature == 'wrap':
        passed = after.get('editor_wrap') != before.get('editor_wrap')
    elif feature == 'zoom':
        passed = after.get('editor_zoom', 0) > before.get('editor_zoom', 0)
    elif feature == 'eol':
        passed = after.get('editor_eol') == 2
    elif action['kind'] == 'npp_dialog':
        if feature == 'find':
            content = after.get('editor_text', '').encode('utf-8')
            selection = content[after.get('editor_selection_start', 0):after.get('editor_selection_end', 0)]
            passed = selection == b'Second'
        elif feature == 'replace':
            previous = before.get('editor_text', '')
            passed = '123' in previous and after.get('editor_text') == previous.replace('123', '456')
        else:
            passed = after.get('editor_line') == 2
    elif feature in ('find', 'replace', 'goto', 'open', 'save'):
        return '已到达功能入口，等待实际操作及结果检查', None
    else:
        return '未配置业务检查', None
    labels = {'new': '文档数量增加', 'edit': '文本长度增加', 'multiline': '行数增加且保留中文',
              'selection': '选区覆盖全文', 'undo': '撤销后文本长度回退', 'redo': '重做后文本长度恢复',
              'wrap': '自动换行状态改变', 'zoom': '缩放级别增加', 'eol': '行尾模式为 LF',
              'find': '实际选区为 Second', 'replace': '123 全部替换为 456', 'goto': '光标位于第 2 行'}
    return labels.get(feature, feature)+('：通过' if passed else '：未满足预期'), passed
