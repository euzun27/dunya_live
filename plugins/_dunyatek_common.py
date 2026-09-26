"""
Shared helpers for the DUNYATEK mail / contacts / document plugins (not a plugin itself —
the loader skips files starting with '_').

Everything personal lives in config/ and is gitignored:
  config/mail_accounts.json  — written by tools/mail_kurulum.py (passwords)
  config/contacts.json       — the user's address book
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_FILE = BASE_DIR / "config" / "mail_accounts.json"
CONTACTS_FILE = BASE_DIR / "config" / "contacts.json"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Outgoing (SMTP) servers for the IMAP presets in tools/mail_kurulum.py.
SMTP_PRESETS = {
    "imap.gmail.com": ("smtp.gmail.com", 465),
    "outlook.office365.com": ("smtp.office365.com", 587),
    "imap.yandex.com": ("smtp.yandex.com", 465),
}


# ── mail accounts ────────────────────────────────────────────────────────────

def load_accounts() -> list[dict]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("accounts", data) if isinstance(data, dict) else data
    return [a for a in items if isinstance(a, dict) and a.get("user") and a.get("password")]


def pick_account(name: str | None) -> dict | None:
    accounts = load_accounts()
    if not accounts:
        return None
    want = (name or "").strip().lower()
    if want:
        for a in accounts:
            if want in (a.get("name") or "").lower() or want in (a.get("user") or "").lower():
                return a
    return accounts[0]


def smtp_for(acc: dict) -> tuple[str, int]:
    if acc.get("smtp_host"):
        return acc["smtp_host"], int(acc.get("smtp_port") or 465)
    host = acc.get("host") or "imap.gmail.com"
    if host in SMTP_PRESETS:
        return SMTP_PRESETS[host]
    # Typical hosting: imap.example.com / mail.example.com → smtp on the same name.
    return re.sub(r"^imap\.", "smtp.", host), 465


# ── contacts ─────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    text = (text or "").casefold().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


def load_contacts() -> list[dict]:
    try:
        data = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
        return [c for c in data.get("contacts", []) if isinstance(c, dict) and c.get("email")]
    except Exception:
        return []


def save_contacts(contacts: list[dict]) -> None:
    CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTACTS_FILE.write_text(json.dumps({"contacts": contacts}, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def find_contacts(query: str) -> list[dict]:
    q = _norm(query)
    if not q:
        return []
    exact = [c for c in load_contacts() if _norm(c.get("name")) == q]
    if exact:
        return exact
    return [c for c in load_contacts()
            if q in _norm(c.get("name")) or q in _norm(c.get("email"))
            or q in _norm(c.get("note", ""))]


def resolve_recipient(text: str) -> tuple[str | None, list[dict]]:
    """Returns (email, candidates). email is set only when unambiguous."""
    text = (text or "").strip()
    if EMAIL_RE.match(text):
        return text, []
    found = find_contacts(text)
    if len(found) == 1:
        return found[0]["email"], found
    return None, found


# ── files ────────────────────────────────────────────────────────────────────

def user_folder(key: str) -> Path:
    home = Path(os.path.expanduser("~"))
    original = key
    key = _norm(key)
    mapping = {
        "desktop": "Desktop", "masaustu": "Desktop",
        "documents": "Documents", "belgeler": "Documents", "belgelerim": "Documents",
        "downloads": "Downloads", "indirilenler": "Downloads",
    }
    sub = mapping.get(key)
    if sub:
        # OneDrive often redirects Desktop/Documents.
        for base in (home / "OneDrive", home):
            if (base / sub).is_dir():
                return base / sub
        return home / sub
    # A real path: keep it exactly as given (Turkish letters, case).
    return Path(os.path.expandvars(os.path.expanduser((original or "").strip().strip('"'))))


def output_dir() -> Path:
    d = user_folder("documents") / "DUNYATEK"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_file(name_or_path: str) -> list[Path]:
    """Exact path, or a name searched in DUNYATEK output, Desktop, Documents, Downloads."""
    raw = (name_or_path or "").strip().strip('"')
    if not raw:
        return []
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if p.is_file():
        return [p]
    want = _norm(Path(raw).name)
    hits: list[Path] = []
    for folder in (output_dir(), user_folder("desktop"), user_folder("documents"),
                   user_folder("downloads")):
        try:
            for f in folder.rglob("*"):
                if f.is_file() and (_norm(f.name) == want or _norm(f.stem) == want
                                    or want in _norm(f.name)):
                    hits.append(f)
                    if len(hits) >= 10:
                        break
        except Exception:
            continue
    # exact names first, newest first
    hits = list(dict.fromkeys(hits))
    hits.sort(key=lambda f: (_norm(f.name) != want and _norm(f.stem) != want,
                             -f.stat().st_mtime))
    return hits
