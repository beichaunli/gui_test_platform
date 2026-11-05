"""Create the teacher delivery archive without local tasks, logs or bytecode."""
from pathlib import Path
import zipfile

from common import PROJECT, ROOT


def package(destination=None):
    destination = Path(destination or PROJECT.parent / (PROJECT.name+'_delivery.zip'))
    required = {'prompt', 'source_code', 'video', 'README.md'}
    if {p.name for p in PROJECT.iterdir() if p.name != '.git'} != required:
        raise ValueError('交付根目录必须仅含 prompt、source_code、video 和 README.md')
    if [p.name for p in (PROJECT/'prompt').iterdir()] != ['CONVERSATION_TRANSCRIPT.md']:
        raise ValueError('prompt 中只允许本次 GUI 对话文件')
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for directory in ('prompt', 'source_code', 'video'):
            archive.write(PROJECT/directory, PROJECT.name+'/'+directory+'/')
        for path in PROJECT.rglob('*'):
            relative = path.relative_to(PROJECT)
            if relative.parts[0] == '.git':
                continue
            if not path.is_file() or '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if relative.parts[:2] == ('source_code', 'data'):
                continue
            archive.write(path, PROJECT.name+'/'+relative.as_posix())
    return destination


if __name__ == '__main__':
    result = package()
    print('Delivery archive:', result.name, 'bytes:', result.stat().st_size)
