# Güvenlik ve kalite denetimi — 29.09.2026

## Kapsam ve yöntem

Ürün kaynak kodu, CLI, Git taşıması, Oracle parser alt süreci, SQLite state, model HTTP istemcisi, SMTP teslimatı, HTML/MIME üretimi, temizlik işlemi, paketleme ve kilitli bağımlılıklar incelendi. Kontroller yerel statik inceleme, `ruff` güvenlik kuralları, `pip-audit`, saldırı odaklı regresyon testleri, tüm `pytest` paketi, sözleşme/manifest doğrulaması ve izole wheel derlemesidir. Dış servislerde sızma testi yapılmadı.

| Alan | Sonuç ve kanıt |
|---|---|
| Komut enjeksiyonu | Git ve parser alt süreçleri argüman dizisiyle, `shell=False` çalışıyor. Git ref/OID sınırları doğrulanıyor. HTTP yönlendirmesi kapatıldı; credential helper dosyası rastgele, özel izinli ve işlem sonrası siliniyor. |
| Python modül enjeksiyonu | Oracle parser alt süreci `python -I -m` ile başlatılıyor; çalışma klasöründeki sahte `db_change_analyzer` paketini yüklememe testi geçti. |
| SQL enjeksiyonu | Ürün Oracle'a bağlanmıyor ve DDL çalıştırmıyor. SQLite veri sorguları parametreli; `PRAGMA busy_timeout` yalnız sınırlandırılmış tamsayı konfigürasyonundan üretiliyor. Migration SQL'i paket içindeki sabit dosyalardan okunuyor. |
| HTML/MIME enjeksiyonu | HTML Jinja otomatik kaçış kullanıyor; V5 rapor bağlantıları HTTPS, host, yol ve kontrol karakteri sınırlarından geçiyor. Düz metin şablonundaki `autoescape=False` HTML yürütme yolu değildir. E-posta MIME başlıkları standart kütüphane ile üretiliyor. |
| Model çıktısı / prompt enjeksiyonu | V5 model girdisi doğrulanmış, gösterime uygun fact'lerden oluşuyor; ham SQL modele gönderilmiyor. Yanıt şema, digest, evidence ve deterministik yeniden ifade denetiminden geçmeden e-postaya alınmıyor. Model için araç çağrısı tanımlı değil. |
| Dosya yolu ve silme | Temizlik yalnız doğrudan `exports`/`backups` çocuklarını kabul ediyor. Sembolik bağlantılar, başka state dosyası yoluyla hazırlanmış manifest, güncel veya korunan export silinmesi reddediliyor. |
| Bağımlılıklar | `pip-audit --no-deps --disable-pip` ile 20 runtime ve 10 geliştirme paketi tarandı. Geliştirme tarafındaki `pytest 9.0.2`, `setuptools 80.9.0`, `wheel 0.45.1` uyarıları sırasıyla `9.0.3`, `83.0.0`, `0.46.2` ile giderildi. Güncel iki kilit dosyasında bildirilen açık: **0**. Bu sonuç tarama veritabanının tarihindeki bilinen açıklarla sınırlıdır. |
| Statik güvenlik uyarıları | `ruff --select S` kalan 9 uyarı verdi: 5 kabuksuz alt süreç (`S603`), 1 düz metin Jinja (`S701`), 1 geliştirme aracı Java yolu (`S607`), 1 iç kilit assert'i (`S101`), 1 kapatma sırasındaki yutulan ikinci hata (`S110`). Bunlar incelendi; otomatik uyarı sayısı doğrulanmış açık sayısı değildir. |

## Uygulanan düzeltmeler

1. Git fetch'te HTTP yönlendirmesi kapatıldı. Kimlik bilgisi verilen fetch'in başka hosta yönlendirilmesi başarısız olur; adres değişikliği ayrıca gözden geçirilmelidir.
2. Git askpass dosyası sabit `.git-askpass` adından çıkarıldı; `mkstemp` ile benzersiz ve özel izinli üretiliyor. Var olan aynı adlı dosyayı ezmeme ve işlem sonunda temizleme testleri eklendi.
3. Parser alt süreci `-I` ile izole edildi; `PYTHONPATH` aktarımı kaldırıldı.
4. Retention plan/apply alanı doğrudan izinli klasör çocuklarına indirildi; sembolik bağlantı, eski olmayan ve sonradan korunan dosyalar tekrar denetleniyor.
5. Geliştirme bağımlılıkları ve build isolation pin'leri güvenlik yamalı sürümlere yükseltildi; release manifest hash'leri yenilendi.

Kaynak duyurular: [pytest 9.0.3 değişiklikleri](https://docs.pytest.org/en/stable/changelog.html), [setuptools 83.0.0 değişiklikleri](https://setuptools.pypa.io/en/latest/history.html), [wheel güvenlik duyurusu](https://github.com/advisories/GHSA-8rrh-rw8j-w5fx).

## Doğrulama sonucu

- Güncel `pytest 9.0.3` ile tam paket: **214 geçti, 1 atlandı** (`symlink` izni Windows hostunda yok); 253,22 saniye.
- 5 sözleşme/örnek config ve 38 girdilik release manifest: PASS.
- `setuptools 83.0.0` ve `wheel 0.46.2` build isolation ile wheel üretimi: PASS.
- Ayrı CPython 3.13 ortamına wheel yeniden kuruldu; `python -I -m db_change_analyzer --version` → `0.1.0`, `pip check` → bozuk gereksinim yok.
- Değişen kod/test dosyalarında `ruff --select F,B,E4,E7,E9`: PASS. Güvenlik uyarılarının gerekçeleri yukarıdaki tabloda.

## Yerel tekrar komutları

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools/check_contracts.py
.venv\Scripts\python.exe tools/check_release_manifest.py
.venv\Scripts\python.exe -m build --wheel --outdir dist
pip-audit -r requirements.lock --no-deps --disable-pip
pip-audit -r requirements-dev.lock --no-deps --disable-pip
ruff check src/db_change_analyzer tools --select S --exclude generated --statistics
```

## Kalan kabul sınırları

- Windows ortamı sembolik bağlantı oluşturma izni vermediği için gerçek symlink regresyonu bu hostta atlandı; düz yol ve sahte manifest reddi Windows'ta doğrulandı. Symlink testi izinli Windows veya Linux ortamında çalıştırılmalı.
- Bitbucket `gpu-db` şu anda `/unavailable` yanıtı veriyor; tam kaynak blob'larıyla uçtan uca analiz/teslimat kanıtı eksik. Bu denetim gerçek model gateway, SMTP relay veya Jenkins job'u tetiklemedi.
- Gelişmiş Oracle sözdizimi ve native Outlook/ekran okuyucu/%200 zoom kabulü ayrı iş kalemidir; `V5_KABUL_DURUMU.md` sınırları geçerlidir.
- Statik inceleme ve başarılı testler sıfır açık garantisi değildir. Kurum ağına ve gerçek servislere erişim açıldığında yetkili sızma testi, gateway/SMTP sözleşme testi ve işletim sistemi bazlı test koşusu gerekir.
