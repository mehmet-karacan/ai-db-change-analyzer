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
| `.venv/Scripts/python.exe -m pytest -q` | Önceki tam koşu PASS — 210 test, 444.99 s; model-birim eşleme değişikliğinden önce |
| Önceki 28 `tests/test_*.py` dosyası dört ayrı `pytest -q` grubunda | PASS — 211 test; coverage düzeltmesinden önce |
| Güncel 28 `tests/test_*.py` dosyası dört ayrı `pytest -q` grubunda, her dosya bir kez | PASS — 58 + 84 + 38 + 32 = 212 test; `pytest --collect-only -q` aynı 212 testi topladı. Kaynak biçimi/sırası, commit/revert geçmişi, package overload, INDEX partition/paralellik/sıkıştırma, VIEW JOIN/analitik, TABLE kısmi MODIFY/sanal sütun/ALTER, retry model birimi etiketleri/süre temeli, kaynak coverage düzeltmesi ve Windows manifest taşınabilirliği dâhil |
| Coverage düzeltmesi sonrası `pytest -q tests/test_oracle_changes.py tests/test_workflow_conflicts.py tests/test_v5_adapter.py` | PASS — 67 hedefli test |
| Yerel renderer, AI, SMTP ve state test grubu | PASS — 42 test; sentetik V5 HTML static denetiminde `lang=tr`, 10 veri tablosunda `scope` başlıkları, görünür önceki/yeni etiketleri, 0 script/form/iframe/SVG ve 0 dış asset |
| `.venv/Scripts/python.exe -m pytest -q tests/test_v5_adapter.py tests/test_v5_rendering.py` | PASS — 26 hedefli test; iki AI biriminin farklı dönen model etiketleri eşlemesi doğrulandı |
| `.venv/Scripts/python.exe tools/check_contracts.py` | PASS — 5 JSON schema + strict config example |
| `.venv/Scripts/python.exe tools/check_release_manifest.py` | PASS — 38 release girdisi SHA-256; `.gitattributes` ile Windows checkout satır sonları sabit |
| `.venv/Scripts/python.exe -m pytest -q tests/test_release_manifest_portability.py` | PASS — 1 test; temiz index checkout üzerinde 38 girdilik manifest kontrolü PASS |
| Windows `core.autocrlf=true` ile temiz clone, `tools/check_release_manifest.py` | PASS — 38 dosya doğrulandı |
| `py -3 <onaylı paket>/tools/check_package.py` | PASS — 115 sunum/paket kontrolü, 0 hata |
| `.venv/Scripts/python.exe -m build --wheel --outdir dist` | PASS — son kaynak coverage düzeltmesini içeren `ai_db_change_analyzer-0.1.0-py3-none-any.whl` yeniden üretildi |
| Ayrı CPython 3.13 venv, `pip install --no-deps <wheel>` ve `pip install --require-hashes -r requirements.lock` | PASS |
| Ayrı venv, son wheel `--force-reinstall --no-deps`, `python -I -m db_change_analyzer --version` | PASS — `0.1.0` |
| Ayrı venv, `pip check` | PASS — broken requirement yok |

Suite varsayılan olarak dış ağ kullanmadı. Git senaryoları yerel fixture repository/bare cache, model HTTP senaryoları `httpx.MockTransport`, SMTP senaryoları fake transport ile çalıştı. V5 testleri pinlenmiş hedefi, geçersiz AI yanıtı sonrası aynı run ile sürmeyi, retry'daki eski/yeni dönen model etiketlerini ve süreçler arası süre temelini, SMTP hatası sonrası kayıtlı MIME'ı, operatör retry/resolve checkpoint'ini, SQLite rollback'ini, V5 manifest/MIME bağını, yarım çıktı onarımını, biçim/sıra kaynak farkında modelsiz raporlamayı, INDEX/PACKAGE ham aralıklarında biçim ayrımını, INDEX partition clause/paralellik derecesi/sıkıştırma, VIEW JOIN türü/analitik aralık, TABLE kısmi MODIFY/sanal sütun/bileşen sınırı/çoklu DROP/fiziksel özellik düzeyi, aynı/ayrı dosyadaki ALTER bağlamı farkını ve `COMMENT ... IS ''` ile yorum silinmesi fact'ini, net sıfır olan commit/revert geçmişini ve PACKAGE overload imza/sınırlı coverage farkını kapsar. Oracle bağlantısı veya SQL execution yolu yoktur.

