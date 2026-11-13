# AI Job Radar

AI eğitim ve veri etiketleme şirketlerinin ilan sayfalarını her 15 dakikada bir kontrol eder.
Yeni çıkan ilanı CV'ne göre süzer ve Telegram'dan haber verir.
GitHub Actions üzerinde çalışır. Sunucu yok, ücret yok.

## Nasıl çalışıyor

1. Her 15 dakikada GitHub bu kodu çalıştırır
2. Bot 30'dan fazla şirketin ilan sistemini direkt okur (Greenhouse, Lever, Ashby, Workable, site haritaları vb.)
3. Daha önce görmediği ilanları ayırır. İlk turda hiç mesaj atmaz, sadece mevcut ilanları kaydeder
4. Kelime filtresi açıkça uymayanları atar (satış, ofis işi, sadece ABD vb.)
5. Kalanları yapay zeka CV'ne göre 0-100 arası puanlar
6. Puanı yeterli olanlar Telegram'a düşer. Türkçe geçen ilanlar puandan bağımsız her zaman gelir
7. Her gün saat 10'da kısa bir durum mesajı gelir. Bozulan kaynak varsa orada görünür

Örnek mesaj

```
🆕 Turkish AI Tutor
🏢 Mercor · 📍 Remote
💰 $30-45/saat
⭐ 86/100  Türkçe rol, değerlendirme tecrüben uyuyor
İlana git
```

## Kurulum (yaklaşık 15 dakika)

### 1. Telegram botu

1. Telegram'da **@BotFather** hesabını aç, `/newbot` yaz
2. Bir isim ve sonu `bot` ile biten bir kullanıcı adı ver
3. Sana verdiği uzun **token**'ı kaydet (`123456:ABC...` gibi)
4. Yeni botunu aç ve ona herhangi bir mesaj at (örneğin "selam")
5. Tarayıcıda şu adresi aç, TOKEN yerine kendi token'ını koy

   `https://api.telegram.org/botTOKEN/getUpdates`

6. Çıkan yazıda `"chat":{"id":123456789` kısmındaki sayı senin **chat id**'n

### 2. Yapay zeka anahtarı (ücretsiz)

1. https://aistudio.google.com/apikey adresine gir, Google hesabınla giriş yap
2. **Create API key** de ve anahtarı kaydet

Not. Google'ın ücretsiz katmanında gönderilen veriler ürün geliştirme için kullanılabiliyor.
CV'nin oraya gitmesini istemezsen Groq da ücretsiz katman veriyor (https://console.groq.com).
O zaman aşağıdaki secret'lara ek olarak `LLM_PROVIDER` = `openai` ekle.

Anahtar eklemezsen bot yine çalışır, sadece kelime filtresiyle gönderir.

### 3. GitHub reposu

1. GitHub'da yeni bir repo aç. **Public** olsun (public repolarda Actions dakikası sınırsız)
2. Bu klasördeki her şeyi repoya yükle. `.github` klasörü de gitmeli, gizli klasör olduğu için gözden kaçabiliyor

   En kolayı boş repoyu açıp Claude'a adını söylemek, o yükler.

### 4. Secret'lar

Repo sayfasında **Settings → Secrets and variables → Actions → New repository secret**

| İsim | Değer |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | getUpdates'ten aldığın sayı |
| `LLM_API_KEY` | Google AI Studio anahtarı |
| `CV_TEXT` | CV'nin düz metin hali (kopyala yapıştır yeterli) |

İsteğe bağlı olanlar

| İsim | Ne işe yarar |
|---|---|
| `LLM_PROVIDER` | `gemini` (varsayılan) veya `openai` (Groq, OpenRouter vb.) |
| `LLM_MODEL` | Model adı. Boşsa `gemini-flash-latest` kullanılır |
| `LLM_BASE_URL` | `openai` seçtiysen API adresi. Boşsa Groq |
| `PROFILE_YAML` | `config/profile.yaml` ayarlarını gizli tutmak istersen aynı formatta buraya yaz |

Secret'lar public repoda bile kimseye görünmez.

