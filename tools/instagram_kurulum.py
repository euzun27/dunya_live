"""
DUNYATEK Instagram kurulumu (bu bilgisayarda, bir kerelik).

Calistirma (Dunya_live klasorunde):
    .venv\\Scripts\\python.exe tools\\instagram_kurulum.py

Meta gelistirici panelinde (Instagram girisiyle API kurulumu) "Belirtec olustur" ile
aldiginiz uzun kodu burada siz yapistirirsiniz; ekranda gorunmez ve sadece bu
bilgisayardaki config/instagram.json dosyasina kaydedilir (GitHub'a gonderilmez).
Kod 60 gun gecerlidir; DUNYATEK onu suresi dolmadan kendisi yeniler.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).resolve().parent.parent / "config" / "instagram.json"
API = "https://graph.instagram.com/v25.0"


def main() -> int:
    print("=" * 60)
    print(" DUNYATEK - Instagram hesabi baglama")
    print("=" * 60)
    print("Meta panelinden kopyaladiginiz kodu yapistirin (sag tik ya da Ctrl+V).")
    token = getpass.getpass("Erisim kodu (yazarken gorunmez): ").strip()
    if len(token) < 50:
        print("Kod cok kisa gorunuyor - tamamini kopyaladiginizdan emin olun.")
        return 1
    print("Kontrol ediliyor...")
    try:
        r = requests.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"},
                         params={"fields": "user_id,username,account_type,followers_count,media_count"},
                         timeout=25)
        data = r.json()
    except Exception as e:
        print(f"Instagram'a baglanilamadi ({e.__class__.__name__}).")
        return 1
    if not r.ok or "error" in data:
        print("Kod kabul edilmedi: " + str((data.get("error") or {}).get("message", r.status_code))[:200])
        return 1
    print(f"Baglandi: @{data.get('username')}  | hesap turu: {data.get('account_type')}  | "
          f"takipci: {data.get('followers_count')}  | gonderi: {data.get('media_count')}")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"access_token": token, "user_id": data.get("user_id"),
                                      "username": data.get("username"), "saved_at": time.time()},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass
    print("Kaydedildi. DUNYATEK'i yeniden baslatin ve 'Instagram ozetim' deyin.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nIptal edildi.")
        sys.exit(1)
