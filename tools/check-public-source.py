"""Check the Git INDEX before publishing. Never emit a matched secret value.

This bounded deny-list is a release guard, not proof that every kind of secret
has been detected. Private delivery archives must never be staged, even encrypted.
Only the standard library is required; it also runs before dependencies in CI.
"""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 8 * 1024 * 1024
DENIED_EXTENSIONS = {'.zip', '.apk', '.aab', '.whl', '.onnx', '.pt', '.pth', '.tflite',
    '.pem', '.key', '.jks', '.keystore', '.dump', '.img', '.tar', '.db', '.npy', '.npz', '.csv'}


def inspect_file(name, data, mode='100644'):
    path = PurePosixPath(name)
    lower = name.lower()
    reasons = []
    if mode not in ('100644', '100755'):
        reasons.append('linked entry or submodule')
    if (path.suffix.lower() in DENIED_EXTENSIONS or any(part.lower() in
            ('credentials', 'node_modules', '.output', 'local-output', 'wheelhouse', 'state', 'runtime') for part in path.parts)
            or path.name in ('.env', 'device.toml', 'google-services.json', 'KEY-REVOCATION.json')
            or (path.name.startswith('.env.') and path.name != '.env.example')
            or lower.endswith(('.encrypted.json', '.sqlite', '.sqlite3', '.private', '.img.xz'))
            or ('service-account' in lower and lower.endswith('.json'))
            or ('passphrase' in lower and lower.endswith('.txt'))):
        reasons.append('private/runtime/binary distribution path')
    if len(data) > MAX_FILE_BYTES:
        reasons.append('oversized public-source file')
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{40}', data):
        reasons.append('private key material')
    if re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b', data):
        reasons.append('GitHub credential-shaped value')
    if path.suffix.lower() == '.json':
        try:
            value = json.loads(data)
            if isinstance(value, dict) and value.get('type') == 'service_account' and value.get('private_key'):
                reasons.append('Google server credential JSON')
        except (ValueError, UnicodeDecodeError):
            pass
    return reasons


def main():
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    total = count = 0
    for entry in entries:
        if not entry: continue
        head, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = head.decode().split()
        name = raw_name.decode('utf-8')
        if stage != '0':
            failures.append({'path':name, 'reasons':['unresolved index conflict']}); continue
        size = int(subprocess.check_output(['git','cat-file','-s',oid], cwd=ROOT))
        if size > MAX_FILE_BYTES:
            failures.append({'path':name, 'reasons':['oversized public-source file']}); continue
        data = subprocess.check_output(['git', 'cat-file', 'blob', oid], cwd=ROOT)
        reasons = inspect_file(name, data, mode)
        if reasons: failures.append({'path':name, 'reasons':reasons})
        total += size; count += 1
    print(json.dumps({'passed':not failures,'indexed_files_checked':count,'indexed_bytes_checked':total,
                      'failures':failures,'matched_secret_values_printed':False}))
    return int(bool(failures))


if __name__ == '__main__': sys.exit(main())
