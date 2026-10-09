# Araştırmalı analiz test sonuçları

Bu kayıt 8 Ekim 2026 tarihinde `ai-db-change-analyzer` çalışma dizininde alınmıştır. Testler yerel fixture, mock model/SMTP ve geçici Git/SQLite dizinleriyle çalıştırılmıştır. Canlı Oracle, kurumsal model route'u, Jenkins veya Outlook kullanılmamıştır.

## Gerçekleştirilen kontroller

| Kontrol | Sonuç |
|---|---|
| Core offline pytest suite (`-m "not scale"`) | `317 passed, 1 skipped, 2 deselected` (son tam koşu) |
| Gerçek workflow scale suite (`41/100`, `scale`) | `2 passed` (`41` ve `100`) |
| Atlanan test | `tests/test_retention.py:58`; Windows ortamında symlink desteği yok |
| Hedefli renderer/adapter/mail-commentary seti | `50 passed` |
| Son risk/attribution düzeltmesi sonrası hedefli analiz/adapter/renderer seti | `45 passed` |
| Source-review seçili CLI e2e seti | `4 passed` |
| Dynamic SQL/package/change/catalog/adapter seti | `75 passed` |
| Source-review mock evaluation harness | `20/20 rubric_pass`, status `MOCK_ONLY`; model quality PASS değil |
| Contract checker | `7 schemas PASS` |
| Release manifest checker | `72 files PASS` |
| Wheel build | `PASS` |
| `git diff --check` | `PASS`; yalnız Git'in LF/CRLF uyarıları mevcut |

Tam suite komutu:

```text
.venv\Scripts\python.exe -m pytest -q -m "not scale"
.venv\Scripts\python.exe -m pytest -q -m scale
```

## Bu turda eklenen kanıtlar

