# AI Database Change Analyzer

Oracle DDL snapshot’larının sabit Git revision’ları arasındaki değişikliklerini kaynak kanıtından ayırmadan analiz eden, Python 3.10+ CLI ürünüdür. Ürün kaynak DDL repository’sine yazmaz, Oracle SQL çalıştırmaz ve mevcut DB Sync pipeline’ını değiştirmez. Açıkça etkinleştirilen arşiv ayarı, çıktıyı ayrı bir uygulama repository checkout’unda kalıcı kayıt olarak tutabilir; commit/push işlemi Analyzer dışında yürütülür.

Repository'deki Analyzer `Jenkinsfile`, `10-ORACLE_DB_DDL_SYNC` job'ının başarılı tamamlanmasından sonra çalışacak artifact pipeline'ını tanımlar. Pipeline yalnız `gpu-db` Git geçmişini analiz eder, canlı Oracle'a bağlanmaz; doğrulanmış HTML/JSON/text artifact'lerini Jenkins'e arşivletir ve doğrulanmış HTML raporunu `mkaracan@innova.com.tr` adresine mevcut `emailext` mekanizmasıyla gönderir. `BASELINED` ve `NO_CHANGE` sonuçlarında mail gönderilmez. Kaynak kod, GPU artifact config'i ve Python 3.10 uyumlu Linux wheelhouse doğrudan bu repository checkout'undan okunur; Jenkins agent'ta yalnız genel `python3` ve Git bulunması gerekir, pip, venv veya internet gerekmez. Wheel'ler `.site` klasörüne doğrudan açılarak kullanılır. Job, upstream tetikleyicisinin yanında Jenkins'teki **Build with Parameters** ile manuel çalıştırılabilir; ilk manuel çalıştırma baseline oluşturur, yeni commit varsa analiz üretir. Kurumdaki mevcut `09-AI-DB-CHANGE-ANALYZER` job'ı bu dosya pushlandıktan ve model credential'ı tanımlandıktan sonra çalıştırılmalıdır. `docs/Jenkinsfile.txt` ayrı DB Sync referansıdır ve değiştirilmemiştir.

Yerel güvenlik ve kalite denetimi: `docs/GUVENLIK_KALITE_DENETIMI.md`.

Geliştirme kurulumu:

```text
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
PYTHONPATH=src .venv/bin/python -m db_change_analyzer --config config/gpu.example.toml doctor --offline
```

Örnek config bilinçli olarak canlı model capability doğrulamasını kapalı ve SMTP kurulum alanlarını placeholder bırakır. Gerçek credential dosyaya yazılmaz; yalnız allowlist environment değişkenlerinden çalışma anında okunur.

Yerel corpus hazırlama ve güvenlik sınırları için `docs/CORPUS_DOGRULAMA.md`, işletim akışları için `docs/OPERASYON.md` kullanılır.

Source-review değerlendirmesi aynı sentetik corpus üzerinde mock sözleşme/rubrik
koşusu ile yapılır; mock sonuç model kalite kabulü değildir. Politika sürümü
karşılaştırması için iki policy kökü aynı corpus'a bağlanabilir:

```text
python tools/evaluate_source_review.py --config config/gpu.artifact.example.toml --corpus tests/fixtures/review_eval/corpus.json --output eval.json --mode mock
python tools/compare_source_review_eval.py --config config/gpu.artifact.example.toml --corpus tests/fixtures/review_eval/corpus.json --output eval-compare.json --baseline-policy-root src/db_change_analyzer --candidate-policy-root src/db_change_analyzer --mode mock
```

Her çalışma policy dosyalarının sürüm/hash bilgisini taşır; kabul edilen model
yorumları yalnız kabul edilmiş execution kaydının kimliğiyle ilişkilendirilir.
Gerçek route değerlendirmesi ayrıca `--allow-ai` ve doğrulanmış capability
kaydı gerektirir. İnsan değerlendirme alanları `docs/GERCEK_MODEL_DEGERLENDIRME_FORMU.md` içindedir.

Temel offline akış:

```text
db-change-analyzer --config gpu.toml state init --confirm-new-install
db-change-analyzer --config gpu.toml doctor --offline
db-change-analyzer --config gpu.toml inventory --target <FULL_SHA> --offline
db-change-analyzer --config gpu.toml run --dry-run --offline
```

`run --allow-ai --allow-mail` yalnız verified model profile ve hazırlanmış SMTP config ile açılır. `manual` otomatik checkpoint'i değiştirmez; `notification resend` yeni AI analizi yapmaz. CLI stdout'ta tek satır `ResultRecord` JSON, stderr'de kaynak/secret içermeyen JSONL işletim olayları üretir.

Yeni raporlar canonical `report/2.0` kaydına bağlı `mail-view/3.0` ve `render-manifest/2.0` yan kayıtlarıyla onaylı İnnova HTML/plain text/MIME üretir; eski `report/1.0`, `mail-view/2.0` ve V5 kayıtları okunabilir kalır. Yapısal diff kapsamı tablo, index, sequence, view, package, trigger, procedure, function ve type kaynaklarını kapsayacak şekilde genişletilmiştir; doğrulanamayan özellik aileleri görünümde sınırlı kapsam olarak işaretlenir. Repository içi statik bağımlılıklar, nesne bazlı deterministik risk rozetleri ve çalıştırılmamış deployment ön kontrol planı canonical rapora yazılır. Model yalnız temizlenmiş `mail-unit-input/1.0` alır; `mail-commentary/1.1` yanıtındaki serbest yorumlar kanıtlı deterministik kabul kapısından geçmedikçe mailde gösterilmez. Önceden hazırlanmış bildirim tekrar denendiğinde kayıtlı MIME aynen kullanılır.

Ürün kapsamı ve kalıcı uygulama repository arşivi `docs/DB_CHANGE_ANALYZER_KAPSAM.md` içinde tanımlıdır. Arşiv isteğe bağlıdır: `reports.archive_enabled = true` ve `reports.archive_root` ile etkinleştirilir; raporlar uygulama checkout’unda `db-change-analyzer/reports/<year>/<date>/...` altında immutable kayıt olarak tutulur. Otomatik akış aynı `repository + branch + base_sha + target_sha` aralığı daha önce arşivlenmişse yeni change analizi başlatmaz; aynı gün içindeki farklı aralıklar ayrı raporlanır. Bu yol Jenkins tetikleme kapsamından çıkarılmalıdır.

Mevcut schema v1 state için kapsam kilidi altında ve yedek alarak `db-change-analyzer --config gpu.toml state migrate-v5` çalıştırılır. Eski rapor ve pending bildirim byte'ları korunur. `docs/V5_UYGULAMA_DURUMU.md` uygulama kapsamını ve henüz yapılmamış kurum kabul adımlarını listeler.

Doğrulama:

```text
python -m pytest -q
python tools/check_contracts.py
python tools/check_release_manifest.py
python -m build --wheel
python tools/final_audit.py %TEMP%\ai-db-change-analyzer-final-audit.json
```
