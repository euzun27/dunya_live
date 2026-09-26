"""
DUNYATEK mail sending — ALWAYS two steps, never in one call:

  1. action=draft  → recipients / attachments are resolved and checked, nothing is
                     sent. Returns a preview + draft_id that the assistant reads out
                     and asks "Göndereyim mi?".
  2. action=send   → only with that draft_id, only after the user explicitly said
                     yes. The draft must be at least a few seconds old (a model
                     cannot chain draft→send in the same breath) and expires after
                     15 minutes. What is sent is exactly the stored draft — the send
                     call cannot change recipients, text or attachments.

Credentials come from config/mail_accounts.json (tools/mail_kurulum.py). Nothing
secret is logged or returned.
"""

from __future__ import annotations

import mimetypes
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from plugins._dunyatek_common import find_file, pick_account, resolve_recipient, smtp_for

MAX_TOTAL_BYTES = 20 * 1024 * 1024     # Gmail limit is 25 MB incl. encoding overhead
MIN_CONFIRM_SECONDS = 5
DRAFT_TTL_SECONDS = 15 * 60
_DRAFTS: dict[str, dict] = {}

PLUGIN = {
    "name": "mail_send",
    "description": (
        "Sends an e-mail (optionally with file attachments) from the user's Gmail or "
        "company account. ALWAYS TWO STEPS: first call action='draft' — it sends NOTHING "
        "and returns a preview with a draft_id. Read the preview to the user (recipient "
        "name and address, subject, attachments, a short gist of the text) and ask "
        "'Göndereyim mi?'. Only after the user clearly says yes ('evet', 'gönder', "
        "'tamam gönder') call action='send' with that draft_id. If the user wants a "
        "change, make a NEW draft. Never call send in the same turn as draft and never "
        "without an explicit yes. action='cancel' drops a draft. Recipients can be names "
        "from the address book (contacts tool) or e-mail addresses. Attachments can be "
        "file names (searched in Documents\\DUNYATEK, Desktop, Documents, Downloads) or "
        "full paths. Write the subject and body in Turkish unless told otherwise, signed "
        "as Erdal Bey would sign. Trigger phrases: 'mail at', 'e-posta gönder', "
        "'şu dosyayı X'e gönder', 'maille yolla'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "draft | send | cancel"},
            "to": {"type": "STRING", "description": "Recipients, comma separated (names or addresses)"},
            "cc": {"type": "STRING", "description": "Optional CC, comma separated"},
            "subject": {"type": "STRING", "description": "Subject line"},
            "body": {"type": "STRING", "description": "Plain-text message body"},
            "attachments": {"type": "STRING", "description": "Optional files, comma separated (names or paths)"},
            "account": {"type": "STRING", "description": "Sending account name, e.g. 'gmail' or 'sirket' (default: first)"},
            "draft_id": {"type": "STRING", "description": "For send/cancel: the id returned by draft"},
        },
        "required": ["action"],
    },
}


def _split(text: str) -> list[str]:
    return [t.strip() for t in (text or "").replace(";", ",").split(",") if t.strip()]


