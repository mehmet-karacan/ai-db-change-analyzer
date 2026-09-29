# Test Sonuclari

Tarih: 2026-09-29 (Europe/Istanbul)

Ortam:

- CPython 3.13.15
- Git 2.55.0.windows.3
- Java 21.0.11
- ANTLR generator/runtime 4.13.2
- grammars-v4 commit `b434a051c56dcc2a5de0a0f2d86575ed192b59da`

## Calistirilan kapilar

| Komut | Sonuc |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | PASS — 209 test, 257.92 s; kaynak biçimi/sırası, commit/revert geçmişi, package overload, INDEX eşdeğer/çakışan tekrar tanımları ve partition/paralellik derecesi/sıkıştırma, VIEW JOIN/analitik aralık, TABLE kısmi MODIFY/sanal sütun/bileşen sınırı/çoklu DROP/fiziksel özellik ve ALTER bağlamı, boş literal ile yorum kaldırma, retry model etiketleri/süre temeli, büyük START WITH gösterimi testli |
| `.venv/Scripts/python.exe tools/check_contracts.py` | PASS — 5 JSON schema + strict config example |
| `.venv/Scripts/python.exe tools/check_release_manifest.py` | PASS — 37 release girdisi SHA-256 |
| `py -3 <onaylı paket>/tools/check_package.py` | PASS — 115 sunum/paket kontrolü, 0 hata |
| `.venv/Scripts/python.exe -m build --wheel --outdir dist` | PASS — `ai_db_change_analyzer-0.1.0-py3-none-any.whl` |
| Ayrı `--system-site-packages` venv, `pip install --no-deps <wheel>` | PASS |
| Ayrı venv, `python -m db_change_analyzer --version` | PASS — `0.1.0` |
| `pip check` | PASS — broken requirement yok |

Suite varsayılan olarak dış ağ kullanmadı. Git senaryoları yerel fixture repository/bare cache, model HTTP senaryoları `httpx.MockTransport`, SMTP senaryoları fake transport ile çalıştı. V5 testleri pinlenmiş hedefi, geçersiz AI yanıtı sonrası aynı run ile sürmeyi, retry'daki eski/yeni dönen model etiketlerini ve süreçler arası süre temelini, SMTP hatası sonrası kayıtlı MIME'ı, operatör retry/resolve checkpoint'ini, SQLite rollback'ini, V5 manifest/MIME bağını, yarım çıktı onarımını, biçim/sıra kaynak farkında modelsiz raporlamayı, INDEX/PACKAGE ham aralıklarında biçim ayrımını, INDEX partition clause/paralellik derecesi/sıkıştırma, VIEW JOIN türü/analitik aralık, TABLE kısmi MODIFY/sanal sütun/bileşen sınırı/çoklu DROP/fiziksel özellik düzeyi, aynı/ayrı dosyadaki ALTER bağlamı farkını ve `COMMENT ... IS ''` ile yorum silinmesi fact'ini, net sıfır olan commit/revert geçmişini ve PACKAGE overload imza/sınırlı coverage farkını kapsar. Oracle bağlantısı veya SQL execution yolu yoktur.

Onaylı V5 HTML referansı ile gerçek renderer'ın showcase fixture'ından ürettiği HTML'in iki CSS bloğu byte düzeyinde eşleşti (SHA-256: `f938437cbb26237b4a6ab74a038c56a3035682ef0661bd9e1dff7c0f220b7362`, `f37156c05807bb9e58ae4af1e770aa42f49c64e477dd7d7ff5591e0b605b646d`). Yerel `file://` önizlemesi tarayıcı URL politikası tarafından reddedildi; 320 px, %200 zoom, native Outlook ve ekran okuyucu görsel/etkileşim kabulü yapılmadı.

## Gercek corpus sonucu

Saglanan metadata ZIP'i salt okunur olarak 2.340 SQL dosyasinin tamaminda tarandi. Olculen 2.382 CREATE occurrence ve 2.380 unique top-level ObjectKey, normatif K3 beklentisi/kimligiyle eslesmedi. Arşiv SHA-256 de beklenen K3 hashinden farklidir; bu nedenle X01–X15 normatif kabul sonucu PASS yapilmadi. En buyuk 796.216-byte package worker'da 60 saniyede tamamlanmadi; `PARSER_TIMEOUT`/fallback siniri acik tutuldu. K4 yalniz uygulama/cross-check rolunde kaldı.

## Kabul ozeti

`docs/KABUL_MATRISI.md` ilk sürümün 129 ID'sini listeler ve V5 kapsamına göre henüz yeniden sınıflandırılmamıştır. V5 gruplarının yerel ve bekleyen kabul ayrımı `docs/V5_KABUL_DURUMU.md` içindedir. V5 yerel otomatik testleri tüm normatif K3 corpus, model kalite, gateway ve istemci render kabulünün yerine geçmez.

Acik yetki olmadigi icin su live kapilar NOT RUN kaldı: kurum LiteLLM route/model/capability ve kalite eval'i; gercek SMTP relay kabul/ret/UNKNOWN provasi; Jenkins job kurulumu/cron; production gpu-db history/inventory; Oracle/canli DB (urun zaten Oracle'a baglanmaz). Bu sonuclar production onayi veya canli DB guvenligi iddiasi degildir.
