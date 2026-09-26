"""
DUNYATEK address book — who "Ahmet" is when the user says "Ahmet'e mail at".
Stored only on this PC in config/contacts.json (gitignored).
"""

from __future__ import annotations

from plugins._dunyatek_common import EMAIL_RE, find_contacts, load_contacts, save_contacts

PLUGIN = {
    "name": "contacts",
    "description": (
        "The user's e-mail address book (kişi rehberi). action=add to save a person "
        "('Ahmet Yılmaz'ın maili ahmet@firma.com, rehbere ekle'), find to look someone "
        "up, list to read all, remove to delete. mail_send already looks names up here "
        "by itself; only call this to add/change/list/remove entries or when the user "
        "asks who is in the book."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add | find | list | remove"},
            "name": {"type": "STRING", "description": "Person or company name"},
            "email": {"type": "STRING", "description": "E-mail address (for add)"},
            "note": {"type": "STRING", "description": "Optional note, e.g. 'muhasebeci', 'X firması'"},
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = (parameters.get("action") or "").strip().lower()
        name = (parameters.get("name") or "").strip()
        email_addr = (parameters.get("email") or "").strip()
        note = (parameters.get("note") or "").strip()

        if action == "add":
            if not name or not EMAIL_RE.match(email_addr):
                return "Rehbere eklemek için isim ve geçerli bir e-posta adresi gerekli."
            contacts = [c for c in load_contacts()
                        if c.get("email", "").lower() != email_addr.lower()]
            contacts.append({"name": name, "email": email_addr, "note": note})
            save_contacts(contacts)
            return f"{name} ({email_addr}) rehbere eklendi."

        if action == "remove":
            before = load_contacts()
            key = (email_addr or name).lower()
            after = [c for c in before if key not in (c.get("email", "").lower(), c.get("name", "").lower())]
            if len(after) == len(before):
                matches = find_contacts(name or email_addr)
                if len(matches) == 1:
                    after = [c for c in before if c is not matches[0] and c != matches[0]]
            if len(after) == len(before):
                return f"Rehberde '{name or email_addr}' bulunamadı."
            save_contacts(after)
            return f"'{name or email_addr}' rehberden silindi."

        if action == "find":
            found = find_contacts(name or email_addr)
            if not found:
                return f"Rehberde '{name or email_addr}' yok."
            return "Rehberde bulunanlar: " + "; ".join(
                f"{c['name']} <{c['email']}>" + (f" ({c['note']})" if c.get("note") else "")
                for c in found)

        if action == "list":
            contacts = load_contacts()
            if not contacts:
                return "Rehber henüz boş."
            return f"Rehberde {len(contacts)} kişi var: " + "; ".join(
                f"{c['name']} <{c['email']}>" for c in contacts)

        return "Bilinmeyen işlem. add, find, list veya remove kullan."
    except Exception as e:
        return f"Rehber işleminde hata: {e.__class__.__name__}"
