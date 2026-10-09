# Analiz sözleşmesi

Bu belge `AKTIF_GOREV.md` 1.1'deki runtime veri ve provenance kararlarının
kısa uygulama özetidir. Normatif görev kapsamı `AKTIF_GOREV.md`, JSON Schema
dosyaları ve Python doğrulama modelleridir.

## Ayrımlar

- Deterministik Git/Oracle kaynak bulguları, model açıklamaları ve yapılmamış
  kontroller ayrı alanlarda tutulur.
- Bir model açıklaması kabul edildiğinde onu üreten `author_execution_id`,
  kullanılan policy fingerprint'i ve cevap şeması kaydedilir.
- `no_finding`, `insufficient_context`, `partial`, `blocked`, `failed` ve
  `not_run` birbirinin yerine geçirilemez.
- HTML veya mail şablonu gerçek sayım, kaynak, model veya risk verisi üretmez;
  yalnız doğrulanmış ortak view'i gösterir.

## Policy yükleme

`src/db_change_analyzer/prompts/analysis_policy.tr.md` ve
`report_language.tr.md` çalışma zamanında UTF-8 olarak yüklenir. İlk satırdaki
sürüm ve her dosyanın SHA-256 değeri `PolicyBundle.fingerprint` içinde tutulur.
Dosyalardan biri eksik, symlink veya okunamazsa sessiz legacy fallback yapılmaz.

## Sürüm geçişi

Eski `report/1.0`, `mail-view/2.0` ve V5 kayıtları okunabilir kalır. Aktif
canonical rapor `report/2.0` ve `analysis_fingerprint` ile üretilir; yeni
canonical, source-review ve mail-view sözleşmeleri eski bytes üzerinde yerinde
değişiklik yapmadan ayrı sürümler olarak üretilir.

