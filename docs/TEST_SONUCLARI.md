# Test Sonuclari

Tarih: 2026-09-28 (Europe/Istanbul)

Ortam:

- CPython 3.13.15
- Git 2.55.0.windows.3
- Java 21.0.11
- ANTLR generator/runtime 4.13.2
- grammars-v4 commit `b434a051c56dcc2a5de0a0f2d86575ed192b59da`

## Calistirilan kapilar

| Komut | Sonuc |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | PASS — 58 test, 118.71 s |
| `.venv/Scripts/python.exe tools/check_contracts.py` | PASS — 5 JSON schema + strict config example |
| `.venv/Scripts/python.exe tools/check_release_manifest.py` | PASS — 10 release girdisi SHA-256 |
| `.venv/Scripts/python.exe -m build --wheel --outdir dist` | PASS — `ai_db_change_analyzer-0.1.0-py3-none-any.whl` |
| Ayrı `--system-site-packages` venv, `pip install --no-deps <wheel>` | PASS |
| Ayrı venv, `python -m db_change_analyzer --version` | PASS — `0.1.0` |
| `pip check` | PASS — broken requirement yok |

Suite varsayılan olarak dış ağ kullanmadı. Git senaryoları yerel fixture repository/bare cache, model HTTP senaryoları `httpx.MockTransport`, SMTP senaryoları fake transport ile çalıştı. Oracle bağlantısı veya SQL execution yolu yoktur.

## Gercek corpus sonucu

Saglanan metadata ZIP'i salt okunur olarak 2.340 SQL dosyasinin tamaminda tarandi. Olculen 2.382 CREATE occurrence ve 2.380 unique top-level ObjectKey, normatif K3 beklentisi/kimligiyle eslesmedi. Arşiv SHA-256 de beklenen K3 hashinden farklidir; bu nedenle X01–X15 normatif kabul sonucu PASS yapilmadi. En buyuk 796.216-byte package worker'da 60 saniyede tamamlanmadi; `PARSER_TIMEOUT`/fallback siniri acik tutuldu. K4 yalniz uygulama/cross-check rolunde kaldı.

## Kabul ozeti

`docs/KABUL_MATRISI.md` 129 ID'nin tamamini listeler: 16 PASS, 113 NOT RUN, 0 FAIL. NOT RUN, ilgili birim kodunun yok oldugu anlamina gelmeyebilir; tam normatif A/E/K, checkpoint ve fault-injection kombinasyonunun calistirilmadigini belirtir.

Acik yetki olmadigi icin su live kapilar NOT RUN kaldı: kurum LiteLLM route/model/capability ve kalite eval'i; gercek SMTP relay kabul/ret/UNKNOWN provasi; Jenkins job kurulumu/cron; production gpu-db history/inventory; Oracle/canli DB (urun zaten Oracle'a baglanmaz). Bu sonuclar production onayi veya canli DB guvenligi iddiasi degildir.
