# Parser Destek Matrisi

| Nesne | Yerel destek | Kesinlik sınırı |
|---|---|---|
| TABLE | Header, kolon/constraint projection, raw/token farkı | Oracle semantik eşdeğerliği kanıtlanmaz |
| VIEW / MATERIALIZED_VIEW | Header ve query source | Statik repository referansları; sonuç satırı/plan bilinmez |
| SEQUENCE | Header ve görülen clause'lar | `START WITH` gerçek reset nedeni veya canlı değer değildir |
| INDEX | Header, unique/bitmap bilgisi | Fiziksel performans sonucu bilinmez |
| TRIGGER | Header, hedef nesne ve trailing ENABLE/DISABLE projection'ı | Source durumu canlı enable state değildir |
| PACKAGE_SPEC / PACKAGE_BODY | Ayrı logical occurrence, raw kaynak ve parser sonucu | Timeout/recovery/error varsa limited fallback |
| PROCEDURE / FUNCTION | Header, exception/COMMIT/ROLLBACK göstergeleri ve kaynak bölgesi | Dynamic SQL/conditional compilation sınırlıdır; çağrı imzası tam eşleştirilmez |
| TYPE / TYPE_BODY | Header ve güvenli kaynak farkı | Tüm type semantiği çıkarılmaz; typed alanlar sınırlı kalır |
| SYNONYM | Hedef adayı, cycle ve en çok 5 hop | DB link/görünmeyen schema unresolved kalır |
| GRANT/REVOKE ve wrappers | Limited/text fallback | Efektif yetki veya SQL*Plus çalışma sonucu yoktur |
| Symlink/gitlink/binary/encoding bozuk | Metadata/unresolved | Semantik analiz ve checkpoint otomasyonu bloke edilir |

Parser kaynağı grammars-v4 `b434a051c56dcc2a5de0a0f2d86575ed192b59da`, generator/runtime 4.13.2'dir. Generated dosyalar ve kaynaklar SHA-256 manifestlidir. SLL ardından LL denenir; örnek GPU konfigürasyonunda worker timeout 60 saniyedir ve timeout sonucu açık `PARSER_TIMEOUT` olur. Parse cache anahtarı blob SHA-256, timeout ve parser worker/generated manifest fingerprint'inden oluşur; sonuç önce process cache'inden, sonra scope SQLite `cache_entries` tablosundan okunur. 32'den fazla üst seviye nesne içeren birleşik export dosyalarında ölçülebilir olmayan uzun tekil parse yerine `PARSER_NOT_RUN_LARGE_EXPORT` ile text fallback seçilir; küçük birleşik dosyalarda nesne parçaları tek izole worker'da toplu doğrulanabilir. Raw CRLF byte offset/hash ile türetilmiş token görünümü birbirine karıştırılmaz.
