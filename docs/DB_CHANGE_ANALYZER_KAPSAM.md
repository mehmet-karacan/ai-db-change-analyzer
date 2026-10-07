# DB Change Analyzer kapsamı

Bu belge ürünün DB değişikliklerini yalnız bildirmesini değil, değişikliğin anlamını ve incelenmesi gereken olası etkileri kanıta bağlı biçimde göstermesini tanımlar.

## Yetkinlik katmanları

1. **Kaynak farkı:** Git snapshot’ları arasındaki yeni, değişen, silinen ve taşınan kaynakları tespit eder. Aralık içindeki her commit ve dosya geçişi raporda ayrı tutulur.
2. **Yapısal açıklama:** Tablo, kolon, constraint, index, view, package, sequence, trigger, procedure, function ve type kaynaklarının doğrulanabilen özelliklerini önceki/yeni değerleriyle gösterir. Grammar kapsamı dışındaki alanlar sınırlı olarak işaretlenir.
3. **Olası etki:** Repository içi statik forward/reverse ilişkilerden ve değişiklik türünden kontrol edilmesi gereken kolon sözleşmesi, veri bütünlüğü, sorgu planı, package çağrısı ve sequence davranışı alanlarını üretir. Bu bölüm gerçek tüketici bulunduğunu iddia etmez.
4. **Bağımlılık analizi:** Repository kaynakları içindeki statik ilişkiler eski ve yeni snapshot’ta kanıtlarıyla raporlanır. Oracle metadata, uygulama repository’leri ve Jenkins/deployment kayıtları için rapor sözleşmesinde ayrı bağlantı durumu tutulur; bu kaynaklar bağlı değilse gerçek çalışma zamanı etkisi iddia edilmez.
5. **Risk ve yayın hazırlığı:** Değişiklikleri kanıta dayalı deterministik kurallarla kontrol önceliğine ayırır; çalıştırılmamış ön kontrol SQL’leri, rollback inceleme notu ve deployment sonrası doğrulama adımları üretir.
6. **Kurumsal iletişim:** Linksiz, kurum kimliğine uygun HTML/plain text mail ve makine tarafından işlenebilir JSON üretir.

## Kalıcı arşiv

`reports.archive_enabled = true` ve `reports.archive_root` bir uygulama repository checkout kökünü gösterdiğinde, tamamlanmış rapor şu yapıya yazılır:

```text
<application-repository>/db-change-analyzer/reports/
└── <year>/<yyyy-mm-dd>/<repository>/<branch>/
    └── <base-sha>_<target-sha>_<report-id>/
        ├── report.html
        ├── report.txt
        ├── report.json
        ├── mail-view.json
        ├── render-manifest.json
        └── archive-manifest.json
```

Arşiv yazımı atomiktir. Aynı rapor tekrar üretildiğinde mevcut byte’lar doğrulanır ve kayıt tekrar yazılmaz. Aynı kimlik yolunda farklı içerik görülürse işlem `ARCHIVE_COLLISION` ile durur. Arşiv çıktısı kaynak DDL dosyalarının yerine geçmez; yalnızca analiz kaydıdır.

Otomatik `run` başlamadan önce İstanbul saatine göre gün/repository/branch klasörü içindeki tam `base_sha` ve `target_sha` aralığı aranır. Aynı commit aralığına ait geçerli `archive-manifest.json` varsa analiz, model çağrısı ve mail üretimi yeniden başlatılmaz; `RANGE_REPORT_EXISTS` sonucu ile mevcut manifest yolu bildirilir. Aynı gün içindeki farklı commit aralıkları ayrı arşiv yaprakları olarak çalışır.

Uygulama repository’sindeki `db-change-analyzer/reports/` yolu Jenkins tetikleme kapsamı dışında tutulmalıdır. Böylece Analyzer’ın arşiv commit’i yeni bir DB değişikliği gibi algılanıp aynı job’u döngüye sokmaz. Analyzer arşiv checkout’una Git push yapmaz; commit ve push Jenkins’in açıkça tanımlanmış publish adımının sorumluluğudur.

Raporlarda ham kaynak veya secret saklanmaz. Kanıt yolları, güvenli özetler, commit kimlikleri, değişen değerler ve hash’ler tutulur.
