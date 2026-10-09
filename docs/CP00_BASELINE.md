# CP00 baseline kaydı

- Tarih: 7 Ekim 2026, Europe/Istanbul
- Başlangıç HEAD: `ff876e2941cbe8bb5e43bf9f9b72e0c73c0a18eb`
- Dal: `main`
- Remote: `https://github.com/mehmet-karacan/ai-db-change-analyzer`
- Onaylı HTML SHA-256: `2bb904b06deaf632d19ffd5b62c6dbb427a16196c455eb763cedc0009a00e9d2`
- Onaylı HTML boyutu: `57197` byte
- Kullanıcı çalışma ağacı dosyası: `.ignore` (commit kapsamına alınmadı)

## Kontroller

| Kontrol | Sonuç |
|---|---|
| `python -m pytest -q -m "not live"` | Ortam bağımlılığı nedeniyle NOT_RUN: global Python'da paket importu yok |
| `.venv\Scripts\python.exe -m pytest -q -m "not live"` | PASS: 221 passed, 1 skipped; skip Windows symlink |
| `python tools/check_contracts.py` | PASS: 5 şema, örnek config |
| `python tools/check_release_manifest.py` | PASS: 38 dosya |
| `python -m build --wheel` | PASS: `ai_db_change_analyzer-0.1.0-py3-none-any.whl` |
| Gerçek model/SMTP/Oracle/Jenkins/Outlook | NOT_RUN / NOT_VERIFIED |

Baseline sırasında dış servise çağrı yapılmadı. Onaylı HTML yalnız görsel
referans olarak hash'lendi; örnek içerik runtime analiz girdisine alınmadı.

