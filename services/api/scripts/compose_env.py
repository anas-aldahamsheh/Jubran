"""Print the database settings docker compose needs, as NAME=value lines (used by run_all.bat).

Older .env files have the database password only inside DATABASE_URL; the
database container needs it as POSTGRES_PASSWORD. Values already in .env win.
"""
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

env_file = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
values = {}
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")

url = values.get("DATABASE_URL", "")
parts = urlsplit(url) if "://" in url else None
derived = {
    "POSTGRES_USER": unquote(parts.username) if parts and parts.username else None,
    "POSTGRES_PASSWORD": unquote(parts.password) if parts and parts.password else None,
    "POSTGRES_DB": parts.path.lstrip("/") if parts and parts.path.strip("/") else None,
}
for name, value in derived.items():
    value = values.get(name) or value
    if value:
        print(f"{name}={value}")
