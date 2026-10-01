"""A public test link from this computer (Tailscale Funnel), in production mode.

Kept apart from local development, so neither affects the other:
- its own database (jubran_public, in the same PostgreSQL container),
- its own settings (.env.public at the project root, git-ignored; secrets generated once),
- its own ports on this computer only (website 3101, API 8101), behind one HTTPS link on port 8443,
- its own photo folder (services/api/media-public).
run_all.bat and the development database stay as they are. These production settings are
also what a cloud server needs later.

    run_public.bat                              start everything and print the link
    public_trial.py setup --admin-email EMAIL   first time: settings, secrets, database
    public_trial.py start [--rebuild]           what run_public.bat runs
    public_trial.py stop                        close the public link (Tailscale Funnel off)
"""
import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "services" / "api"
WEB_DIR = ROOT / "apps" / "web"
PUBLIC_ENV = ROOT / ".env.public"
PYTHON = Path(sys.executable)
DB_NAME = "jubran_public"
API_PORT, WEB_PORT = 8101, 3101
# HTTPS port of the link on this computer's Tailscale name. Not 443, so another project's public
# link on the same computer keeps working (Funnel allows 443, 8443 and 10000).
FUNNEL_PORT = 8443
MEDIA_DIR = API_DIR / "media-public"
BUILD_STAMP = WEB_DIR / ".next" / "public-link.txt"
HEADER = """# Public test link (Tailscale Funnel) from this computer, used only by run_public.bat.
# Production mode with its own database; development (.env, run_all.bat) is not affected.
# Secrets were generated once for this file: keep it private (it is git-ignored).
"""


def read_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values: dict) -> None:
    order = ["ADMIN_EMAIL", "ADMIN_PASSWORD", "PUBLIC_URL", "SECRET_KEY", "CSRF_SECRET", "DATA_ENCRYPTION_KEYS"]
    lines = [f"{name}={values[name]}" for name in order if values.get(name) is not None]
    lines += [f"{name}={value}" for name, value in values.items() if name not in order]
    PUBLIC_ENV.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")


def development_database_url() -> str:
    for path in (ROOT / ".env", API_DIR / ".env"):
        url = read_env(path).get("DATABASE_URL")
        if url:
            return url
    raise SystemExit("DATABASE_URL is missing from .env")


def with_database(url: str, name: str, driver: str = "postgresql+asyncpg") -> str:
    parts = urlsplit(url)
    return urlunsplit((driver, parts.netloc, f"/{name}", parts.query, ""))


async def ensure_database() -> None:
    import asyncpg
    connection = await asyncpg.connect(with_database(development_database_url(), "postgres", "postgresql"))
    try:
        if not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME):
            await connection.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"Created the database {DB_NAME}.")
    finally:
        await connection.close()


def tailscale() -> str | None:
    found = shutil.which("tailscale")
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    return found or (str(default) if default.exists() else None)


