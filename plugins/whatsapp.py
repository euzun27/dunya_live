"""
DUNYATEK WhatsApp — official WhatsApp Cloud API (Meta).

Incoming messages arrive through dashboard/whatsapp_webhook.py and are stored in
config/whatsapp_messages.jsonl; this plugin reads that store and sends through
graph.facebook.com/{phone_number_id}/messages.

Sending is ALWAYS two steps (same rules as mail_send / instagram): draft_* only
prepares a preview + draft_id; send posts exactly that draft, ≥5 s later, within
15 min, once. WhatsApp only allows free-form replies within 24 h of the customer's
last message — outside that window the draft is refused with an explanation.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

import requests

from dashboard.whatsapp_webhook import append_messages, load_config, read_messages
from plugins._dunyatek_common import _norm, find_file, normalize_phone, resolve_phone

GRAPH = "https://graph.facebook.com/v25.0"
SEEN_FILE = Path(__file__).resolve().parent.parent / "config" / "whatsapp_seen.json"
WINDOW_SECONDS = 24 * 3600
MIN_CONFIRM_SECONDS = 5
DRAFT_TTL_SECONDS = 15 * 60
MAX_MEDIA_BYTES = 100 * 1024 * 1024   # WhatsApp document limit
_DRAFTS: dict[str, dict] = {}

PLUGIN = {
    "name": "whatsapp",
    "description": (
        "The company's WhatsApp Business line via the official API. action='summary' for "
        "'WhatsApp özetim', 'WhatsApp'ta ne var', 'yeni WhatsApp mesajı var mı' (new messages "
        "grouped by customer). Sending is ALWAYS two steps: action='draft_text' (to + text) or "
        "'draft_file' (to + file [+ text as caption]) only prepares a preview and a draft_id — "
        "read it to the user and ask 'Göndereyim mi?'; only after a clear yes call "
        "action='send' with draft_id. Never send in the same turn as the draft. "
        "action='cancel' drops a draft. 'to' is a customer name as shown in the summary or "
        "a phone number with country code. Write replies in polite Turkish for the company. "
        "Files may be names (Documents\\DUNYATEK, Desktop, Documents, Downloads) or full "
        "paths — e.g. create with document_create, then draft_file. This is NOT the old "
        "send_message tool; prefer this one for WhatsApp."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "summary | draft_text | draft_file | send | cancel"},
            "hours": {"type": "INTEGER", "description": "summary: look back N hours (default: since last summary, max 168)"},
            "to": {"type": "STRING", "description": "Customer name from the summary, or phone number"},
            "text": {"type": "STRING", "description": "Message text (or caption for a file)"},
            "file": {"type": "STRING", "description": "draft_file: file name or path"},
            "draft_id": {"type": "STRING", "description": "send/cancel: id returned by a draft"},
        },
        "required": ["action"],
    },
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def _load_seen() -> float:
    try:
        return float(json.loads(SEEN_FILE.read_text(encoding="utf-8")).get("seen_until", 0))
    except Exception:
        return 0.0


def _save_seen(ts: float) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"seen_until": ts}), encoding="utf-8")


def _contacts() -> dict[str, dict]:
    """wa_id → {name, last_in} from the message store."""
    people: dict[str, dict] = {}
    for m in read_messages():
        p = people.setdefault(m.get("wa_id"), {"name": "", "last_in": 0})
        if m.get("direction") == "in":
            if m.get("name"):
                p["name"] = m["name"]
            p["last_in"] = max(p["last_in"], int(m.get("ts") or 0))
    return people


def _resolve(to: str) -> tuple[str | None, str]:
    to = (to or "").strip()
    if re.fullmatch(r"[\d\s()+.-]+", to):
        digits = normalize_phone(to)
        return (digits, "") if digits else (None, "Geçerli bir telefon numarası değil.")
    people = _contacts()
    q = _norm(to)
    hits = [(w, p) for w, p in people.items() if q and q in _norm(p["name"])]
    if len(hits) == 1:
        return hits[0][0], hits[0][1]["name"]
    if len(hits) > 1:
        return None, "Birden fazla kişi eşleşti: " + ", ".join(p["name"] or w for w, p in hits[:5])
    # Not a recent WhatsApp sender — try the address book (imported phone contacts).
    num, cands = resolve_phone(to)
    if num:
        return num, cands[0]["name"] if cands else ""
    if len(cands) > 1:
        return None, "Rehberde birden fazla kişi eşleşti: " + ", ".join(
            f"{c['name']} (+{c['phone']})" for c in cands[:5])
    return None, (f"'{to}' ne WhatsApp'tan yazanlar arasında ne de rehberde (telefonuyla) var. "
                  "Telefon numarasıyla (ör. 0532 111 22 33) belirt.")


def _window_ok(wa_id: str) -> tuple[bool, str]:
    last = _contacts().get(wa_id, {}).get("last_in", 0)
    if not last:
        return False, ("Bu kişi bize hiç yazmamış. WhatsApp kuralları gereği ilk mesaj sadece "
                       "Meta onaylı şablonla atılabilir (henüz tanımlı değil).")
    if time.time() - last > WINDOW_SECONDS:
        return False, (f"Bu kişinin son mesajı {_fmt(last)} tarihinde; 24 saat geçtiği için "
                       "WhatsApp serbest cevaba izin vermiyor. Müşteri tekrar yazınca cevap verilebilir.")
    return True, ""


def _post(cfg: dict, path: str, **kw) -> dict:
    r = requests.post(f"{GRAPH}/{path}", headers={"Authorization": f"Bearer {cfg['access_token']}"},
                      timeout=60, **kw)
    try:
        data = r.json()
    except Exception:
        data = {}
    if not r.ok or "error" in data:
        err = (data or {}).get("error", {})
        if err.get("code") == 190:
            raise RuntimeError("WhatsApp erişim anahtarı geçersiz/süresi dolmuş — "
                               "tools/whatsapp_kurulum.py ile yenilenmeli.")
        raise RuntimeError(f"WhatsApp hatası ({err.get('code') or r.status_code}): "
                           f"{(err.get('message') or r.text or '')[:160]}")
    return data


# ── actions ──────────────────────────────────────────────────────────────────

def _summary(hours: int | None) -> str:
    msgs = [m for m in read_messages() if m.get("direction") == "in"]
    now = time.time()
    since = now - hours * 3600 if hours else max(_load_seen(), now - 7 * 24 * 3600)
    fresh = [m for m in msgs if (m.get("ts") or 0) > since]
    by_person: dict[str, dict] = {}
    for m in fresh:
        p = by_person.setdefault(m["wa_id"], {"kisi": m.get("name") or m["wa_id"],
                                              "numara": m["wa_id"], "mesajlar": []})
        p["mesajlar"].append({"saat": _fmt(m["ts"]), "mesaj": (m.get("text") or "")[:300]})
    for p in by_person.values():
        p["mesajlar"] = p["mesajlar"][-8:]
        last = _contacts().get(p["numara"], {}).get("last_in", 0)
        p["cevap_verilebilir"] = (now - last) < WINDOW_SECONDS
    _save_seen(now)
    if not by_person:
        return json.dumps({"talimat": "Yeni WhatsApp mesajı yok. Erdal Bey'e kısaca söyle."},
                          ensure_ascii=False)
    return json.dumps({
        "yeni_mesaj_sayisi": len(fresh), "kisiler": list(by_person.values()),
        "talimat": ("Erdal Bey'e Türkçe özetle: kaç kişiden kaç mesaj var, her kişi ne istiyor "
                    "(sipariş, fiyat, şikayet, randevu, acil olanlar önce). Numaraları sesli okuma. "
                    "Cevap isterse draft_text ile taslak hazırla."),
    }, ensure_ascii=False)


def _draft(p: dict, with_file: bool) -> str:
    cfg = load_config()
    wa_id, info = _resolve(p.get("to") or "")
    if not wa_id:
        return "Taslak hazırlanamadı: " + info
    ok, why = _window_ok(wa_id)
    if not ok and not cfg.get("allow_outside_window"):
        return "Taslak hazırlanamadı: " + why
    text = (p.get("text") or "").strip()
    path = None
    if with_file:
        hits = find_file(p.get("file") or "")
        if not hits:
            return f"Taslak hazırlanamadı: '{p.get('file')}' dosyası bulunamadı."
        path = hits[0]
        if path.stat().st_size > MAX_MEDIA_BYTES:
            return "Dosya WhatsApp için çok büyük (100 MB sınırı)."
    elif not text:
        return "Mesaj metni boş."
    draft_id = secrets.token_hex(3)
    _DRAFTS[draft_id] = {"wa_id": wa_id, "name": info, "text": text,
                         "file": str(path) if path else None, "created": time.time()}
    who = info or wa_id
    return (f"TASLAK HAZIR (henüz GÖNDERİLMEDİ) — draft_id={draft_id}\n"
            f"Kime: {who}\n"
            + (f"Dosya: {path.name}\n" if path else "")
            + (f"Metin: {text}\n" if text else "")
            + "\nErdal Bey'e oku ve 'Göndereyim mi?' diye sor. Açıkça 'evet/gönder' demeden "
              "action=send ÇAĞIRMA.")


def _send(draft_id: str, player) -> str:
    cfg = load_config()
    d = _DRAFTS.get((draft_id or "").strip())
    if not d:
        return "Bu numarada bekleyen taslak yok. Önce taslak hazırla."
    age = time.time() - d["created"]
    if age > DRAFT_TTL_SECONDS:
        _DRAFTS.pop(draft_id, None)
        return "Taslağın süresi doldu (15 dk). Yeniden hazırlayıp onay al."
    if age < MIN_CONFIRM_SECONDS:
        return "Önce taslağı Erdal Bey'e okuyup onayını almalısın. Onay gelmeden gönderme."

    pid = cfg["phone_number_id"]
    if d["file"]:
        path = Path(d["file"])
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            media = _post(cfg, f"{pid}/media", data={"messaging_product": "whatsapp", "type": mime},
                          files={"file": (path.name, fh, mime)})
        kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
        obj = {"id": media["id"]}
        if kind == "document":
            obj["filename"] = path.name
        if d["text"]:
            obj["caption"] = d["text"]
        body = {"messaging_product": "whatsapp", "to": d["wa_id"], "type": kind, kind: obj}
    else:
        body = {"messaging_product": "whatsapp", "to": d["wa_id"], "type": "text",
                "text": {"body": d["text"]}}
    res = _post(cfg, f"{pid}/messages", json=body)
    _DRAFTS.pop(draft_id, None)
    append_messages([{"id": (res.get("messages") or [{}])[0].get("id") or secrets.token_hex(8),
                      "direction": "out", "wa_id": d["wa_id"], "name": d["name"],
                      "ts": int(time.time()), "type": "document" if d["file"] else "text",
                      "text": (f"[dosya] {Path(d['file']).name} " if d["file"] else "") + d["text"]}])
    who = d["name"] or d["wa_id"]
    if player:
        try:
            player.write_log(f"SYS: WhatsApp gönderildi → {who}")
        except Exception:
            pass
    return f"WhatsApp mesajı gönderildi: {who}."


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        if not load_config():
            return ("WhatsApp henüz bağlı değil. Bilgisayarda tools/whatsapp_kurulum.py ile "
                    "kurulum yapılmalı. Erdal Bey'e kısaca söyle.")
        action = (parameters.get("action") or "summary").strip().lower()
        if action == "summary":
            try:
                hours = int(parameters["hours"]) if parameters.get("hours") else None
            except Exception:
                hours = None
            return _summary(min(hours, 168) if hours else None)
        if action == "draft_text":
            return _draft(parameters, with_file=False)
        if action == "draft_file":
            return _draft(parameters, with_file=True)
        if action == "send":
            return _send(parameters.get("draft_id") or "", player)
        if action == "cancel":
            _DRAFTS.pop((parameters.get("draft_id") or "").strip(), None)
            return "Taslak iptal edildi, hiçbir şey gönderilmedi."
        return "Bilinmeyen işlem."
    except requests.RequestException:
        return "WhatsApp'a bağlanılamadı (internet bağlantısını kontrol edin). Hiçbir şey gönderilmedi."
    except Exception as e:
        return f"WhatsApp: {str(e)[:220]}"
