# Kaynak Manifesti

Bu ürünün çalışma zamanı veri sözleşmesi sabit Git revision’larıdır. Yerel ZIP arşivleri yalnız geliştirme ve doğrulama corpus’udur; production `gpu-db` geçmişi veya canlı veritabanı snapshot’ı sayılmaz.

| Kaynak | Beklenen kimlik | 28 Eylül 2026 yerel gözlem | Rol |
|---|---|---|---|
| `Jenkinsfile.txt` | `3a125d…4619` | eşleşti | Mevcut DB Sync sınırı; değiştirilmez |
| `opencode.json` | `b4f5eb…2131` | eşleşti | LiteLLM URL/model adı referansı; runtime bağımlılığı değildir |
| metadata ZIP (normatif K3) | `cb2a5d…1c88` | **eşleşmedi:** `2f81ff…a7322` | Yerel dosya ayrı corpus olarak ölçülür; K3 sonuçları ona mal edilmez |
| application ZIP (K4) | `6f68af…9d2d` | eşleşti | Biçim/negatif fixture referansı; DDL snapshot değildir |

Yerel metadata arşivi 2.341 dosya/2.340 SQL içerir; `package_bodies/` kökü yoktur. Manifest 2.386 bulunan nesne, 2.340 yazılan DDL ve 46 hata beyan eder. Bu nedenle AKTIF_GOREV.md içindeki K3’e özgü 2.381/2.380/2.422 değerleri bu farklı hash’e uygulanmaz.

Ham arşivler, referans config ve mevcut DB Sync dosyası `.gitignore` ile ürün repository’si/wheel dışında tutulur.