def tailscale_link() -> str | None:
    """https://<this computer>.<tailnet>.ts.net:8443, once Tailscale is installed and signed in."""
    exe = tailscale()
    if not exe:
        return None
    result = subprocess.run([exe, "status", "--json"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    name = (json.loads(result.stdout).get("Self") or {}).get("DNSName", "").rstrip(".")
    return f"https://{name}:{FUNNEL_PORT}" if name else None


def setup(admin_email: str | None, public_url: str | None) -> dict:
    from cryptography.fernet import Fernet
    values = read_env(PUBLIC_ENV)
    values.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
    values.setdefault("CSRF_SECRET", secrets.token_urlsafe(48))
    values.setdefault("DATA_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    values.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(18))
    if admin_email:
        values["ADMIN_EMAIL"] = admin_email.strip().lower()
    link = public_url or tailscale_link() or values.get("PUBLIC_URL")
    if link:
        values["PUBLIC_URL"] = link.rstrip("/")
    write_env(values)
    asyncio.run(ensure_database())
    MEDIA_DIR.mkdir(exist_ok=True)
    return values


def api_environment(values: dict) -> dict:
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "production",
        "DATABASE_URL": with_database(development_database_url(), DB_NAME),
        "SECRET_KEY": values["SECRET_KEY"],
        "CSRF_SECRET": values["CSRF_SECRET"],
        "DATA_ENCRYPTION_KEYS": values["DATA_ENCRYPTION_KEYS"],
        "ADMIN_EMAIL": values["ADMIN_EMAIL"],
        "ADMIN_PASSWORD": values["ADMIN_PASSWORD"],
        "CORS_ORIGINS": json.dumps([values["PUBLIC_URL"]]),
        "SECURE_COOKIES": "true",
        "COOKIE_SAMESITE": "lax",
        "MEDIA_DIR": str(MEDIA_DIR),
        "EMBEDDING_PROVIDER": "gemini",
        # How each live voice call went (never what was said), kept after the window closes.
        "LOG_FILE": str(API_DIR / "logs" / "public_api.log"),
    })
    return env


def web_environment(values: dict) -> dict:
    env = dict(os.environ)
    env.update({
        "NEXT_PUBLIC_API_URL": f"{values['PUBLIC_URL']}/api/v1",
        "NEXT_PUBLIC_SITE_URL": values["PUBLIC_URL"],
        # "Try as a guest" on Admin > Tables (admins only), handy while testing.
        "NEXT_PUBLIC_DEMO_MODE": "true",
        "NODE_ENV": "production",
    })
    return env


def website_changed_since_build() -> bool:
    build_id = WEB_DIR / ".next" / "BUILD_ID"
    if not build_id.exists():
        return True
    built = build_id.stat().st_mtime
    for folder in ("app", "components", "lib", "context", "public"):
        for path in (WEB_DIR / folder).rglob("*"):
            if path.is_file() and path.stat().st_mtime > built:
                return True
    return any((WEB_DIR / name).stat().st_mtime > built
               for name in ("next.config.ts", "package.json") if (WEB_DIR / name).exists())


def build_website(values: dict, force: bool) -> None:
    fresh = BUILD_STAMP.exists() and BUILD_STAMP.read_text(encoding="utf-8") == values["PUBLIC_URL"]
    if not force and fresh and not website_changed_since_build():
        return
    print("Building the website for the public link (a minute or two)...")
    subprocess.run("npm run build", cwd=WEB_DIR, env=web_environment(values), shell=True, check=True)
    BUILD_STAMP.write_text(values["PUBLIC_URL"], encoding="utf-8")


def port_busy(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def open_window(title: str, command: str, cwd: Path, env: dict) -> None:
    # One string, passed to cmd as written: a list would escape the inner quotes (\"), which cmd
    # does not understand, and paths with spaces would break.
    subprocess.Popen(f'cmd /k "title {title} && {command}"', cwd=cwd, env=env,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


def funnel(exe: str) -> None:
    """One HTTPS link: the website at /, the API at /api and its live updates at /ws."""
    for path, target in (("/", f"http://127.0.0.1:{WEB_PORT}"),
                         ("/api", f"http://127.0.0.1:{API_PORT}/api"),
                         ("/ws", f"http://127.0.0.1:{API_PORT}/ws")):
        subprocess.run([exe, "funnel", "--bg", f"--https={FUNNEL_PORT}", f"--set-path={path}", target], check=True)


def start(rebuild: bool) -> None:
    values = setup(None, None)
    missing = [name for name in ("ADMIN_EMAIL", "PUBLIC_URL") if not values.get(name)]
    if missing:
        raise SystemExit("Missing in .env.public: " + ", ".join(missing)
                         + " (install and sign in to Tailscale, then run: public_trial.py setup --admin-email ...)")
    exe = tailscale()
    if not exe:
        raise SystemExit("Tailscale is not installed: https://tailscale.com/download/windows")
    build_website(values, rebuild)
    if port_busy(API_PORT):
        print(f"API already running on port {API_PORT}.")
    else:
        open_window("Jubran Public API",
                    f'"{PYTHON}" -m uvicorn jubran.main:app --app-dir "{API_DIR / "src"}" --host 127.0.0.1 '
                    f"--port {API_PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1",
                    ROOT, api_environment(values))
    if port_busy(WEB_PORT):
        print(f"Website already running on port {WEB_PORT}.")
    else:
        open_window("Jubran Public Website", f"npm run start -- -p {WEB_PORT} -H 127.0.0.1",
                    WEB_DIR, web_environment(values))
    funnel(exe)
    print(f"\nPublic link: {values['PUBLIC_URL']}")
    print(f"Admin: {values['PUBLIC_URL']}/login  ({values['ADMIN_EMAIL']}; the password is in .env.public)")
    print("Keep this computer on and awake while testing. Close the two windows to stop.")


def stop() -> None:
    exe = tailscale()
    if exe:
        # Only this link's port: other public links on this computer stay open.
        subprocess.run([exe, "funnel", f"--https={FUNNEL_PORT}", "off"], check=False)
        print("The public link is closed. Close the two server windows to stop the servers.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("--admin-email")
    setup_parser.add_argument("--public-url")
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--rebuild", action="store_true")
    commands.add_parser("stop")
    args = parser.parse_args()
    if args.command == "setup":
        values = setup(args.admin_email, args.public_url)
        print("Settings ready in .env.public (admin: %s, link: %s)." % (values.get("ADMIN_EMAIL") or "not set",
                                                                        values.get("PUBLIC_URL") or "not set"))
    elif args.command == "start":
        start(args.rebuild)
    else:
        stop()


if __name__ == "__main__":
    main()
