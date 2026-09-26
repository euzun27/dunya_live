"""
DUNYATEK WhatsApp kurulumu (bu bilgisayarda, bir kerelik).

Calistirma (Dunya_live klasorunde):
    .venv\\Scripts\\python.exe tools\\whatsapp_kurulum.py

Meta gelistirici panelindeki WhatsApp > "API Setup" sayfasindan:
  - Phone number ID  (telefon numarasi kimligi, sadece rakam)
  - Access token     (erisim kodu; kalici kullanim icin "Sistem kullanicisi" kodu)
ve Uygulama ayarlari > Temel sayfasindan:
  - App secret       (uygulama gizli anahtari)
girilir. Kodlar ekranda gorunmez, sadece config/whatsapp.json'a yazilir (GitHub'a
gonderilmez). Sonunda Meta'ya girilecek Webhook adresi ve dogrulama kodu yazdirilir.
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "whatsapp.json"
GRAPH = "https://graph.facebook.com/v25.0"
TAILSCALE = r"C:\Program Files\Tailscale\tailscale.exe"


def public_url() -> str:
    try:
        out = subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dns = json.loads(out.stdout)["Self"]["DNSName"].rstrip(".")
        return f"https://{dns}/webhook/whatsapp"
    except Exception:
        return "https://<bilgisayar-adi>.ts.net/webhook/whatsapp"


def _clean(text: str) -> str:
    """Drop spaces and invisible control characters (Ctrl+V in a hidden prompt on
    Windows inserts a literal ^V instead of pasting)."""
    return "".join(ch for ch in (text or "") if ch.isprintable() and not ch.isspace())


def _read_clipboard() -> str:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout or ""
    except Exception:
        return ""


def _clear_clipboard() -> None:
    """The token/secret must not linger on the clipboard."""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _from_clipboard(prompt: str) -> str:
    """Enter = read the copied code from the clipboard; typing/pasting also works."""
    typed = _clean(getpass.getpass(f"{prompt} (kod gorunmez): "))
    value = typed or _clean(_read_clipboard())
    if value:
        print(f"  -> {len(value)} karakterlik kod alindi.")
    return value


def main() -> int:
    print("=" * 60)
    print(" DUNYATEK - WhatsApp (resmi API) baglama")
    print("=" * 60)
    old = {}
    try:
        old = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

    pid = _clean(input(f"Phone number ID{' (Enter = ' + old['phone_number_id'] + ')' if old.get('phone_number_id') else ''}: ")) \
        or old.get("phone_number_id", "")
    print()
    print("Kodlari YAPISTIRMANIZA GEREK YOK: tarayicida kodu kopyalayin (Copy / Ctrl+C),")
    print("sonra buraya donup sadece Enter'a basin - program kodu panodan kendisi okur.")
    token = _from_clipboard("Access token'i kopyaladiktan sonra Enter'a basin") or old.get("access_token", "")
    secret = _from_clipboard("App secret'i kopyaladiktan sonra Enter'a basin") or old.get("app_secret", "")
    _clear_clipboard()
    if not pid.isdigit():
        print("Phone number ID sadece rakamlardan olusmali (ornek: 1369310946256517). Iptal edildi.")
        return 1
    if len(token) < 50:
        print(f"Access token cok kisa ({len(token)} karakter) - yapistirma olmamis olabilir. "
              "Sag tikla yapistirip tekrar deneyin.")
        return 1
    if len(secret) != 32:
        print(f"App secret 32 karakter olmali, {len(secret)} karakter geldi. "
              "'Show' ile gorunen kodun tamamini kopyalayip sag tikla yapistirin.")
        return 1

    print("Kontrol ediliyor...")
    try:
        r = requests.get(f"{GRAPH}/{pid}", headers={"Authorization": f"Bearer {token}"},
                         params={"fields": "display_phone_number,verified_name,quality_rating"},
                         timeout=25)
        data = r.json()
    except Exception as e:
        print(f"Meta'ya baglanilamadi ({e.__class__.__name__}: {str(e)[:120]}).")
        return 1
    if not r.ok or "error" in data:
        print("Kabul edilmedi: " + str((data.get("error") or {}).get("message", r.status_code))[:200])
        return 1
    print(f"Baglandi: {data.get('verified_name')}  {data.get('display_phone_number')}")

    cfg = {**old, "phone_number_id": pid, "access_token": token, "app_secret": secret,
           "display_phone_number": data.get("display_phone_number"),
           "verify_token": old.get("verify_token") or secrets.token_urlsafe(24),
           "saved_at": time.time()}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass

    print()
    print("Kaydedildi. Simdi Meta panelinde WhatsApp > Configuration > Webhook bolumune")
    print("asagidaki iki bilgiyi girin (bunlar gizli sifre degildir, sadece bu kuruluma ozeldir):")
    print(f"  Callback URL : {public_url()}")
    print(f"  Verify token : {cfg['verify_token']}")
    print("Sonra 'messages' alanina abone olun (Subscribe). DUNYATEK'i yeniden baslatin.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nIptal edildi.")
        sys.exit(1)
