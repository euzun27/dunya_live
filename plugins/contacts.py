"""
DUNYATEK address book — who "Ahmet" is when the user says "Ahmet'e mail at" or
"Ahmet'e WhatsApp'tan yaz". Stored only on this PC in config/contacts.json (gitignored).
Contacts can be added one by one or imported in bulk from a phone / Google Contacts
export (CSV or vCard .vcf).
"""

from __future__ import annotations

from plugins._dunyatek_common import (EMAIL_RE, find_contacts, find_file, import_contacts,
                                      load_contacts, normalize_phone, save_contacts)

PLUGIN = {
    "name": "contacts",
    "description": (
        "The user's address book (kişi rehberi) with names, e-mail addresses and phone "
        "numbers. action=add to save a person ('Ahmet Yılmaz'ın numarası 0532..., maili ..., "
        "rehbere ekle'), find to look someone up, list for a count and a sample, remove to "
        "delete, import to load a contacts export file (Google Contacts CSV or phone .vcf; "
        "'rehberimi aktar', 'kişiler dosyasını yükle') — file may be a name searched in "
        "Downloads/Desktop/Documents or a full path. mail_send and whatsapp already look names "
        "up here by themselves."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add | find | list | remove | import"},
            "name": {"type": "STRING", "description": "Person or company name"},
            "email": {"type": "STRING", "description": "E-mail address (for add)"},
            "phone": {"type": "STRING", "description": "Phone number (for add), any format"},
            "note": {"type": "STRING", "description": "Optional note, e.g. 'muhasebeci', 'X firması'"},
            "file": {"type": "STRING", "description": "import: export file name or path (.csv/.vcf)"},
        },
        "required": ["action"],
    },
}


def _show(c: dict) -> str:
    parts = [c.get("name", "")]
    if c.get("phone"):
        parts.append(f"+{c['phone']}")
    if c.get("email"):
        parts.append(f"<{c['email']}>")
    if c.get("note"):
        parts.append(f"({c['note']})")
    return " ".join(parts)


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = (parameters.get("action") or "").strip().lower()
        name = (parameters.get("name") or "").strip()
        email_addr = (parameters.get("email") or "").strip()
        phone = normalize_phone(parameters.get("phone") or "")
        note = (parameters.get("note") or "").strip()

        if action == "add":
            if not name or not (EMAIL_RE.match(email_addr) or phone):
                return "Rehbere eklemek için isim ve geçerli bir e-posta ya da telefon gerekli."
            contacts = load_contacts()
            existing = next((c for c in contacts
                             if (phone and c.get("phone") == phone)
                             or (email_addr and (c.get("email") or "").lower() == email_addr.lower())), None)
            if existing:
                existing.update({k: v for k, v in (("name", name), ("email", email_addr),
                                                   ("phone", phone), ("note", note)) if v})
                entry = existing
            else:
                entry = {"name": name, "email": email_addr, "phone": phone, "note": note}
                contacts.append(entry)
            save_contacts(contacts)
            return f"Rehbere kaydedildi: {_show(entry)}."

        if action == "import":
            hits = find_file(parameters.get("file") or "")
            hits = [h for h in hits if h.suffix.lower() in (".csv", ".vcf")] or hits
            if not hits:
                return ("Kişi dosyası bulunamadı. Dosyayı İndirilenler ya da Masaüstü klasörüne "
                        "koyup adını söyleyin (örn. contacts.csv veya kisiler.vcf).")
            res = import_contacts(hits[0])
            if not res["added"] and not res["updated"] and res["total"]:
                return (f"'{hits[0].name}' dosyasındaki kişilerin hepsi ZATEN rehberde, yeni kişi "
                        f"yok (daha önce aktarılmış). Rehberde toplam {res['total']} kişi var. "
                        "Erdal Bey'e bunun bir sorun olmadığını söyle.")
            if player:
                try:
                    player.write_log(f"SYS: Rehber aktarıldı ({hits[0].name}) — {res}")
                except Exception:
                    pass
            return (f"'{hits[0].name}' aktarıldı: {res['added']} yeni kişi eklendi, "
                    f"{res['updated']} kişi güncellendi, {res['skipped']} kayıt (isim ya da "
                    f"numara/e-posta olmadığı için) atlandı. Rehberde toplam {res['total']} kişi var.")

        if action == "remove":
            matches = find_contacts(name or email_addr or phone)
            if phone:
                matches = [c for c in load_contacts() if c.get("phone") == phone] or matches
            if len(matches) != 1:
                return (f"Rehberde '{name or email_addr or phone}' bulunamadı." if not matches else
                        "Birden fazla kişi eşleşti: " + "; ".join(_show(c) for c in matches[:5]))
            save_contacts([c for c in load_contacts() if c != matches[0]])
            return f"Rehberden silindi: {_show(matches[0])}."

        if action == "find":
            found = find_contacts(name or email_addr)
            if phone:
                found = [c for c in load_contacts() if c.get("phone") == phone] or found
            if not found:
                return f"Rehberde '{name or email_addr or phone}' yok."
            more = f" (+{len(found) - 10} kişi daha)" if len(found) > 10 else ""
            return "Rehberde bulunanlar: " + "; ".join(_show(c) for c in found[:10]) + more

        if action == "list":
            contacts = load_contacts()
            if not contacts:
                return "Rehber henüz boş."
            with_phone = sum(1 for c in contacts if c.get("phone"))
            with_mail = sum(1 for c in contacts if c.get("email"))
            return (f"Rehberde {len(contacts)} kişi var ({with_phone} telefonlu, {with_mail} "
                    f"e-postalı). Tamamını sesli okuma; belirli birini arayabilirsin. İlk 10: "
                    + "; ".join(c.get("name", "") for c in contacts[:10]))

        return "Bilinmeyen işlem. add, find, list, remove veya import kullan."
    except Exception as e:
        return f"Rehber işleminde hata: {e.__class__.__name__}"
