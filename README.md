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
