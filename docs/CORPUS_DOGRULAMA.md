# Corpus Doğrulama

`tools/prepare_corpus.py` ZIP üyelerini çalıştırmadan inceler; absolute/traversal/drive path, symlink, encryption, duplicate path ve boyut limitlerini fail-closed uygular. Metadata ve application kaynaklarını ayrı provenance kayıtlarıyla yazar. `tools/verify_corpus.py` kopyalanan byte’ların boyut ve SHA-256 değerlerini yeniden doğrular.

Yerel komut:

```text
python tools/prepare_corpus.py --metadata-archive docs/gpu-fusion-metadata.zip --application-archive docs/gpu-fusion-release-5.1.0@20effa77e29.zip --output graft/corpus
python tools/verify_corpus.py graft/corpus
```

28 Eylül 2026 sonucu:

- Metadata: `2f81ffe06e261c3d2870b17074ac03f891c77fb42237fd97f95109aeef1a7322`, 2.341 file, 2.340 SQL, 3.937.438 açılmış byte; normatif K3 kimliğiyle eşleşmedi.
- Application: `6f68afc1458bc86e252ad3f020765b3e2b7fd06f2a383004bb4cd3a9aaf99d2d`, 1.645 file, 64 SQL, 8.745.239 açılmış byte; K4 ile eşleşti.
- Mevcut metadata hash’inde durum-tutan tam lexical koşu 2.382 CREATE occurrence, 2.380 farklı üst seviye ObjectKey adayı ve iki duplicate index grubu buldu. Tür dağılımı: 981 TABLE, 576 INDEX, 556 SEQUENCE, 130 VIEW, 41 TRIGGER, 40 PACKAGE_SPEC, 40 PACKAGE_BODY, 16 TYPE, 2 PROCEDURE. 42 dosya multi-CREATE; trailing trigger durumu 40 ENABLE/1 DISABLE.
- Pinned ANTLR 4.13.2 küçük structural fixture’larda gerçek generated parser ile çalıştırıldı. En büyük 796.216-byte package 60 saniyelik geliştirme denemesinde tamamlanmadı; bu açık `PARSER_TIMEOUT`/fallback kapısıdır, lexical başarı full parse diye raporlanmaz.
- Hiçbir SQL, shell veya uygulama kaynağı çalıştırılmadı. DB locator manifest çıktısında redacted tutulur.
- Tam pinned ANTLR parse/projection ve normatif K3 X01–X15 sonuçları, doğru K3 byte’ları bulunmadığı için bu farklı arşiv adına PASS yazılmaz.
