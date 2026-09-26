"""
DUNYATEK mail summary — READ-ONLY.

Reads recent / unread mail over IMAP (Gmail first, company mail later) and hands a
compact list back to the assistant, which summarises it out loud in Turkish.

Safety rules (do not relax):
  * The mailbox is opened with select(readonly=True) and bodies are fetched with
    BODY.PEEK, so nothing is ever marked as read.
  * This plugin never sends, deletes, moves or flags anything.
  * Credentials live only in config/mail_accounts.json (gitignored), written by
    tools/mail_kurulum.py on this PC. They are never logged or returned.
"""

from __future__ import annotations

import email
import html
import imaplib
import json
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

ACCOUNTS_FILE = Path(__file__).resolve().parent.parent / "config" / "mail_accounts.json"
MAX_COUNT = 30
SNIPPET_CHARS = 300
MAX_FETCH_BYTES = 2_000_000   # bigger messages (attachments): headers only
TIMEOUT_S = 25

PLUGIN = {
    "name": "mail_summary",
    "description": (
        "Reads the user's e-mail inbox (Gmail and, if configured, company mail) "
        "READ-ONLY and returns recent or unread messages so you can summarise them. "
        "Use it when the user asks things like 'mail özeti', 'mail özeti ver', "
        "'maillerimi kontrol et', 'maillerimi özetle', 'yeni mail var mı', 'gelen "
        "kutum', 'bugün kimden mail geldi', 'e-postalarım', 'check my email'.It never sends, deletes or marks mail as read. "
        "Do NOT use web_search or the browser for the user's own mail."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "account": {
                "type": "STRING",
                "description": "Account name, e.g. 'gmail' or 'sirket'. Empty = all accounts.",
            },
            "count": {
                "type": "INTEGER",
                "description": f"How many messages at most (default 10, max {MAX_COUNT}).",
            },
            "only_unread": {
                "type": "BOOLEAN",
                "description": "Only unread messages (default true).",
            },
            "days": {
                "type": "INTEGER",
                "description": "Only mail from the last N days (optional, e.g. 1 = today).",
            },
        },
        "required": [],
    },
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_accounts() -> list[dict]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    items = data.get("accounts", data) if isinstance(data, dict) else data
    return [a for a in items if isinstance(a, dict) and a.get("user") and a.get("password")]