Onaylı V5 HTML referansı ile gerçek renderer'ın showcase fixture'ından ürettiği HTML'in iki CSS bloğu byte düzeyinde eşleşti (SHA-256: `f938437cbb26237b4a6ab74a038c56a3035682ef0661bd9e1dff7c0f220b7362`, `f37156c05807bb9e58ae4af1e770aa42f49c64e477dd7d7ff5591e0b605b646d`). Uygulamanın ürettiği 69.916-byte showcase HTML'i localhost üzerinden Codex in-app browser'da 320, 390, 600 ve 900 px viewport ile ayrıca incelendi. Dört genişlikte document scrollWidth sırasıyla 305, 375, 585 ve 885 px; viewport genişliğini aşan tablo ve hedefi bulunmayan iç anchor sayısı sıfırdı. 320 px'te önceki/yeni etiketleri görünür HTML metniydi; uzun, boşluksuz nesne adı ve 180 karakterlik değerle üretilen 70.485-byte stress görünümünde yatay taşma veya `overflow: hidden/clip` ile kesilen metin görülmedi. Mobil metinlerin ölçülen en küçük fontu 13 px idi. Bu yerel tarayıcı ölçümü native e-posta istemcisi kabulü değildir. %200 tarayıcı yakınlaştırması, native Outlook, ekran okuyucu ve insan kullanıcısı görsel/etkileşim kabulü yapılmadı.

## Gercek corpus sonucu

Saglanan metadata ZIP'i salt okunur olarak 2.340 SQL dosyasinin tamaminda tarandi. Olculen 2.382 CREATE occurrence ve 2.380 unique top-level ObjectKey, normatif K3 beklentisi/kimligiyle eslesmedi. Arşiv SHA-256 de beklenen K3 hashinden farklidir; bu nedenle X01–X15 normatif kabul sonucu PASS yapilmadi. En buyuk 796.216-byte package worker'da 60 saniyede tamamlanmadi; `PARSER_TIMEOUT`/fallback siniri acik tutuldu. K4 yalniz uygulama/cross-check rolunde kaldı.

Kaynak `gpu-db` Git repository'sinin `master` geçmişi ayrıca salt okunur incelendi. Tam commitler `15f2e810e5ee288b2344b79ce3c3f58006d0bf38` (base) ve `40b75d1fc0e17fe51355dd55e2e607add2fb3345` (target). Her iki Git ağacında 57 blob/dosya var; 56 blob byte-eşit, yalnız `gpu_user/sequences.oracle.sql` değişmiş; eklenen/silinen dosya yok. Değişen 6 satırın tamamı `CREATE SEQUENCE` tanımıdır ve her birinde yalnız `START WITH` sayısı farklıdır; sayı maskelendikten sonra satırların kalan byte'ları eşit. Bu Git kanıtı, ZIP arşivi hash'lerinin eşleştiği veya Oracle'da DDL çalıştığı anlamına gelmez.

Aynı değişen Git dosyasının iki blob'u ürünün `inventory_bytes` parser ve `compare_projections` çıkarıcısından geçirildi: her tarafta 552 sequence tanımı `parsed`, 0 parser tanısı, 0 eklenen/çıkarılan nesne, tam 6 değişen nesne ve 6 `sequence.sequence_property.start_with` fact'i. Ortak kaynak/presence ailelerinin yanlış `unsupported_families` sayılması düzeltildikten sonra bu altı nesnenin alan kapsamı `limited` değil. Yerel, ayrı state üzerinde `doctor --offline` ağ/model/SMTP çağrısı yapmadı; ilk `run --offline` base SHA'yı baseline yaptı, aynı tetiklemenin `run --dry-run --offline` tekrarı target SHA için 1 commit ve 1 kapsam dosyası planladı, checkpoint'i ilerletmedi ve 0 AI/SMTP girişimi yaptı. Bu dry-run tam analiz/teslimat değildir.

## Kabul ozeti

`docs/KABUL_MATRISI.md` ilk sürümün 129 ID'sini listeler ve V5 kapsamına göre henüz yeniden sınıflandırılmamıştır. V5 gruplarının yerel ve bekleyen kabul ayrımı `docs/V5_KABUL_DURUMU.md` içindedir. V5 yerel otomatik testleri tüm normatif K3 corpus, model kalite, gateway ve istemci render kabulünün yerine geçmez.

Acik yetki veya kurum ortamı olmadığı icin su live kapilar NOT RUN kaldı: kurum LiteLLM route/model/capability ve kalite eval'i; gercek SMTP relay kabul/ret/UNKNOWN provasi; Jenkins job/cron ortamı; production gpu-db tam history/inventory analizi; Oracle/canli DB (urun zaten Oracle'a baglanmaz). İki sabit gpu-db Git revizyonunun salt okunur byte farkı yukarıda doğrulandı. Kullanıcının ayrı isteği üzerine Analyzer Jenkinsfile etkisizleştirildi ve commit'ler `origin/main` dalına gönderildi. Bu sonuclar production onayi veya canli DB guvenligi iddiasi degildir.
