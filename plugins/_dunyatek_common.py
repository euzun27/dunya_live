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
        return [c for c in data.get("contacts", [])
                if isinstance(c, dict) and (c.get("email") or c.get("phone"))]
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
    found = [c for c in find_contacts(text) if c.get("email")]
    if len(found) == 1:
        return found[0]["email"], found
    return None, found


def normalize_phone(raw: str) -> str:
    """Any Turkish/international format → WhatsApp digits (e.g. 905321112233).
    Returns '' when it doesn't look like a mobile/landline number."""
    raw = (raw or "").strip()
    if raw.startswith("+0") and not raw.startswith("+00"):   # "+0532…" typo → local number
        raw = raw[1:]
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    elif raw.startswith("+"):
        pass
    elif digits.startswith("0") and len(digits) == 11:        # 05321112233
        digits = "90" + digits[1:]
    elif len(digits) == 10 and digits[0] in "2345":           # 5321112233 / 3423610118
        digits = "90" + digits
    return digits if 10 <= len(digits) <= 15 else ""


def resolve_phone(text: str) -> tuple[str | None, list[dict]]:
    """Returns (whatsapp digits, candidates) from a number or an address-book name."""
    text = (text or "").strip()
    if re.fullmatch(r"[\d\s()+.-]+", text):
        num = normalize_phone(text)
        return (num or None), []
    found = [c for c in find_contacts(text) if c.get("phone")]
    if len(found) == 1:
        return found[0]["phone"], found
    return None, found


def _vcard_entries(text: str) -> list[dict]:
    text = re.sub(r"\r?\n[ \t]", "", text)                    # unfold continued lines
    out = []
    for block in re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, flags=re.S | re.I):
        entry = {"name": "", "phones": [], "emails": [], "org": ""}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key_u = key.upper()
            if "QUOTED-PRINTABLE" in key_u:
                import quopri
                charset = re.search(r"CHARSET=([\w-]+)", key_u)
                val = quopri.decodestring(val.encode()).decode(
                    (charset.group(1) if charset else "utf-8"), errors="replace")
            base = key_u.split(";")[0].split(".")[-1]
            if base == "FN":
                entry["name"] = val.strip()
            elif base == "N" and not entry["name"]:
                parts = [p for p in val.split(";") if p.strip()]
                entry["name"] = " ".join(reversed(parts[:2])).strip()
            elif base == "TEL":
                entry["phones"].append(val)
            elif base == "EMAIL":
                entry["emails"].append(val.strip())
            elif base == "ORG":
                entry["org"] = val.replace(";", " ").strip()
        out.append(entry)
    return out


def _csv_entries(text: str) -> list[dict]:
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        name = low.get("name") or " ".join(x for x in (
            low.get("first name") or low.get("given name"), low.get("middle name") or low.get("additional name"),
            low.get("last name") or low.get("family name")) if x).strip()
        phones = [v for k, v in low.items() if ("phone" in k or "telefon" in k or "mobile" in k)
                  and "type" not in k and "label" not in k and v]
        emails = [v for k, v in low.items() if ("e-mail" in k or "email" in k or "e-posta" in k)
                  and "type" not in k and "label" not in k and v]
        org = low.get("organization name") or low.get("organization 1 - name") or low.get("company") or ""
        out.append({"name": name, "phones": phones, "emails": emails, "org": org})
    return out


def import_contacts(path: Path) -> dict:
    """Merge a Google/Outlook CSV or a vCard (.vcf) export into config/contacts.json.
    Google exports join several numbers with ' ::: '. Never deletes existing entries."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1254", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    entries = _vcard_entries(text) if "BEGIN:VCARD" in text.upper() else _csv_entries(text)

    contacts = load_contacts()
    by_phone = {c.get("phone"): c for c in contacts if c.get("phone")}
    by_email = {(c.get("email") or "").lower(): c for c in contacts if c.get("email")}
    added = updated = skipped = 0
    for e in entries:
        phones = [normalize_phone(p) for chunk in e["phones"] for p in chunk.split(":::")]
        phones = [p for p in dict.fromkeys(phones) if p]
        emails = [m.strip() for chunk in e["emails"] for m in chunk.split(":::") if EMAIL_RE.match(m.strip())]
        name = e["name"] or e["org"] or (emails[0] if emails else "")
        if not name or not (phones or emails):
            skipped += 1
            continue
        phone = phones[0] if phones else ""
        email_addr = emails[0] if emails else ""
        existing = by_phone.get(phone) if phone else None
        existing = existing or (by_email.get(email_addr.lower()) if email_addr else None)
        if existing:
            changed = False
            if phone and not existing.get("phone"):
                existing["phone"] = phone; changed = True
            if email_addr and not existing.get("email"):
                existing["email"] = email_addr; changed = True
            updated += changed
            continue
        c = {"name": name, "email": email_addr, "phone": phone, "note": e["org"]}
        if len(phones) > 1:
            c["other_phones"] = phones[1:]
        contacts.append(c)
        if phone:
            by_phone[phone] = c
        if email_addr:
            by_email[email_addr.lower()] = c
        added += 1
    save_contacts(contacts)
    return {"added": added, "updated": updated, "skipped": skipped, "total": len(contacts)}


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
