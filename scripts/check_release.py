"""Check tracked files for local-only artifacts and configured secrets before pushing."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_EXAMPLES = {'examples/README.md', 'examples/postman_synthetic.docx'}
DENIED_PARTS = {'.venv', '.wheelhouse', 'backups', '__pycache__', '.pytest_cache', '.ruff_cache',
                'build', 'dist', '.vscode', 'private'}
TOKEN = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})')


def check_release(root: Path = ROOT) -> int:
    output = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True).stdout
    paths = [Path(name.decode('utf-8')) for name in output.split(b'\0') if name]
    secrets = []
    local_env = root / '.env'
    if local_env.exists():
        for line in local_env.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('"\'')
            if any(part in key for part in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'AUTH_KEY')) and len(value) >= 16:
                if value not in {'local-dev-only-change-before-exposure', 'sed_dev_password'}:
                    secrets.append(value.encode('utf-8'))
    for path in paths:
        name = path.as_posix()
        if (set(path.parts) & DENIED_PARTS or path.name == '.env'
                or (name.startswith('examples/') and name not in ALLOWED_EXAMPLES)
                or path.suffix in {'.dump', '.sqlite3', '.log', '.db'}):
            raise ValueError('Local-only artifact is tracked; publication refused')
        data = subprocess.run(['git', 'show', ':' + name], cwd=root,
                              capture_output=True, check=True).stdout
        if TOKEN.search(data) or any(secret in data for secret in secrets):
            raise ValueError('Credential detected in tracked content; publication refused')
    return len(paths)


if __name__ == '__main__':
    try:
        print(f'OK: {check_release()} tracked files; local artifacts and configured secrets excluded')
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Release check failed; no credentials or document contents were printed') from None
