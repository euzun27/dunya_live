"""
DUNYATEK e-posta kurulumu (bu bilgisayarda, bir kerelik).

Calistirma (Dunya_live klasorunde):
    .venv\\Scripts\\python.exe tools\\mail_kurulum.py

E-posta adresini ve UYGULAMA SIFRESINI burada siz yazarsiniz; sifre ekranda
gorunmez, sadece bu bilgisayardaki config/mail_accounts.json dosyasina kaydedilir
(bu dosya GitHub'a gonderilmez). Kaydetmeden once baglanti SALT OKUNUR test edilir.
"""

from __future__ import annotations

import getpass
import imaplib
import json
import os
import sys
from pathlib import Path

ACCOUNTS_FILE = Path(__file__).resolve().parent.parent / "config" / "mail_accounts.json"

PRESETS = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yandex": ("imap.yandex.com", 993),
}


def load() -> list[dict]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return data.get("accounts", []) if isinstance(data, dict) else list(data)
    except Exception:
        return []


def save(accounts: list[dict]) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    try:
        os.chmod(ACCOUNTS_FILE, 0o600)
    except Exception:
        pass


def test_login(host: str, port: int, user: str, password: str) -> tuple[bool, str]:
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=25)
    except Exception as e:
        return False, f"Sunucuya baglanilamadi: {host}:{port} ({e.__class__.__name__})"
    try:
        conn.login(user, password)
        status, data = conn.select("INBOX", readonly=True)
        if status != "OK":
            return False, "Gelen kutusu acilamadi."
        return True, f"Baglanti basarili. Gelen kutusunda {int(data[0] or 0)} ileti var."
    except imaplib.IMAP4.error:
        return False, ("Giris reddedildi. Normal Gmail sifresi DEGIL, 16 harfli "
                       "UYGULAMA SIFRESI girilmeli (bosluklar onemli degil).")
    except Exception as e:
        return False, f"Hata: {e.__class__.__name__}"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def test_smtp(entry: dict) -> tuple[bool, str]:
    """Logs in to the outgoing server and quits — sends nothing."""
    import smtplib
    import ssl
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from plugins._dunyatek_common import smtp_for
    host, port = smtp_for(entry)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, timeout=25, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=25)
            s.starttls(context=ctx)
        s.login(entry["user"], entry["password"])
        s.quit()
        return True, f"Giden posta hazir ({host}:{port})."
    except Exception as e:
        return False, f"Giden posta girisi basarisiz ({host}:{port}, {e.__class__.__name__})."


def main() -> int:
    print("=" * 60)
    print(" DUNYATEK - E-posta hesabi ekleme (salt okunur)")
    print("=" * 60)
    accounts = load()
    if accounts:
        print("Kayitli hesaplar: " + ", ".join(f"{a.get('name')} ({a.get('user')})" for a in accounts))

    kind = input("Hesap turu [gmail / outlook / yandex / diger] (Enter = gmail): ").strip().lower() or "gmail"
    if kind in PRESETS:
        host, port = PRESETS[kind]
    else:
        host = input("IMAP sunucusu (ornek: mail.sirketim.com): ").strip()
        port = int(input("Port (Enter = 993): ").strip() or 993)
    name = input(f"Bu hesaba bir ad verin (Enter = {kind if kind in PRESETS else 'sirket'}): ").strip() \
        or (kind if kind in PRESETS else "sirket")
    user = input("E-posta adresi: ").strip()
    password = getpass.getpass("Uygulama sifresi (yazarken gorunmez): ").replace(" ", "").strip()
    if not user or not password:
        print("Adres ve sifre bos olamaz. Iptal edildi.")
        return 1

    print("Baglanti test ediliyor (hicbir mail okundu isaretlenmez)...")
    ok, msg = test_login(host, port, user, password)
    print(msg)
    if not ok:
        return 1

    entry = {"name": name, "host": host, "port": port, "user": user,
             "password": password, "folder": "INBOX"}

    # Giden posta (mail gonderme) - ayni sifre kullanilir.
    display = input("Gonderilen maillerde gorunecek adiniz (orn. Erdal Uzun, Enter = bos): ").strip()
    if display:
        entry["display_name"] = display
    if kind not in PRESETS:
        guess = "smtp." + host.split(".", 1)[1] if host.startswith(("imap.", "mail.")) else host
        smtp_host = input(f"Giden posta (SMTP) sunucusu (Enter = {guess}): ").strip() or guess
        smtp_port = int(input("SMTP portu (Enter = 465): ").strip() or 465)
        entry["smtp_host"], entry["smtp_port"] = smtp_host, smtp_port
    print("Giden posta girisi test ediliyor (mail GONDERILMEZ)...")
    ok_smtp, smsg = test_smtp(entry)
    print(smsg)
    if not ok_smtp:
        print("Not: Okuma calisir, ama gonderme icin SMTP ayarini sonra duzeltmek gerekebilir.")

    accounts = [a for a in accounts if a.get("name") != name]
    accounts.append(entry)
    save(accounts)
    print(f"Kaydedildi: '{name}'. DUNYATEK'i yeniden baslatin ve 'maillerimi ozetle' deyin.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nIptal edildi.")
        sys.exit(1)
