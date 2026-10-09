# Gerçek Model Değerlendirme Formu

Bu form, `tests/fixtures/review_eval/corpus.json` ile yapılan gerçek route
koşularında insan değerlendirmesini makine ölçümlerinden ayırmak için kullanılır.
Mock koşusu yalnız sözleşme ve rubrik tesisatını doğrular; model kalite kabulü
değildir.

## Koşu bilgisi

| Alan | Değer |
|---|---|
| Tarih/saat (UTC) | |
| Corpus SHA-256 | |
| Baseline/candidate policy fingerprint | |
| Route ve configured model | |
| Result dosyası | |
| Değerlendiren | |

## Case değerlendirmesi

Her case için ayrı satır doldurulur. Kaynak DDL, ham credential, ham prompt veya
özel kurum verisi forma kopyalanmaz; yalnız case kimliği, kanıt kimliği ve kısa
değerlendirme yazılır.

| Case ID | Beklenen sonuç | Gözlenen sonuç | Kanıt bağlı mı? | Bulgu doğru mu? | Severity doğru mu? | Belirsizlik doğru mu? | Karar / not |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Kabul ölçütü

- Açıklama, bulgu ve öneri ayrı değerlendirilir.
- Her açıklama/finding kabul edilmiş execution kimliğine ve izinli evidence ID'lerine bağlı olmalıdır.
- `no_finding`, `insufficient_context`, `partial` ve `blocked` birbirinin yerine yazılmaz.
- Modelin gerçek route etiketi, policy fingerprint'i, response schema'sı ve
  generation bilgisi sonuç dosyasındaki execution kaydıyla eşleşmelidir.
- İnsan kararı `PASS`, `REVIEW_REQUIRED` veya `FAIL` olarak açıkça yazılır.
