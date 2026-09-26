# DUNYATEK — Yapılacaklar

## Bekleyen (Erdal Bey'in kararıyla ertelendi)

### WhatsApp: canlı şirket numarasını bağlama (2. aşama) — EN SONA
- Şu an WhatsApp, Meta'nın **test numarasıyla** çalışıyor: +1 555 139 4712
  (Phone number ID 1369310946256517, WABA 1114512654595546).
- Canlı numara: **+90 535 926 49 50** — telefondaki *WhatsApp Business uygulamasında*
  kayıtlı (WABA "Dünyatek Bilişim – WhatsApp Business Uygulaması", kod 4305737106312503,
  kalite: Yüksek). İşletme portföyü "Dünyatek Bilişim" **doğrulanmış**.
- ⚠️ Meta panelinde "Add phone number" ile bu numarayı EKLEMEYİN: numara WhatsApp Business
  uygulamasından çıkar. Doğru yol **Coexistence** (uygulama + API aynı numarada, 6 aylık
  geçmiş ve kişiler senkronize olur).
- Coexistence için seçenekler:
  1. Dünyatek Bilişim'in Meta **Tech Provider** olması + Embedded Signup (ücretsiz; işletme
     zaten doğrulanmış).
  2. Bir Meta iş ortağı (360dialog, YCloud vb.) üzerinden bağlanmak (hızlı, aylık ücretli).
- Canlıya geçerken ayrıca: kalıcı erişim kodu (Sistem kullanıcısı token'ı), ödeme yöntemi
  (sadece işletmenin başlattığı şablon mesajlar için), gerekirse onaylı mesaj şablonları.
- DUNYATEK tarafında değişecek tek şey: `tools/whatsapp_kurulum.py` ile yeni Phone number ID
  ve kalıcı token girilmesi. Webhook adresi aynı kalır.

## Şu anki sıra
1. Kişi rehberini DUNYATEK'e aktarmak (Google CSV / vCard) — `contacts` eklentisi, action=import.
2. WhatsApp'ı test numarasıyla bir süre denemek.
3. En son: yukarıdaki canlı numara (2. aşama).
