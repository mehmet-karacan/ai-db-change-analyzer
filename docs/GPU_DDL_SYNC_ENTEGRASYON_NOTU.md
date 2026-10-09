# GPU DDL Sync ile Analyzer sınırı

Yerel `C:\innova\projeler\gpu-db-ddl-sync` incelemesine göre mevcut Jenkins
akışı şu sınırla çalışıyor:

1. Oracle'dan `DBMS_METADATA.GET_DDL` ile `GPU_USER` ve `INNOVA_ODI`
   şemalarının çıktısını alıyor.
2. Çıktıyı hedef `gpu-db` checkout'ında `gpu_user/` ve `innova_odi/`
   klasörlerine yazıyor. `by_object` düzeninde nesne kartları, DDL, grants,
   comments ve dependency dosyaları da oluşabiliyor.
3. Kök `README.md` ve `AGENTS.md` dosyalarını da güncelliyor.
4. Bu kapsamı stage edip hedef `gpu-db` reposuna commit ve push ediyor.
5. `.oracle_ddl_sync_status.tsv`, `.oracle_ddl_sync_numstat.tsv`, commit ve
   push durum dosyaları Jenkins workspace'inde bildirim özeti için tutuluyor;
   bunlar hedef repo tarihçesinin yerine geçmiyor.

Analyzer için doğru kaynak, bu akışın sonunda oluşan `gpu-db` commit
tarihçesidir. Analyzer iki commit arasındaki kaynak snapshot'larını ve aralık
içindeki commitleri inceler; commit mesajı sabit olsa bile analiz sonucu mesajdan
üretilmez. Aynı gün içindeki farklı commitler kendi aralık/rapor kimlikleriyle
ayrılabilir; deduplication `base_sha + target_sha` üzerinden yapılır.

Mevcut DDL Sync Jenkins maili build/push durum mailidir ve `BUILD_URL` bağlantısı
içerir. Analyzer'ın yeni kurumsal raporu bunun yerine geçmez ve bu repoda mevcut
job aktive edilmez. Analyzer artifact profili `report.html`,
`report-email.html`, `report.txt`, `mail-view.json` ve manifest'i hazırlar;
mevcut Jenkins job'ı bu klasörü arşivleyecek şekilde ayrıca uyarlanabilir.

Üretici job'ın gerçek Oracle/Bitbucket/Jenkins çalıştırması bu yerel incelemede
yapılmadı. Kaynak kod davranışı doğrulandı; kurum pipeline kabulü
`NOT_VERIFIED` olarak kalır.