- Accepted model execution kaydı standalone HTML ve CID e-posta HTML'inde aynı yorumla ilişkilendiriliyor.
- Policy sürüm/hash, route/model, zaman, attempt ve execution id alanları view sözleşmesine bağlanıyor.
- Retry sonrası `ai_http_attempts` toplamı ile yeni execution attempt numarası birbirine karıştırılmıyor.
- Artifact consumer doğru `report-email.html` hash'ini kabul ediyor; bozuk e-posta hash'ini reddediyor.
- SMTP bölümü olmayan `config/gpu.artifact.example.toml` ile gerçek CLI artifact e2e akışı çalışıyor; `result.json` V2, `smtp_attempts=0` ve notification satırı yok.
- Mevcut `emailext` için yeni job/sender oluşturmayan Groovy tüketici parçası `jenkins/consume_report_artifact.groovy` içine alındı.
- Yeni arşiv manifestleri `db-change-archive/2.0` olarak yazılıyor; `db-change-archive/1.0` kayıtları range aramasında okunabilir kalıyor.
- Aynı run içindeki yeni analiz generation'ı eski `units` kayıtlarını yeniden kullanmıyor; generation izolasyonu state testi ve stateful e2e ile doğrulandı.
- V2 source-review aktif yolu Git fixture'ından `get_diff`, `read_source`, `find_references` ve `get_history` receipt'leri topluyor; model yanıtını source-review/1.0 sözleşmesiyle kabul ediyor, execution atfını uygulama ekliyor ve canonical rapora yazıyor. Added/removed ve package-body body-only nesne e2e'leri geçti.
- `{{ ... }}` ve `$BUILD_URL` gibi metinler Jinja/email-ext macro'su olarak işletilmiyor.
- DDL Sync'in geçici Jenkins TSV dosyalarının Analyzer kanıtı olmadığı ve gerçek kanıtın `gpu-db` Git snapshot'ları olduğu belgeleniyor.
- Source-review contract testi finding başlık/detay/verification metnini de güvenli biçimde doğruluyor; accepted finding canonical rapor ve mail-view içinde uygulama execution atfıyla görünüyor.
- `tools/evaluate_source_review.py`, `tests/fixtures/review_eval/corpus.json` ile sentetik Git snapshot'larını ve `get_diff`, `read_source`, `find_references`, `get_history` receipt'lerini çalıştırıyor. Mock sonucu kalite PASS'ı olarak raporlanmıyor.
- `tools/compare_source_review_eval.py` aynı corpus'u iki policy köküyle çalıştırıp corpus SHA'sını, policy fingerprint'lerini, case bazlı rubrik farklarını ve geçen case delta'sını makine okunur JSON'a yazar. Aynı policy köküyle tek sentetik case smoke koşusu `MOCK_ONLY`, `changed_cases=0`, `passed_case_delta=0` verdi.
- CP04 local LiteLLM tool loop'u iki tool call devamını ve final JSON schema geçişini mock'ta kabul ediyor; capability record digest/route bağlanıyor ve negatif envelope durumları reddediliyor. `smoke-model` tool probe sonucunu capability kaydına yazacak şekilde güncellendi. Source-review workflow'ü loop varsa kapalı read-only tool surface'ini kullanıp yeni receipt'leri canonical rapora ekliyor; eski test double'ları `complete()` ile geriye uyumlu kalıyor.
- CP06 source-review journal'ı scope/run/generation altında atomik hash-zincirli `tool_receipt` ve `unit_status` kayıtları üretiyor. Deduplication, torn/hash/index bozulması ve raw secret payload yazmama test edildi. `receipt_days` retention yalnız pasif ve tamamlanmış generation dizinini açık apply manifestiyle temizliyor.
- `report emit --report-id ... --expected-report-sha256 ...` yalnız persisted report/sidecar/MIME hash'lerini doğrulayıp yeniden yazıyor; artifact e2e'de replay çıktısının altı rapor dosyasıyla byte eşitliği doğrulandı.
- `max_new_units_per_invocation` için yerel bütçe/resume e2e'si ilk çağrıda 2, ikinci çağrıda kalan 1 birimi işledi ve final raporda 3 nesneyi korudu. Source-review execution attribution artık persisted ham model yanıtını mutate etmiyor; resume strict contract doğrulaması geçti.
- Resume fingerprint'i bozuk veya geçersiz şekle dönüştüğünde güvenli state sonucu üretiliyor; analiz fingerprint'i değişirse aynı run yeni politika/model/şema bağlamıyla sessizce karıştırılmıyor.
- Aktif canonical rapor artık `report/2.0`; eski `report/1.0` kayıtları okunabilir. Gün arşivi gate'i `analysis_fingerprint` eşleşmesini doğruluyor; eski politika/model/şema arşivi yeni analiz için cache hit sayılmıyor.
- İlk model çağrısı ve tool sonucu yazıldıktan sonra simüle edilen process crash senaryolarında `ANALYZING` run checkpoint'i ilerletmeden kaldı; sonraki invocation aynı run kimliğiyle artifact'i tamamladı ve tool receipt'i journal'da korudu. Ölçek renderer senaryoları 0/1/40/41/100/120/1000 kayıtlarını kapsıyor.
- Validation failure sonrası birim `INVALID` ve run `ANALYZING` kaldı; sonraki invocation aynı run'ı iki başarısız HTTP denemesini yeniden çoğaltmadan tamamladı. Render process crash sonrasında doğrulanmış unit yeniden model çağrısı yapılmadan kullanıldı.
- Final validation tamamlandıktan sonra canonical report üretimindeki process crash sonrasında doğrulanmış unit yeniden model çağrısı yapılmadan artifact'e dönüştü.
- Source-review input digest'i runtime'a ait `elapsed_ms` değerini kimlikten çıkarıyor; receipt payload'ında süre korunurken resume request digest'i değişmiyor.
- Gerçek source-review workflow'ü 41 ve 100 nesnede `max_new_units_per_invocation=40` ile kalan unit'leri kaybetmeden tamamlandı; 100 nesne final view'ında AI execution/model unit listeleri schema sınırına takılınca ilgili sınırlar 100000'e çıkarıldı ve tekrar geçti.
- `search_sources` için gerçek iki sayfalı cursor akışı üç sentetik kaynakta doğrulandı; ilk sayfa `complete=false`, devam sayfası `complete=true` ve toplam üç kanıt korundu. Cursor başka sorguya taşınamadı.
- Source-review açıklama ve bulgu metinlerinde HTML, Jinja (`{{ }}`), Groovy code fence ve email-ext `$BUILD_URL` tokenları reddediliyor; Innova renderer'da görünür veri escaping'i ayrıca korunuyor.
- Onaylı Innova logosu yoksa renderer `APPROVED_LOGO_MISSING` ile duruyor; eski/demo logo fallback'i üretmiyor.
- Validation failure sonrası kabul edilen devam yanıtının model ve `author_execution_id` bağları doğrulanıyor; başarısız ilk model son yoruma taşınmıyor.
- Dinamik SQL için sabit literal, bind'lı sabit hedef, parçalı runtime hedef, visible `DBMS_ASSERT` validation ve loop bağlamı profilleri parser → change fact → mail-view akışında doğrulandı; ham `EXECUTE IMMEDIATE` metni gösterime taşınmıyor.
- Silinen nesne source-review akışında base ve target revision'larının ayrı `find_references` receipt'leri ve base `get_history` receipt'i doğrulandı; target'ta kalan consumer kaydı korunuyor. Qualified package/routine çağrıları overload bağlama iddiası olmadan `CALL` candidate kenarlarına taşınıyor ve reverse impact context'e alınıyor. Tekil schema-qualified standalone procedure/function çağrısı statik hedefe bağlanıyor; bounded signature parse ile arity/name uyumu sınıflandırılıyor, gerçek expression type conversion ve runtime binding iddia edilmiyor.
- Package call candidate'larında positional/named/default arity ve overload belirsizliği kaynak şekliyle sınıflandırılıyor; expression type conversion veya runtime overload seçimi kesinleştirilmiyor.
- Local package/standalone çağrılarında düzeltilmiş caller'ın yalnızca geçmişteki kullanım olarak tutulduğu ve target revision'da yeni bağımlılık gibi gösterilmediği ayrı graph testiyle doğrulandı.
- Birden fazla kaldırılan nesnenin paylaştığı snapshot kanıtı registry'de tekilleştirildi; repaired caller history-only source-review CLI e2e'si artifact üretimiyle geçti.
- Registry tekilleştirmesi sonrası önceki tam offline core suite `317 passed, 1 skipped, 2 deselected` verdi; atlanan tek test Windows symlink desteği olmayan retention senaryosudur. Son legacy attribution düzeltmesi için ilgili CLI e2e `1 passed` ve kısa hedefli grup `45 passed` verdi.

