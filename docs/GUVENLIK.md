# Güvenlik Sınırları

- Kaynak yalnız allowlist HTTPS Git hostundan, uygulamanın owned bare cache'ine alınır. Hook, replace/graft, external diff, textconv, submodule ve credential helper etkileri kapalıdır.
- Revision girdisi full OID veya uygulamanın kendi ref'idir. Shell kullanılmaz; kaynak SQL hiçbir yürütme motoruna verilmez.
- Parser child'a model/SMTP/Git secret aktarılmaz. Model key yalnız `LITELLM_API_KEY`, SMTP/Git credential yalnız adlandırılmış environment alanlarından okunur.
- HTTPX `trust_env=false`, redirect kapalı, TLS verification zorunludur. Route/model/capability doğrulanmadan production DDL gönderilmez; 400 yanıtından capability downgrade çıkarılmaz.
- Model kaynakları güvenilmeyen veri kabul eder. Tool/function çağrısı yoktur. JSON, şema, unit echo, evidence üyeliği ve secret taraması yerelde uygulanır. Bu kontroller yorumun semantik doğruluğunu kanıtlamaz.
- Jinja `StrictUndefined` ve autoescape kullanır; AI metni template olarak derlenmez veya `safe` yapılmaz. Rapor linkleri yalnız açık HTTPS host allowlist'inden gelir.
- SMTP plaintext fallback yapmaz. DATA sonrası bağlantı belirsizliği `UNKNOWN` olur ve açık duplicate-risk kararı olmadan retry edilmez. SMTP 250 inbox/okunma kanıtı değildir.
- Raw prompt/response, secret, credential-bearing URL ve private corpus Git'e/loga/result projection'ına yazılmaz. Secret scanner bütün hassas veriyi bulma garantisi değildir; repository/model/artifact ACL'i zorunludur.
- Owned state root marker+UUID, realpath containment, symlink reddi ve OS lock ile korunur. Cleanup genel amaçlı dosya silici değildir.

Bilinen sınırlar: ANTLR grammar topluluk grammar'ıdır; Oracle derleyicisi değildir. Dependency çözümü repository içi statik kanıttır; canlı `ALL_DEPENDENCIES`, runtime SQL veya etkisiz tüketici garantisi değildir. Git yazarı DB değişikliğini yapan kişi ve commit zamanı DDL uygulanma zamanı sayılmaz.
