# AI Database Change Analyzer

Oracle DDL snapshot’larının sabit Git revision’ları arasındaki değişikliklerini kaynak kanıtından ayırmadan analiz eden, tek süreçli CPython 3.13 CLI ürünüdür. Ürün kaynak repository’ye yazmaz, Oracle SQL çalıştırmaz ve mevcut DB Sync pipeline’ını değiştirmez.

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

Doğrulama:

```text
python -m pytest -q
python tools/check_contracts.py
python tools/check_release_manifest.py
python -m build --wheel
```
