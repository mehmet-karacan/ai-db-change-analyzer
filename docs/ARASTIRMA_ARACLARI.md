# Salt okunur araştırma araçları

`ResearchToolDispatcher`, Analyzer'ın sahip olduğu bare Git cache üzerinde
revision/path/scope doğrulaması yaparak `read_source`, `search_sources`,
`lookup_symbol`, `get_diff`, `find_references` ve `get_history` araçlarını sunar.
Araçlar çalışma dizini, shell, keyfi revision,
symlink, submodule veya kaynak yazma yetkisi vermez.

Her sonuç `tool_call_id`, argüman özeti, revision, scope hash, durum, kanıt
kimlikleri, kapsam ve tanılama alanlarını taşır. Arama sayfalıdır; cursor
revision, sorgu ve scope hash'e bağlıdır. Tam tarama yapılmadıysa sonuç
`partial` kalır ve kesin yokluk iddiası üretilemez.

