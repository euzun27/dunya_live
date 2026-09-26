"""
DUNYATEK — WhatsApp Cloud API webhook + local message store.

WhatsApp has no "list my messages" API: incoming messages are only ever pushed to
a public HTTPS webhook. This module runs that webhook as its OWN tiny server on
127.0.0.1:WEBHOOK_PORT (never the dashboard), which Tailscale Funnel exposes as
https://<pc>.ts.net/webhook/whatsapp. Only two routes exist:

  GET  /webhook/whatsapp  — Meta's one-time verification (hub.verify_token)
  POST /webhook/whatsapp  — message events; rejected unless the
                            X-Hub-Signature-256 HMAC matches the app secret

Messages are appended to config/whatsapp_messages.jsonl (gitignored); the
whatsapp plugin reads that file. Config (tokens, app secret, verify token) lives in
config/whatsapp.json (gitignored), written by tools/whatsapp_kurulum.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "whatsapp.json"
STORE_FILE = BASE_DIR / "config" / "whatsapp_messages.jsonl"
WEBHOOK_PORT = 8010
_LOCK = threading.Lock()


# ── config / store ───────────────────────────────────────────────────────────

def load_config() -> dict | None:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if data.get("access_token") and data.get("phone_number_id") else None
    except Exception:
        return None


def append_messages(records: list[dict]) -> None:
    if not records:
        return
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, STORE_FILE.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_messages() -> list[dict]:
    try:
        with _LOCK:
            lines = STORE_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out, seen = [], set()
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        key = r.get("id")
        if key and key in seen:          # Meta may deliver the same event twice
            continue
        seen.add(key)
        out.append(r)
    return out


def _text_of(m: dict) -> str:
    t = m.get("type")
    if t == "text":
        return (m.get("text") or {}).get("body", "")
    if t in ("image", "video", "document", "audio", "sticker"):
        part = m.get(t) or {}
        label = {"image": "fotoğraf", "video": "video", "document": "belge",
                 "audio": "ses kaydı", "sticker": "çıkartma"}[t]
        extra = part.get("caption") or part.get("filename") or ""
        return f"[{label}]" + (f" {extra}" if extra else "")
    if t == "location":
        loc = m.get("location") or {}
        return f"[konum] {loc.get('name') or ''} {loc.get('latitude')},{loc.get('longitude')}".strip()
    if t == "button":
        return (m.get("button") or {}).get("text", "[düğme]")
    if t == "interactive":
        i = m.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply") or {}).get("title")) or "[etkileşim]"
    if t == "reaction":
        return f"[tepki] {(m.get('reaction') or {}).get('emoji', '')}"
    return f"[{t}]"


def parse_event(payload: dict) -> list[dict]:
    """Webhook JSON → list of inbound message records."""
    records = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                     for c in value.get("contacts", []) or []}
            for m in value.get("messages", []) or []:
                sender = m.get("from")
                media = next((m.get(k) for k in ("image", "video", "document", "audio")
                              if m.get(k)), None) or {}
                records.append({
                    "id": m.get("id"), "direction": "in", "wa_id": sender,
                    "name": names.get(sender) or "", "ts": int(m.get("timestamp") or time.time()),
                    "type": m.get("type"), "text": _text_of(m),
                    "media_id": media.get("id"), "filename": media.get("filename"),
                })
    return records


def valid_signature(app_secret: str, raw: bytes, header: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


# ── server ───────────────────────────────────────────────────────────────────

try:   # module level: FastAPI resolves the (string) annotations from these globals
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse, Response
except Exception:   # dashboard disabled without fastapi; serve() is never reached then
    FastAPI = Request = PlainTextResponse = Response = None


def build_app(on_messages=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/webhook/whatsapp")
    async def verify(request: Request):
        cfg = load_config() or {}
        q = request.query_params
        if (q.get("hub.mode") == "subscribe" and cfg.get("verify_token")
                and hmac.compare_digest(q.get("hub.verify_token", ""), cfg["verify_token"])):
            return PlainTextResponse(q.get("hub.challenge", ""))
        return Response(status_code=403)

    @app.post("/webhook/whatsapp")
    async def receive(request: Request):
        cfg = load_config() or {}
        raw = await request.body()
        if not valid_signature(cfg.get("app_secret", ""), raw,
                               request.headers.get("x-hub-signature-256", "")):
            return Response(status_code=403)
        try:
            records = parse_event(json.loads(raw))
        except Exception:
            return Response(status_code=200)   # never make Meta retry malformed events
        append_messages(records)
        if records and on_messages:
            try:
                on_messages(records)
            except Exception:
                pass
        return Response(status_code=200)

    @app.get("/")
    async def root():
        return Response(status_code=404)

    return app


async def serve(on_messages=None) -> None:
    """Started by DashboardServer.serve() only when WhatsApp is configured."""
    if not load_config():
        return
    import uvicorn
    cfg = uvicorn.Config(build_app(on_messages), host="127.0.0.1", port=WEBHOOK_PORT,
                         log_level="warning")
    print(f"[WhatsApp] Webhook listening on 127.0.0.1:{WEBHOOK_PORT} (public via Tailscale Funnel)")
    await uvicorn.Server(cfg).serve()
