# Operasyon Rehberi

Bu ürün yalnız sabit Git snapshot'larını okur. Oracle'a bağlanmaz, DDL çalıştırmaz ve kaynak repository'ye yazmaz.

## İlk kurulum

1. Dedicated kalıcı Linux agent, Python 3.13 patch'i, Git, yerel wheelhouse, CA dosyaları ve ACL'ler hazırlanır.
2. `config/gpu.example.toml` kopyalanır; `docs/KURULUM_DEGERLERI.md` alanları doldurulur. Secret TOML'a yazılmaz.
3. `doctor --offline` çalıştırılır; ağ, model ve SMTP çağrısı yapmadığı stderr kaydından doğrulanır.
4. Boş owned state için bir kez `state init --confirm-new-install` kullanılır. Otomatik oluşturma veya kayıp state'i baseline sayma yoktur.
5. Yetkili sentetik `smoke-model --allow-ai --record ...` ve `smoke-mail --to ... --allow-mail` ayrı onayla çalıştırılır.
6. `inventory --target <FULL_SHA> --offline`, sonra `run --dry-run --offline` çalıştırılır.
7. İlk normal AUTO çağrısı yalnız baseline kurar; model ve mail kullanmaz.

`smoke-model` yalnız sabit sentetik SQL ile route ve yanıt şemasını doğrular; modelin
azami bağlam penceresini doğrulamaz. Bu değer ayrı bir sağlayıcı kaydıyla teyit
edilmeden üretim profilinde `capabilities_verified=true` yapılmaz.

Tam `inventory` bütün kapsamdaki dosyaları yapısal olarak ayrıştırmayı dener.
Değişiklik analizi ise bütün dosyaları nesne imzası için tarar, pahalı yapısal
ayrıştırmayı yalnız planın değişen yollarında çalıştırır. Timeout alan değişen
dosya raporda `limited` olarak görünür.

## Normal çalışma ve recovery

- `run --allow-ai --allow-mail`: fetch, fixed target, analiz, immutable rapor, outbox ve SMTP receipt akışıdır.
- `NO_CHANGE` ve `OUT_OF_SCOPE_ONLY` model/mail açmaz. `RETRY_PENDING` checkpoint'i korur; sonraki çağrı aynı pinned aralığı tamamlamalıdır.
- Divergence veya eksik ancestry exit 22'dir. Geçmiş kasıtlı kapatılacaksa iki SHA doğrulandıktan sonra `state rebaseline --expected-base ... --target ... --reason ... --ack-unanalysed-history` kullanılır.
- `UNKNOWN` SMTP otomatik retry edilmez. Relay kaydı kontrol edilir; kanıt varsa `notification resolve`, yeniden gönderim kararı varsa `notification retry --ack-duplicate-risk` kullanılır.
- Bildirilmiş blocked rapor için parser/kaynak düzeltmesinden sonra `state retry-blocked`; insanın zorunlu açığı kabul etmesi için `state acknowledge-limits` kullanılır. Her ikisi report digest ve expected base ister.
- Pending run fingerprint'i değiştiyse `state migrate-run --reason ...`; immutable rapor/outbox varsa migration reddedilir.
- Key rotation secret store/Jenkins credential üzerinde yapılır. Config'e veya Git'e key yazılmaz; bitmiş raporlar yeni model etiketi almaz.

## Backup, restore ve temizlik

`state backup` SQLite backup API, integrity check ve SHA-256 manifest üretir. Host backup sistemi owned state kökünü ayrıca korumalıdır. Restore sırasında job kapatılır ve `state restore --backup ... --reason ...` çalıştırılır.

`cleanup --dry-run` owner/installation marker ve DB referanslarını kontrol eden manifest üretir. Yalnız incelendikten sonra `cleanup --apply` kullanılır. Pending run, current checkpoint, UNKNOWN/outbox ve kullanıcı dosyaları retention ile silinmez.

## Exit özeti

0 başarı/bakım, 10 bildirilmiş limited, 11 pending veya review-required, 20 config/izin, 21 Git, 22 history, 23 state/lock, 30 model transport/auth, 31 model response, 40 SMTP failure/held, 41 SMTP unknown, 50 safety, 90 internal hatadır. Jenkins 10/11'i UNSTABLE, diğer sıfır dışını FAILURE yapar.