## Kapsam sınırı

Bu sonuçlar CP06/CP08 yerel regresyon kanıtıdır. T01–T32 ve H01–H16 durumlarının ayrıntılı PASS/KISMİ/AÇIK karşılığı [`docs/CP08_TEST_MATRISI.md`](CP08_TEST_MATRISI.md) içindedir; CP08 bu matrisin bütün satırları PASS olmadan tamamlanmış sayılmaz. Journal zinciri, receipt deduplication, retention, replay emit, fingerprint bağlı archive gate'i, model/tool-result process-crash resume, validation failure resume, render process-crash resume, final-validation sonrası crash resume, archive-failure artifact resume, 41/100 invocation bütçesi ve 100 nesne mail-view ölçeği yerel olarak doğrulandı. Gerçek capability probe'u, gerçek Jenkins artifact tüketimi ve klasik Outlook görünümü açık iş/ortam kapısıdır. Evaluation harness ve tool-loop mock sonucu sözleşme/protokol kanıtıdır; kurum model kalitesi sonucu değildir. Bu nedenle `AKTIF_GOREV.md` içindeki CP04 ve CP08 durumları kısmi yerel olarak tutulur.

Symlink atlaması dışında bu çalıştırmada başarısız test yoktur. Gerçek dış ortam kanıtı olmadan model kalitesi, Oracle kapsamı, Jenkins teslimi veya Outlook uyumluluğu hakkında PASS sonucu çıkarılamaz.
- Gerçek `gpu-db` performans ölçümünde `gpu_user/views.oracle.sql` (123 view, 205.155 byte) birleşik parse + fragment retry ile 120,3 saniyede yine fallback'e düştü. Bu nedenle 32 nesne üstü birleşik export'larda pahalı retry kapatıldı; aynı blob ve parser ayarı için process içi ve scope SQLite SHA-256 cache eklendi. İlk 10 view fragment'ı tek worker'da 39,24 saniye, aynı batch cache'ten 0,00 saniye; 4.848-byte package ilk parse 23,06 saniye, tekrar parse 0,00 saniye ölçüldü.
- 41 nesneli yerel source-review profiler'ı optimizasyon öncesi yaklaşık 298,1 saniyeden sonra 104,3 saniyeye indi; base/target ilk inventory 79,2 saniye, aynı inventory tekrarları 2,05 saniye, dört `_index` çağrısı 0,68 saniye, 41 mock model çağrısı 1,42 saniye ve response validation 7,15 ms ölçüldü. Ayrıntılı kayıt `docs/PERFORMANS_PROFILI.md` ve tekrar üretim aracı `tools/profile_performance.py` içindedir.
- T03 kapsamı genişletildi: birden fazla caller için old/new reverse context ayrımı ve aynı dosyada repaired caller e2e'si geçti.
- T05/T06 için evaluation corpus'una exception handler yokluğu ve dış transaction varsayımında `COMMIT` eklenmesi sahneleri eklendi; mock rubric iki tekrarda 8/8 kabul verdi. Bu sonuç gerçek model kalitesi veya Oracle runtime davranışı kabulü değildir.
- T04 için varsayılan parametreyle uyumlu imza genişlemesi ve varsayılanı olmayan parametreyle arity uyumsuzluğu corpus sahneleri eklendi; named/positional çağrı kontrolü kaynak kanıtlı rubric'e bağlandı.
- T13 temiz sahne için `format-only-clean` evaluation case'i ve source-review no-finding e2e akışı PASS; canlı model kalite ölçümü ayrı NOT_RUN.
- T21/T22 için sabit bind'li ve runtime hedefli dynamic SQL, sequence başlangıç ve numeric precision değişikliği corpus sahneleri eklendi; runtime performans/exploit ve canlı veri davranışı hâlâ NOT_RUN.
- T25 genişletildi: geçerli ilerleme kaydetmiş pending analysis sırasında remote branch yeni commit'e ilerlediğinde resume `PINNED_RUN_CONFLICT/PINNED_RUN_CONTEXT_CHANGED` ile duruyor; persisted report ve INVALID unit retry akışları pinli target'ı koruyor.
- T31 genişletildi: onaylı Innova template dosyaları eksikse renderer `APPROVED_TEMPLATE_MISSING` ile fail-closed oluyor; wheel artık `templates/innova_v1/report_email.html.j2`, text template'i ve onaylı logoyu içeriyor. Paketleme testi `2 passed`.
- H02 genişletildi: aynı mail view içinde unknown ve history-only nesneler açık durum metinleriyle render edildi; native Outlook görsel kabulü ayrı açık.
- Önceki ara değişiklikten sonra non-scale offline suite `310 passed, 1 skipped, 2 deselected` ile tamamlandı; bu ara kayıt son tam koşu olan `317 passed` sonucundan önceki durumdur.
- Canonical `impact_analysis` ve `risk_assessment` mail-view sözleşmesine bounded alanlar olarak eklendi. Innova mailinde kaynak tabanlı risk sınıfı, repository içi etki adayları ve Oracle/uygulama/Jenkins/runtime bağlantı durumları gösteriliyor; operation renklerinden ayrı `critical/high/medium/low/unknown` paleti renderer testleriyle doğrulandı. Her nesneye risk rozeti bağlandı; silinen veya çözümlenemeyen nesneler canlı kullanım bilgisi olmadan `BİLGİ`/unknown olarak gösteriliyor. Minimal profil 18 KB sınırında bu bölümü kısaltıyor.
- Bu değişiklikten sonra hedefli renderer/adapter/mail-commentary grubu `50 passed`, CLI e2e `21 passed`; non-scale offline suite `317 passed, 1 skipped, 2 deselected` ile tamamlandı. Release manifest `72` dosya ve contract checker `7 schema` sonucu bu ara snapshot'a aittir.
- Güncel şablon bölüm sırası `01 / YÖNETİCİ ÖZETİ`, `02 / DEĞİŞİKLİKLER`, `03 / ETKİ VE KONTROL`, `04 / KAPSAM` olarak doğrulandı; nesne bazlı `RİSK DÜZEYİ` rozetleri ve yüksek/bilgi ayrımı korunuyor. Native klasik Outlook kabulü hâlâ açık kapıdır.
- Son CP09/CP10 yerel güncellemesinde release manifest `75` dosya, contract checker `8 schema`, packaging `2 passed`, policy/comparison `4 passed`, accepted-execution attribution CLI e2e `1 passed` verdi. Son risk/attribution düzeltmesi sonrası tam offline suite yeniden çalıştırılmadı; önceki tam sonuç yukarıdaki `317` kaydıdır.
- Jenkins tüketici fragment'i artık `report.json` ve `mail-view.json` report kimliklerini, `render-manifest.json` HTML byte sayısını ve HTML hash'ini emailext öncesi doğruluyor; artifact consumer/fragment kontrolleri `5 passed`. Gerçek Jenkins sunucusu ve emailext teslimi hâlâ NOT_RUN.
- Runtime policy Markdown dosyaları paket içinden sürüm/hash ile yükleniyor; wheel testi policy Markdown, şema, Innova template ve onaylı logo üyelerini doğruluyor. Gerçek route A/B kalite koşusu ve insan formu doldurma dış ortam kapısı olarak NOT_RUN.
