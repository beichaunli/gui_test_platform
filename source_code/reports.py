from html import escape as esc
import json
from pathlib import Path
import zipfile
from common import digest


def build(store, task):
    item = store.task(task)
    directory = store.directory(task)
    events = []
    after = 0
    while True:
        page = store.events(task, after, 1000)
        if not page:
            break
        events.extend(page)
        after = page[-1]['seq']
    value = {'task': item, 'events': events,
             'limits': '仅代表约定范围和预算；操作执行不等于业务正确；没有堆栈时仅定位到事件和控件。'}
    groups = {}
    for event in events:
        if event['status'] in ('check_failed', 'operation_failed', 'timeout', 'environment_error', 'notice'):
            signature = '|'.join(str(event.get(k, '')) for k in ('status', 'label', 'before_state'))
            groups.setdefault(signature, []).append(event['seq'])
    value['issue_groups'] = [{'signature': key, 'events': cases} for key, cases in groups.items()]
    (directory / 'report.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    def image(path):
        return f'<a href="{esc(path, quote=True)}"><img loading="lazy" src="{esc(path, quote=True)}"></a>' if path else '<p>无截图，见事件原因</p>'
    cards = []
    for e in events:
        cards.append(f'<article id="event-{e["seq"]}"><h3>#{e["seq"]} {esc(e.get("label", ""))} · {esc(e.get("status", ""))}</h3>'
                     f'<p>{esc(e.get("reason", ""))}</p><p>轮次 {e.get("round", 0)} · {esc(e.get("category", ""))} · {e.get("duration",0):.2f}s</p>'
                     f'<p>检查：{esc(e.get("check", "未验证功能正确性"))}</p>'
                     f'{image(e.get("before_image"))}{image(e.get("after_image"))}'
                     f'<details><summary>原始事件与定位</summary><pre>{esc(json.dumps(e, ensure_ascii=False, indent=2))}</pre></details></article>')
    runtime = item['runtime']
    html = '<!doctype html><meta charset="utf-8"><title>软件遍历测试报告</title><style>body{font:17px system-ui;max-width:1100px;margin:30px auto;background:#f4f7fa;color:#24364a}article,header{background:white;padding:24px;margin:18px 0;border:1px solid #d8e2ec;border-radius:12px}img{max-width:45%;max-height:340px;margin:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{font-size:28px}</style>'
    config = item['config']
    features = ''.join(f'<tr><td>{esc(f["name"])}</td><td>{esc(f["status"])}</td><td>{", ".join(str(n) for n in f["events"])}</td></tr>' for f in runtime.get('features', {}).values())
    scope = '<table><tr><th>功能</th><th>实际检查结果</th><th>事件编号</th></tr>'+features+'</table>' if features else '<p>通用 EXE 未配置业务功能清单，仅报告实际观察与执行，不提供全软件覆盖率。</p>'
    issues = ''.join('<p>同类观察 '+esc(g['signature'])+'：'+', '.join(f'<a href="#event-{n}">#{n}</a>' for n in g['events'])+'</p>' for g in value['issue_groups']) or '<p>本次记录未观察到待复核问题；不代表软件没有缺陷。</p>'
    html += f'<header><h1>Windows 软件自动遍历测试报告</h1><p>任务 {task} · {esc(item["status"])}</p><p>{esc(runtime.get("message", ""))}</p><p>{esc(value["limits"])}</p></header>'
    html += f'<article><h2>一、测试对象与方法</h2><p>软件：{esc(config.get("executable", ""))}</p><p>版本：{esc(config.get("version", "未知"))}</p><p>SHA256：{esc(config.get("sha256", ""))}</p><p>方法：界面状态广度优先遍历；恢复步骤及正常操作均保存。</p><p>环境：{esc(json.dumps(config.get("environment", {}), ensure_ascii=False))}</p><p>排除操作：{esc(config.get("excluded", ""))}</p></article>'
    html += f'<article><h2>二、执行概要与范围</h2><p>测试动作 {runtime.get("actions",0)}；恢复动作 {runtime.get("recoveries",0)}；全部事件 {len(events)}；观察状态 {len(runtime.get("graph",{}).get("states",[]))}；未探索分支 {runtime.get("graph",{}).get("pending",0)}。</p>{scope}<p>功能入口出现只证明到达该界面；没有明确检查点的操作不标为业务正确。主动停止和预算结束均可能留有未执行范围。</p></article>'
    html += f'<article><h2>三、待复核观察</h2>{issues}<p>归类不删除每次发生记录；人工备注和误报标记保留在对应事件。平台不自动确认安全漏洞。</p></article>'
    html += '<article><h2>四、全部过程与证据</h2><p>以下包括正常操作、子步骤、跳过、恢复和停止记录。定位依据为实际窗口、控件、状态和输入。</p></article>'+''.join(cards)
    html += f'<article><h2>五、原始任务配置</h2><pre>{esc(json.dumps(item, ensure_ascii=False, indent=2))}</pre><p>证据文件及哈希见 manifest.json；原始完整记录见 report.json。</p></article>'
    (directory / 'report.html').write_text(html, encoding='utf-8')
    files = [p for p in directory.rglob('*') if p.is_file() and p.name not in ('evidence.zip', 'manifest.json', 'stop', 'worker.log', 'owner.json') and p.suffix not in ('.tmp',)]
    manifest = [{'file': p.relative_to(directory).as_posix(), 'sha256': digest(p)} for p in files]
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with zipfile.ZipFile(directory / 'evidence.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in files + [directory / 'manifest.json']:
            archive.write(p, p.relative_to(directory).as_posix())
    return value