def _make_draft(p: dict) -> str:
    acc = pick_account(p.get("account"))
    if not acc:
        return ("Gönderecek e-posta hesabı tanımlı değil. Bilgisayarda "
                "tools/mail_kurulum.py ile hesap eklenmeli.")

    problems, to_addrs, cc_addrs, shown = [], [], [], []
    for field, bucket in (("to", to_addrs), ("cc", cc_addrs)):
        for item in _split(p.get(field)):
            addr, cands = resolve_recipient(item)
            if addr:
                bucket.append(addr)
                name = cands[0]["name"] if cands else ""
                shown.append(f"{name} <{addr}>" if name else addr)
            elif cands:
                problems.append(f"'{item}' için rehberde birden fazla kişi var: " +
                                ", ".join(f"{c['name']} <{c['email']}>" for c in cands[:5]) +
                                " — hangisi?")
            else:
                problems.append(f"'{item}' rehberde yok ve bir e-posta adresi değil — "
                                "adresini sor (istersen contacts ile rehbere ekle).")
    if not to_addrs and not problems:
        problems.append("Alıcı belirtilmedi.")

    files, total = [], 0
    for item in _split(p.get("attachments")):
        hits = find_file(item)
        if not hits:
            problems.append(f"'{item}' dosyası bulunamadı.")
            continue
        exact = [h for h in hits if h.name.lower() == item.lower() or str(h).lower() == item.lower()]
        chosen = exact[0] if len(exact) >= 1 else (hits[0] if len(hits) == 1 else None)
        if chosen is None:
            problems.append(f"'{item}' için birden fazla dosya var: " +
                            ", ".join(str(h) for h in hits[:5]) + " — hangisi?")
            continue
        files.append(chosen)
        total += chosen.stat().st_size
    if total > MAX_TOTAL_BYTES:
        problems.append(f"Ekler toplam {total / 1_048_576:.1f} MB; sınır 20 MB.")

    if problems:
        return "Taslak hazırlanamadı, önce şunları netleştir:\n- " + "\n- ".join(problems)

    draft_id = secrets.token_hex(3)
    _DRAFTS[draft_id] = {
        "created": time.time(), "account": acc.get("name"),
        "to": to_addrs, "cc": cc_addrs,
        "subject": (p.get("subject") or "").strip() or "(konusuz)",
        "body": (p.get("body") or "").strip(),
        "files": [str(f) for f in files],
    }
    d = _DRAFTS[draft_id]
    gist = d["body"][:400] + ("…" if len(d["body"]) > 400 else "")
    return (
        f"TASLAK HAZIR (henüz GÖNDERİLMEDİ) — draft_id={draft_id}\n"
        f"Gönderen hesap: {acc.get('name')} ({acc.get('user')})\n"
        f"Kime: {', '.join(shown[:len(to_addrs)])}\n"
        + (f"Bilgi (CC): {', '.join(cc_addrs)}\n" if cc_addrs else "")
        + f"Konu: {d['subject']}\n"
        + (f"Ekler: {', '.join(f.name for f in files)} ({total / 1_048_576:.1f} MB)\n" if files else "Ekler: yok\n")
        + f"Metin: {gist}\n\n"
        "Bunu Erdal Bey'e Türkçe oku (adresleri tam okumana gerek yok, isimleri söyle) "
        "ve 'Göndereyim mi?' diye sor. Açıkça 'evet/gönder' demeden action=send ÇAĞIRMA."
    )


def _send(p: dict, player) -> str:
    draft_id = (p.get("draft_id") or "").strip()
    d = _DRAFTS.get(draft_id)
    if not d:
        return "Bu numarada bekleyen bir taslak yok. Önce action=draft ile taslak hazırla."
    age = time.time() - d["created"]
    if age > DRAFT_TTL_SECONDS:
        _DRAFTS.pop(draft_id, None)
        return "Taslağın süresi doldu (15 dk). Yeniden taslak hazırlayıp onay al."
    if age < MIN_CONFIRM_SECONDS:
        return ("Taslak az önce hazırlandı; önce Erdal Bey'e okuyup onayını almalısın. "
                "Onay gelmeden gönderme.")

    acc = pick_account(d["account"])
    if not acc:
        return "Gönderen hesap artık tanımlı değil."

    msg = EmailMessage()
    msg["From"] = formataddr((acc.get("display_name") or "", acc["user"]))
    msg["To"] = ", ".join(d["to"])
    if d["cc"]:
        msg["Cc"] = ", ".join(d["cc"])
    msg["Subject"] = d["subject"]
    msg["Message-ID"] = make_msgid()
    msg.set_content(d["body"] or " ")
    for path in d["files"]:
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype,
                               filename=path.replace("\\", "/").split("/")[-1])

    host, port = smtp_for(acc)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=40, context=ctx) as s:
                s.login(acc["user"], acc["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=40) as s:
                s.starttls(context=ctx)
                s.login(acc["user"], acc["password"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return "Gönderilemedi: giriş reddedildi (uygulama şifresini kontrol edin)."
    except Exception as e:
        return f"Gönderilemedi: {e.__class__.__name__} ({host}:{port})."

    _DRAFTS.pop(draft_id, None)
    if player:
        try:
            player.write_log(f"SYS: Mail gönderildi → {', '.join(d['to'])} | {d['subject']}")
        except Exception:
            pass
    ek = f", {len(d['files'])} ek ile" if d["files"] else ""
    return f"Mail gönderildi{ek}: {', '.join(d['to'])} — konu: {d['subject']}."


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = (parameters.get("action") or "").strip().lower()
        if action == "draft":
            return _make_draft(parameters)
        if action == "send":
            return _send(parameters, player)
        if action == "cancel":
            _DRAFTS.pop((parameters.get("draft_id") or "").strip(), None)
            return "Taslak iptal edildi, hiçbir şey gönderilmedi."
        return "Bilinmeyen işlem. Önce action=draft kullan."
    except Exception as e:
        return f"Mail işleminde hata: {e.__class__.__name__}. Hiçbir şey gönderilmedi."
