"""One-time disk to Qiniu migration. Delete only after byte-for-byte verification."""
import hashlib
import re
from pathlib import Path
from . import cloud


def migrate(source, kind='image-tools', delete_source=False):
    root = Path(source).resolve(strict=True)
    pattern = r'[a-f0-9]{32}\.(png|original)' if kind == 'image-tools' else r'[a-f0-9]{64}\.png'
    prefix = 'image-tools/' if kind == 'image-tools' else 'generation-artifacts/'
    count = 0
    for file in sorted(root.iterdir()):
        if file.is_symlink() or not file.is_file() or not re.fullmatch(pattern, file.name):
            continue
        data = file.read_bytes()
        key = prefix + file.name
        cloud.put(key, data, 'image/png' if file.suffix == '.png' else 'application/octet-stream')
        if hashlib.sha256(cloud.read(key)).digest() != hashlib.sha256(data).digest():
            raise RuntimeError('Cloud object verification failed; local copy retained')
        if delete_source:
            file.unlink()
        count += 1
    return count
