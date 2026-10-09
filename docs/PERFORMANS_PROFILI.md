# Yerel performans profili

Bu profil 8 Ekim 2026'da, dış model/Oracle/Jenkins/Outlook çağrısı yapılmadan
alındı. `tools/profile_performance.py`, 41 nesneli source-review akışını yerel
Git/SQLite, deterministik mock model ve artifact renderer ile çalıştırır.

## Ölçülen sonuç

| Aşama | İlk ölçüm | Hızlandırma sonrası | Açıklama |
|---|---:|---:|---|
| 41 nesneli iki invocation toplamı | 298,1 sn | 104,3 sn | 40 + 1 birim bütçe akışı |
| Base/target inventory ilk parse | 79,2 sn | 79,2 sn | İzole parser worker gerçek maliyeti |
| Aynı base/target inventory tekrar çağrısı | yaklaşık 38 sn | 2,05 sn | Blob SHA + parser fingerprint cache |
| `_index` dört çağrı | 82,0 sn | 0,68 sn | Inventory'de zaten okunan blob tekrar kullanılmaya başladı |
| Model çağrıları | ölçülememişti | 1,42 sn | 41 yerel mock çağrısı, ortalama 0,035 ms |
| Response validation | ölçülememişti | 7,15 ms | 81 validation geçişi, ortalama 0,088 ms |
| Render + artifact dosyaları | ölçülememişti | 570,6 ms | Report, Innova view, manifest ve 6 staged dosya |

İlk ölçümde her `_index` çağrısı her dosya için ayrı `git cat-file -s` ve
`git cat-file blob` çalıştırıyordu. Son durumda `cat-file --batch` ve sınırlı
128 MiB process-içi blob cache kullanılıyor; inventory'nin taşıdığı aynı blob
`_index`, dependency snapshot ve context akışlarında tekrar okunmuyor. Parser
sonuçları aynı zamanda scope SQLite `cache_entries` tablosunda blob SHA ve parser
fingerprint ile saklanıyor; sonraki process aynı doğrulanmış sonucu kullanabiliyor.

## Parser gözlemi

Gerçek `gpu-db` dosyalarında küçük ama karmaşık package'lar 23–43 saniyede
tamamlanabiliyor. 123 view içeren 205.155 byte'lık birleşik export, birleşik
parser ve fragment retry ile 120,3 saniyede tamamlanmadı. Bu nedenle 32'den
fazla üst seviye nesneli export'lar pahalı retry'ı çalıştırmadan açık
`PARSER_NOT_RUN_LARGE_EXPORT` text fallback durumunda kalıyor. Böylece parser
kanıtı olmayan yapısal sonuç üretilmiyor ve uzun export tek başına job süresini
 tüketmiyor.

## Tekrar çalıştırma

```powershell
.venv\Scripts\python.exe tools/profile_performance.py --objects 41 `
  --output "$env:TEMP\db-change-profile-41.json"
```

Çıktı `started_at`, `finished_at`, toplam süre, nesne sayısı ve nesne başına
ortalama süreyi taşır. Bu profil kalite kabulü değildir; gerçek model ve dış
ortam kapıları ayrıca doğrulanmalıdır.
