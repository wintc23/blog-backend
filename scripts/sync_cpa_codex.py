#!/usr/bin/env python3
"""Synchronize the existing server Codex API credential into private CPA config."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def synchronize(auth_path, config_path):
    auth_path, config_path = Path(auth_path), Path(config_path)
    try:
        auth = json.loads(auth_path.read_text())
        key = auth.get('OPENAI_API_KEY') if isinstance(auth, dict) else None
    except (FileNotFoundError, ValueError):
        key = None
    key = key if isinstance(key, str) and key.strip() else None
    config = json.loads(config_path.read_text())
    matches = [p for p in config.get('openai-compatibility', []) if p.get('name') == 'server-codex']
    if len(matches) != 1:
        raise ValueError('Expected one server-codex CPA provider')
    provider = matches[0]
    entries = [{'api-key': key, 'proxy-url': 'direct'}] if key else []
    disabled = key is None
    if provider.get('api-key-entries') == entries and provider.get('disabled', False) == disabled:
        return False
    provider.update({'api-key-entries': entries, 'disabled': disabled})
    original = config_path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=str(config_path.parent), prefix='.codex-sync-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode) & 0o640)
            json.dump(config, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(config_path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auth', default='/root/.codex/auth.json')
    parser.add_argument('--config', default='/etc/cliproxyapi/config.yaml')
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    try:
        changed = synchronize(args.auth, args.config)
        if changed and args.restart:
            subprocess.run(['/usr/bin/systemctl', 'try-restart', 'cliproxyapi.service'], check=True, timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, 'CPA credential synchronization failed; check private configuration and service status.\n')
    print('CPA credentials updated' if changed else 'CPA credentials already current')


if __name__ == '__main__':
    main()
