"""Create .env from .env.example, replacing every 'change-me' with a random secret."""

import secrets
import sys
from pathlib import Path

src, dst = Path(".env.example"), Path(".env")
if dst.exists():
    sys.exit(".env already exists; not overwriting")
lines = []
for line in src.read_text().splitlines():
    if line.endswith("=change-me"):
        line = line.removesuffix("change-me") + secrets.token_urlsafe(24)
    lines.append(line)
dst.write_text("\n".join(lines) + "\n")
dst.chmod(0o600)
print("wrote .env (mode 600)")
