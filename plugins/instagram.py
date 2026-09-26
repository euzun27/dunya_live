"""
DUNYATEK Instagram — official Instagram API (Instagram Login, graph.instagram.com).

What it does for the company's professional account:
  summary   — followers, recent posts with likes/comments, unanswered comments, recent DMs
  insights  — reach / views / interactions / likes / comments / follows over N days
  reply_comment, reply_dm — ALWAYS two steps, like mail_send: action=draft_* returns a
               preview and a draft_id; only action=send with that id (after the user said
               yes, ≥5 s later, within 15 min, one-shot) posts anything.

The token lives only in config/instagram.json (gitignored), written by
tools/instagram_kurulum.py, and is refreshed automatically before it expires (60 days).
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

API = "https://graph.instagram.com/v25.0"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "config" / "instagram.json"
REFRESH_AFTER_DAYS = 20
MIN_CONFIRM_SECONDS = 5
DRAFT_TTL_SECONDS = 15 * 60
_DRAFTS: dict[str, dict] = {}

PLUGIN = {
    "name": "instagram",
    "description": (
        "The company's Instagram business account via the official API. "
        "action='summary' for 'Instagram özetim', 'Instagram'da ne var', 'yeni yorum/mesaj var mı' "
        "(followers, recent posts, unanswered comments, recent DMs). action='insights' for "
        "statistics ('bu hafta erişim', 'Instagram istatistikleri', 'rapor') with days=7/30. "
        "Replying is ALWAYS two steps: action='draft_comment_reply' (comment_id + text) or "
        "'draft_dm_reply' (conversation_id + text) only prepares a preview and a draft_id — "
        "read it to the user and ask 'Göndereyim mi?'; only after a clear yes call "
        "action='send' with draft_id. Never send in the same turn as the draft. "
        "action='cancel' drops a draft. Write replies in polite Turkish on behalf of the company. "
        "For an Excel/PDF report, call insights then document_create."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "summary | insights | draft_comment_reply | draft_dm_reply | send | cancel"},
            "days": {"type": "INTEGER", "description": "insights: period in days (1-30, default 7)"},
            "count": {"type": "INTEGER", "description": "summary: how many recent posts (default 5, max 15)"},
            "comment_id": {"type": "STRING", "description": "draft_comment_reply: id from summary"},
            "conversation_id": {"type": "STRING", "description": "draft_dm_reply: id from summary"},
            "text": {"type": "STRING", "description": "Reply text (DM max ~1000 bytes)"},
            "draft_id": {"type": "STRING", "description": "send/cancel: id returned by a draft action"},
        },
        "required": ["action"],
    },
}


# ── token ────────────────────────────────────────────────────────────────────

def _load() -> dict | None:
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return data if data.get("access_token") else None
    except Exception:
        return None


def _save(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _maybe_refresh(cfg: dict) -> dict:
    """Long-lived tokens last 60 days; refresh every ~20 days so it never lapses."""
    try:
        age_days = (time.time() - float(cfg.get("saved_at", 0))) / 86400
        if age_days < REFRESH_AFTER_DAYS:
            return cfg
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token",
                                 "access_token": cfg["access_token"]}, timeout=20)
        if r.ok and r.json().get("access_token"):
            cfg = {**cfg, "access_token": r.json()["access_token"], "saved_at": time.time()}
            _save(cfg)
    except Exception:
        pass
    return cfg


class ApiError(Exception):
    pass


def _call(cfg: dict, method: str, path: str, params: dict | None = None,
          body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.request(method, f"{API}/{path.lstrip('/')}", params=params or {},
                         json=body, headers=headers, timeout=25)
    try:
        data = r.json()
    except Exception:
        data = {}
    if not r.ok or "error" in data:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        code = err.get("code")
        if code == 190:
            raise ApiError("Instagram bağlantı anahtarı geçersiz ya da süresi dolmuş — "
                           "tools/instagram_kurulum.py ile yeniden girilmeli.")
        raise ApiError(f"Instagram hatası ({code or r.status_code}): "
                       f"{(err.get('message') or r.text or '')[:160]}")
    return data


def _fmt(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("+0000", "+00:00")).astimezone().strftime("%d.%m %H:%M")
    except Exception:
        return ts or ""


# ── read ─────────────────────────────────────────────────────────────────────

def _summary(cfg: dict, count: int) -> str:
    me = _call(cfg, "GET", "me", {"fields": "user_id,username,name,account_type,followers_count,"
                                              "follows_count,media_count"})
    out = {
        "hesap": f"@{me.get('username')}",
        "takipci": me.get("followers_count"),
        "gonderi_sayisi": me.get("media_count"),
        "son_gonderiler": [],
        "cevaplanmamis_yorumlar": [],
        "son_mesajlar": [],
        "uyarilar": [],
    }
    media = _call(cfg, "GET", "me/media", {
        "fields": "id,caption,media_type,timestamp,like_count,comments_count,permalink",
        "limit": count}).get("data", [])
    for m in media:
        out["son_gonderiler"].append({
            "tarih": _fmt(m.get("timestamp", "")),
            "aciklama": (m.get("caption") or "")[:80],
            "begeni": m.get("like_count"), "yorum": m.get("comments_count"),
        })
        if not m.get("comments_count"):
            continue
        try:
            comments = _call(cfg, "GET", f"{m['id']}/comments", {
                "fields": "id,text,timestamp,username,replies{id}", "limit": 20}).get("data", [])
        except ApiError as e:
            out["uyarilar"].append(str(e))
            continue
        for c in comments:
            if c.get("username") == me.get("username") or (c.get("replies") or {}).get("data"):
                continue
            out["cevaplanmamis_yorumlar"].append({
                "comment_id": c.get("id"), "kimden": c.get("username"),
                "yorum": (c.get("text") or "")[:200], "tarih": _fmt(c.get("timestamp", "")),
                "gonderi": (m.get("caption") or "")[:40],
            })
    try:
        convs = _call(cfg, "GET", "me/conversations", {
            "platform": "instagram", "limit": 10,
            "fields": "id,updated_time,messages.limit(3){message,from,created_time}"}).get("data", [])
        for cv in convs:
            msgs = (cv.get("messages") or {}).get("data", [])
            if not msgs:
                continue
            last = msgs[0]
            sender = (last.get("from") or {})
            out["son_mesajlar"].append({
                "conversation_id": cv.get("id"),
                "kimden": sender.get("username") or sender.get("id"),
                "son_mesaj": (last.get("message") or "(medya/emoji)")[:200],
                "tarih": _fmt(last.get("created_time", "")),
                "bizden_mi": sender.get("id") == str(me.get("user_id")) or sender.get("username") == me.get("username"),
            })
    except ApiError as e:
        out["uyarilar"].append("Mesajlar okunamadı: " + str(e))
    out["talimat"] = ("Erdal Bey'e Türkçe ve kısa özetle: takipçi sayısı, son gönderilerin "
                      "performansı, cevaplanmamış yorumlar (kimden, ne diyor) ve cevap bekleyen "
                      "mesajlar ('bizden_mi' false olanlar). Cevap vermek isterse taslak hazırla. "
                      "ID'leri sesli okuma.")
    return json.dumps(out, ensure_ascii=False)


def _insights(cfg: dict, days: int) -> str:
    days = max(1, min(30, days))
    me = _call(cfg, "GET", "me", {"fields": "user_id,username,followers_count"})
    until = datetime.now()
    since = until - timedelta(days=days)
    metrics = "reach,views,accounts_engaged,total_interactions,likes,comments,shares,saves,follows_and_unfollows"
    data = _call(cfg, "GET", f"{me['user_id']}/insights", {
        "metric": metrics, "period": "day", "metric_type": "total_value",
        "since": int(since.timestamp()), "until": int(until.timestamp())}).get("data", [])
    values = {}
    for item in data:
        tv = item.get("total_value") or {}
        if "value" in tv:
            values[item.get("name")] = tv["value"]
        for b in tv.get("breakdowns", []) or []:   # follows_and_unfollows comes broken down
            for r in b.get("results", []) or []:
                key = f"{item.get('name')}:{'/'.join(r.get('dimension_values', []))}"
                values[key] = r.get("value")
    return json.dumps({
        "hesap": f"@{me.get('username')}", "donem": f"son {days} gün",
        "takipci_simdi": me.get("followers_count"), "metrikler": values,
        "aciklama": {"reach": "erişilen kişi", "views": "görüntülenme",
                     "accounts_engaged": "etkileşen hesap", "total_interactions": "toplam etkileşim",
                     "follows_and_unfollows": "takip edenler/bırakanlar"},
        "talimat": "Erdal Bey'e Türkçe, sade rakamlarla özetle. Rapor istenirse document_create ile Excel/PDF yap.",
    }, ensure_ascii=False)


# ── write (two-step) ─────────────────────────────────────────────────────────

def _draft(kind: str, target: str, text: str) -> str:
    text = (text or "").strip()
    if not target or not text:
        return "Taslak için hedef (comment_id / conversation_id) ve cevap metni gerekli."
    if kind == "dm" and len(text.encode("utf-8")) > 1000:
        return "Mesaj çok uzun (Instagram sınırı ~1000 bayt). Kısalt."
    draft_id = secrets.token_hex(3)
    _DRAFTS[draft_id] = {"kind": kind, "target": target, "text": text, "created": time.time()}
    what = "yoruma cevap" if kind == "comment" else "mesaja cevap"
    return (f"TASLAK HAZIR (henüz GÖNDERİLMEDİ) — draft_id={draft_id}\n"
            f"Tür: {what}\nMetin: {text}\n\n"
            "Erdal Bey'e metni oku ve 'Göndereyim mi?' diye sor. Açıkça 'evet/gönder' "
            "demeden action=send ÇAĞIRMA.")


def _send(cfg: dict, draft_id: str, player) -> str:
    d = _DRAFTS.get((draft_id or "").strip())
    if not d:
        return "Bu numarada bekleyen taslak yok. Önce taslak hazırla."
    age = time.time() - d["created"]
    if age > DRAFT_TTL_SECONDS:
        _DRAFTS.pop(draft_id, None)
        return "Taslağın süresi doldu (15 dk). Yeniden hazırlayıp onay al."
    if age < MIN_CONFIRM_SECONDS:
        return "Önce taslağı Erdal Bey'e okuyup onayını almalısın. Onay gelmeden gönderme."
    if d["kind"] == "comment":
        _call(cfg, "POST", f"{d['target']}/replies", params={"message": d["text"]})
        done = "Yoruma cevap verildi."
    else:
        conv = _call(cfg, "GET", d["target"], {"fields": "participants"})
        me = _call(cfg, "GET", "me", {"fields": "user_id,username"})
        others = [p for p in (conv.get("participants") or {}).get("data", [])
                  if p.get("id") != str(me.get("user_id")) and p.get("username") != me.get("username")]
        if not others:
            return "Mesajın alıcısı bulunamadı; hiçbir şey gönderilmedi."
        _call(cfg, "POST", "me/messages",
              body={"recipient": {"id": others[0]["id"]}, "message": {"text": d["text"]}})
        done = f"@{others[0].get('username') or others[0]['id']} kişisine mesaj gönderildi."
    _DRAFTS.pop(draft_id, None)
    if player:
        try:
            player.write_log(f"SYS: Instagram — {done}")
        except Exception:
            pass
    return done


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        cfg = _load()
        if not cfg:
            return ("Instagram hesabı henüz bağlı değil. Bilgisayarda tools/instagram_kurulum.py "
                    "çalıştırılıp Meta'dan alınan erişim kodu girilmeli. Erdal Bey'e bunu kısaca söyle.")
        cfg = _maybe_refresh(cfg)
        action = (parameters.get("action") or "summary").strip().lower()
        if action == "summary":
            try:
                count = int(parameters.get("count") or 5)
            except Exception:
                count = 5
            return _summary(cfg, max(1, min(15, count)))
        if action == "insights":
            try:
                days = int(parameters.get("days") or 7)
            except Exception:
                days = 7
            return _insights(cfg, days)
        if action == "draft_comment_reply":
            return _draft("comment", parameters.get("comment_id") or "", parameters.get("text") or "")
        if action == "draft_dm_reply":
            return _draft("dm", parameters.get("conversation_id") or "", parameters.get("text") or "")
        if action == "send":
            return _send(cfg, parameters.get("draft_id") or "", player)
        if action == "cancel":
            _DRAFTS.pop((parameters.get("draft_id") or "").strip(), None)
            return "Taslak iptal edildi, hiçbir şey gönderilmedi."
        return "Bilinmeyen işlem."
    except ApiError as e:
        msg = str(e)
        if "allowed window" in msg.lower() or "2534022" in msg:
            msg += " (Instagram, müşterinin son mesajından sonraki 24 saat içinde cevaba izin verir.)"
        return msg
    except requests.RequestException:
        return "Instagram'a bağlanılamadı (internet bağlantısını kontrol edin)."
    except Exception as e:
        return f"Instagram işleminde hata: {e.__class__.__name__}"