### 5. Test

1. Repoda **Actions** sekmesine gir. Workflow'ları açmanı isterse onayla
2. Soldan **radar** seç, sağda **Run workflow** de, mod olarak `test` seçili kalsın
3. Bir iki dakika içinde Telegram'a şunlar gelir
   - her kaynağın kaç ilan döndüğü
   - şu an açık ilanlardan filtreye göre en iyi 5 tanesi
   - kısa bir durum özeti

Test modu kayıt dosyasını değiştirmez. Her şey yolundaysa başka bir şey yapmana gerek yok.
Bot 15 dakikada bir kendiliğinden çalışmaya başlar.

## Ayarlar

**`config/profile.yaml`**

- `min_hourly_usd` saatlik ücret alt sınırın. İlanda ücret yazıyorsa ve altındaysa elenir
- `min_score` yapay zeka puan eşiği. Çok mesaj gelirse yükselt, az gelirse düşür
- `interests`, `avoid` yapay zekaya ne istediğini anlatan serbest metin
- `always_include` bu kelimeler geçerse ilan her zaman gelir
- `exclude_title` başlıkta geçerse direkt elenir
- `skip_reposts` aynı başlıkla tekrar açılan ilanları gizlemek için

**`config/sources.yaml`**

- Bir kaynağı kapatmak için altına `enabled: false` ekle
- Yeni bir Greenhouse, Lever veya Ashby şirketi eklemek için üç satır yeter

  ```yaml
  - name: Şirket Adı
    type: greenhouse
    slug: sirketslug
  ```

  Slug, ilan adresindeki şirket ismidir. Örnek `job-boards.greenhouse.io/anthropic` için `anthropic`

Dosyaları GitHub sitesinde kalem ikonuna basıp düzenleyebilirsin. Bir sonraki turda geçerli olur.

## Kaynak listesi

| Grup | Kaynaklar |
|---|---|
| AI eğitim platformları | Mercor, micro1, Alignerr, Invisible, Mindrift, Innodata, Terac, AnyoneAI, Alpheva, Claru, Perle, Snorkel, OpenTrain (sadece kendi ilanları), AfterQuery |
| Dil ve yerelleştirme | Appen, RWS, Welo Data, Wing Data, DataForce, Sigma AI, Centific |
| Otomatik aranan | LILT, Productive Playhouse, Rise Data Labs, Gramian Consulting, iMerit, Lionbridge |
| AI laboratuvarları | xAI, Anthropic, OpenAI, Google DeepMind (sadece ilgili başlıklar) |
| Şimdilik kapalı | Turing, Remo Experts, SME Careers, Sovrano (herkese açık ilan listesi yok) |

"Otomatik aranan" kaynaklarda hangi ilan sistemini kullandıkları bilinmiyor.
Bot günde bir kez olası isimleri dener, bulursa kendisi kaydedip takibe başlar.

## Sık sorulanlar

**Neden bazen 15 dakikadan geç geliyor?**
GitHub zamanlanmış işleri yoğun saatlerde birkaç dakika geciktirebiliyor. Normal.

**Bir kaynak "Okunamayan" listesinde görünüyor**
Site geçici kapalı olabilir veya yapısını değiştirmiş olabilir.
Yaklaşık 4 saat üst üste bozuk kalırsa ayrıca uyarı gelir. Kalıcıysa Claude'a gönder, düzeltsin.

**Botu durdurmak istiyorum**
Actions → radar → sağ üstteki `...` → **Disable workflow**

**Repo 60 gün işlem görmezse GitHub zamanlamayı durdurur mu?**
Bot her gün kayıt dosyasını güncelleyip commit attığı için repo hep aktif kalır.

**Kendi bilgisayarımda denemek istersem**

```bash
pip install -r requirements.txt
python -m radar --mode dry          # mesaj atmaz, kaydetmez, ekrana yazar
python -m radar --mode dry --only "Mercor,Anthropic"
python -m pytest -q                 # testler
```

Yerelde CV için `config/cv.local.txt` dosyası kullanılabilir. Bu dosya repoya gitmez.
