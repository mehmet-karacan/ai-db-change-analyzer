# AI Database Change Analyzer

Oracle DDL snapshot’larının sabit Git revision’ları arasındaki değişikliklerini kaynak kanıtından ayırmadan analiz eden, tek süreçli CPython 3.13 CLI ürünüdür. Ürün kaynak repository’ye yazmaz, Oracle SQL çalıştırmaz ve mevcut DB Sync pipeline’ını değiştirmez.

Repository'deki Analyzer `Jenkinsfile` yerel kabul boyunca no-op pipeline'dır. Kurumdaki Jenkins job'u korunur; yerel doğrulama tamamlandığında yalnız Analyzer CLI tetikleyicisi olarak düzenlenecektir. Tetiklense bile mevcut no-op dosya analiz, model ve e-posta aşamalarını çalıştırmaz. `docs/Jenkinsfile.txt` ayrı DB Sync referansıdır ve değiştirilmemiştir.

Geliştirme kurulumu:

```text
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
PYTHONPATH=src .venv/bin/python -m db_change_analyzer --config config/gpu.example.toml doctor --offline
```

Örnek config bilinçli olarak canlı model capability doğrulamasını kapalı ve SMTP kurulum alanlarını placeholder bırakır. Gerçek credential dosyaya yazılmaz; yalnız allowlist environment değişkenlerinden çalışma anında okunur.

Yerel corpus hazırlama ve güvenlik sınırları için `docs/CORPUS_DOGRULAMA.md`, işletim akışları için `docs/OPERASYON.md` kullanılır.

Temel offline akış:

```text
db-change-analyzer --config gpu.toml state init --confirm-new-install
db-change-analyzer --config gpu.toml doctor --offline
db-change-analyzer --config gpu.toml inventory --target <FULL_SHA> --offline
db-change-analyzer --config gpu.toml run --dry-run --offline
```

`run --allow-ai --allow-mail` yalnız verified model profile ve hazırlanmış SMTP config ile açılır. `manual` otomatik checkpoint'i değiştirmez; `notification resend` yeni AI analizi yapmaz. CLI stdout'ta tek satır `ResultRecord` JSON, stderr'de kaynak/secret içermeyen JSONL işletim olayları üretir.

Yeni raporlar canonical `report/1.0` kaydına bağlı `mail-view/2.0` ve `render-manifest/1.0` yan kayıtlarıyla V5 HTML/plain text/MIME üretir. Altı desteklenen Oracle nesne türünde doğrulanamayan özellik aileleri görünümde sınırlı kapsam olarak işaretlenir. Model yalnız temizlenmiş `mail-unit-input/1.0` alır; `mail-commentary/1.1` yanıtındaki serbest yorumlar kanıtlı deterministik kabul kapısından geçmedikçe mailde gösterilmez. Önceden hazırlanmış bildirim tekrar denendiğinde kayıtlı MIME aynen kullanılır.

Mevcut schema v1 state için kapsam kilidi altında ve yedek alarak `db-change-analyzer --config gpu.toml state migrate-v5` çalıştırılır. Eski rapor ve pending bildirim byte'ları korunur. `docs/V5_UYGULAMA_DURUMU.md` uygulama kapsamını ve henüz yapılmamış kurum kabul adımlarını listeler.

Doğrulama:

```text
python -m pytest -q
python tools/check_contracts.py
python tools/check_release_manifest.py
python -m build --wheel
```
