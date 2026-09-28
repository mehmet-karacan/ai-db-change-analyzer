# Kurulumda Sağlanacak Değerler

| Değer | Kabul kanıtı |
|---|---|
| Dedicated kalıcı Linux agent label, Python 3.13 patch, Git | `doctor --offline`; state/workspace ayrımı |
| Kalıcı local state root, sahibi ve host backup politikası | owner UUID, lock ve restore provası |
| Read-only gpu-db HTTPS credential ID, URL/branch/root yerleşimi | full-history fetch ve dry-run; push yetkisi yok |
| LiteLLM key credential ID | ayrı kullanıcı key'i; değer rapor/config/logda yok |
| Kesin chat route, model izni, output mode/parametre ve context window | yetkili sentetik smoke + reviewed capability record |
| TLS CA ve gerekiyorsa explicit HTTPS proxy | verify açık; proxy downstream fallback/retention politikası |
| SMTP host/port/from/auth credential ID/CA/Message-ID domain | test alıcısıyla STARTTLS, kabul/ret/UNKNOWN provası |
| Jenkins artifact ve rapor ACL'i; URL allowlist/template | yetkisiz teknik kaynak erişimi yok |
| Production gpu-db gerçek history ve exporter davranışı | K3/K4 geliştirme corpus'undan ayrı production gate |

Environment allowlist: `LITELLM_API_KEY`, `DB_ANALYZER_GIT_USERNAME`, `DB_ANALYZER_GIT_PASSWORD`, `DB_ANALYZER_SMTP_USERNAME`, `DB_ANALYZER_SMTP_PASSWORD`, `ANALYZER_CONFIG`, `ANALYZER_WHEELHOUSE`, `BUILD_NUMBER`, `BUILD_URL`, `JOB_NAME`, `NODE_NAME`. Secret değerleri kullanıcıdan sohbete veya dosyaya istenmez.
