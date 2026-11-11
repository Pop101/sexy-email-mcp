"""One-time credential entry. Independent from the running MCP server."""
import argparse
import getpass
import json
import os
import re
import ssl
import imaplib
import smtplib
import sys
import tempfile
from pathlib import Path
from pydantic import EmailStr, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1] / ".secrets"
FILE = ROOT / "accounts.json"
EMAIL = TypeAdapter(EmailStr)


def setup(replace=False):
    if not sys.stdin.isatty():
        raise ValueError("Run from an interactive terminal")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    if ROOT.is_symlink() or FILE.is_symlink():
        raise ValueError("Refusing symlinked credential paths")
    os.chmod(ROOT, 0o700)
    if FILE.exists() and json.loads(FILE.read_text()).get("accounts") and not replace:
        raise ValueError("Credentials already configured; use --replace to rotate them")
    count = int(input("Number of Runbox accounts (1 or 2): ").strip())
    if count not in (1, 2):
        raise ValueError("Account count must be 1 or 2")
    rows = []
    for position in range(1, count + 1):
        name = input(f"Account {position} label (for example primary): ").strip()
        address = input(f"Account {position} Runbox login address: ").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", name):
            raise ValueError("Invalid account label")
        if name in {row["name"] for row in rows}:
            raise ValueError("Account labels must be unique")
        try:
            EMAIL.validate_python(address)
        except ValidationError:
            raise ValueError("Invalid login address") from None
        password = getpass.getpass("Runbox app password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if not password or password != confirmation:
            raise ValueError("Missing or mismatched password; no changes made")
        rows.append({"name": name, "address": address, "password": password, "send_as": []})
    fd, temporary = tempfile.mkstemp(dir=ROOT, prefix=".accounts-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as dest:
            json.dump({"accounts": rows}, dest, indent=2)
            dest.write("\n")
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(temporary, FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Saved login credentials privately (0600). No restart needed.")


def verify():
    rows = json.loads(FILE.read_text())["accounts"]
    if not rows:
        raise ValueError("Runbox credentials have not been configured")
    for account in rows:
        name = account["name"]
        try:
            with imaplib.IMAP4_SSL("mail.runbox.com", 993, ssl_context=ssl.create_default_context(), timeout=12) as imap:
                imap.login(account["address"], account["password"])
                status, _ = imap.list()
                if status != "OK":
                    raise ValueError("Folder list refused")
            print(f"{name}: IMAP OK")
        except Exception:
            print(f"{name}: IMAP failed (check login or app password)")
        try:
            with smtplib.SMTP_SSL("mail.runbox.com", 465, context=ssl.create_default_context(), timeout=12) as smtp:
                smtp.login(account["address"], account["password"])
                if smtp.noop()[0] != 250:
                    raise ValueError("SMTP check refused")
            print(f"{name}: SMTP OK")
        except Exception:
            print(f"{name}: SMTP failed (check login or app password)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Private Runbox credential setup")
    parser.add_argument("--replace", action="store_true", help="Replace existing credentials")
    parser.add_argument("--verify", action="store_true", help="Test IMAP login and SMTP NOOP without sending")
    args = parser.parse_args()
    try:
        verify() if args.verify else setup(args.replace)
    except Exception:
        print("Credential setup failed. Existing configuration preserved.", file=sys.stderr)
        sys.exit(1)