def _dec(value) -> str:
    """Decode an RFC 2047 header (Turkish characters etc.)."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _body_text(msg: email.message.Message) -> str:
    plain, rich = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if (part.get("Content-Disposition") or "").lower().startswith("attachment"):
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not rich:
            rich = _strip_html(text)
    body = plain or rich
    return re.sub(r"\s+", " ", body).strip()


def _fmt_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return _dec(value)


def _read_account(acc: dict, count: int, only_unread: bool, days: int | None) -> tuple[list[dict], str]:
    """Returns (messages, error). Never raises."""
    host = acc.get("host") or "imap.gmail.com"
    port = int(acc.get("port") or 993)
    folder = acc.get("folder") or "INBOX"
    name = acc.get("name") or acc.get("user")
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT_S)
    except Exception as e:
        return [], f"{name}: sunucuya bağlanılamadı ({e.__class__.__name__})"
    try:
        try:
            conn.login(acc["user"], acc["password"])
        except imaplib.IMAP4.error:
            return [], (f"{name}: giriş reddedildi — uygulama şifresi yanlış ya da "
                        "iptal edilmiş olabilir (tools/mail_kurulum.py ile yeniden girin).")
        status, _ = conn.select(folder, readonly=True)   # READ-ONLY: nothing gets marked read
        if status != "OK":
            return [], f"{name}: '{folder}' klasörü açılamadı."

        criteria = ["UNSEEN"] if only_unread else ["ALL"]
        if days:
            since = (datetime.now() - timedelta(days=max(0, days - 1))).strftime("%d-%b-%Y")
            criteria += ["SINCE", since]
        status, data = conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            return [], f"{name}: arama başarısız."
        uids = (data[0] or b"").split()
        total = len(uids)
        uids = uids[-count:][::-1]   # newest first

        out = []
        for uid in uids:
            status, sz = conn.uid("FETCH", uid, "(RFC822.SIZE)")
            size = 0
            m = re.search(rb"RFC822\.SIZE (\d+)", (sz[0] if sz and isinstance(sz[0], bytes) else b""))
            if m:
                size = int(m.group(1))
            what = "(BODY.PEEK[])" if size and size <= MAX_FETCH_BYTES else \
                   "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            status, parts = conn.uid("FETCH", uid, what)
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if status != "OK" or not raw:
                continue
            msg = email.message_from_bytes(raw)
            sender_name, sender_addr = parseaddr(_dec(msg.get("From")))
            snippet = _body_text(msg)[:SNIPPET_CHARS] if "HEADER.FIELDS" not in what \
                else "(büyük ileti / ek içeriyor — sadece başlık okundu)"
            out.append({
                "hesap": name,
                "kimden": f"{sender_name} <{sender_addr}>" if sender_name else sender_addr,
                "konu": _dec(msg.get("Subject")) or "(konu yok)",
                "tarih": _fmt_date(msg.get("Date")),
                "ozet": snippet,
            })
        if total > len(out):
            out.append({"hesap": name, "not": f"Toplam {total} ileti eşleşti; en yeni {len(out)} tanesi gösterildi."})
        return out, ""
    except Exception as e:
        return [], f"{name}: okuma hatası ({e.__class__.__name__})"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        accounts = _load_accounts()
        if not accounts:
            return ("Henüz bir e-posta hesabı tanımlı değil. Bilgisayarda "
                    "tools/mail_kurulum.py çalıştırılıp Gmail adresi ve uygulama şifresi "
                    "girilmeli. Kullanıcıya bunu Türkçe ve kısaca söyle.")

        want = (parameters.get("account") or "").strip().lower()
        if want:
            chosen = [a for a in accounts if want in (a.get("name") or "").lower()
                      or want in (a.get("user") or "").lower()]
            accounts = chosen or accounts
        try:
            count = int(parameters.get("count") or 10)
        except Exception:
            count = 10
        count = max(1, min(MAX_COUNT, count))
        only_unread = parameters.get("only_unread")
        only_unread = True if only_unread is None else bool(only_unread)
        days = parameters.get("days")
        try:
            days = int(days) if days else None
        except Exception:
            days = None

        messages, errors = [], []
        for acc in accounts:
            got, err = _read_account(acc, count, only_unread, days)
            messages += got
            if err:
                errors.append(err)

        if player:
            try:
                n = sum(1 for m in messages if "konu" in m)
                player.write_log(f"SYS: Mail okundu (salt okunur) — {n} ileti.")
            except Exception:
                pass

        payload = {
            "talimat": (
                "Bu iletileri Erdal Bey'e Türkçe, kısa ve sesli okunacak şekilde özetle: "
                "önce kaç yeni ileti olduğunu söyle, sonra önemli/acil görünenleri "
                "(fatura, ödeme, toplantı, müşteri, resmi kurum, son tarih) öne çıkar, "
                "reklam/bülten gibi önemsizleri tek cümlede geç. E-posta adreslerini "
                "sesli okuma, sadece gönderen adını söyle. Hiçbir iletiyi okundu "
                "işaretlemedin; bunu belirtmene gerek yok."
            ),
            "iletiler": messages,
            "hatalar": errors,
        }
        if not messages and not errors:
            payload["talimat"] = ("Kriterlere uyan ileti yok. Erdal Bey'e Türkçe olarak "
                                  "yeni (okunmamış) mail olmadığını söyle.")
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return f"E-posta okunurken beklenmeyen bir hata oldu: {e.__class__.__name__}"
