"""Verify the vendored skill against the pinned lock file (no network needed)."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
lock=json.loads((root/'third_party/lock.json').read_text(encoding='utf-8'))
for name,expected in lock['files'].items():
    path=root/name
    if not path.is_file():
        raise SystemExit(f'Missing vendor file: {name}')
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=expected:
        raise SystemExit(f'Vendor hash mismatch: {name}')
print('Vendor files match pinned revision:',lock['revision'])
