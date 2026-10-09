# CP08 test matrisi

Bu dosya, `AKTIF_GOREV.md` Bölüm 11'deki T01–T32 ve H01–H16 senaryolarının
yerel kanıt durumunu izler. `PASS`, senaryonun ilgili davranışının bağımsız bir
testte doğrudan ölçüldüğü; `KISMİ`, ilgili altyapının test edildiği fakat
senaryonun bütün bileşiminin henüz tek testte kapatılmadığı; `AÇIK` ise yerel
kanıt bulunmadığı anlamına gelir. Canlı Oracle, kurumsal model, Jenkins ve
Outlook doğrulaması bu matriste yerel PASS sayılmaz.

## Deterministik ve mock model senaryoları

| ID | Durum | Yerel kanıt | Açık sınır |
|---|---|---|---|
| T01 | PASS | `tests/test_cli_e2e.py::test_source_review_profile_includes_added_and_removed_objects` | Sentetik kaynakla sınırlı |
| T02 | PASS | `tests/test_cli_e2e.py::test_source_review_profile_includes_added_and_removed_objects`; silinen nesne için base ve target `find_references` receipt'leri, target'ta kalan consumer kanıtı | Sentetik kaynakla sınırlı |
| T03 | PASS | `tests/test_git_selection.py::test_g05_revert_has_empty_net_but_two_history_transitions`; `tests/test_dependencies_context.py::test_repaired_caller_remains_history_only_when_target_has_no_reference`; `tests/test_dependencies_context.py::test_multiple_callers_keep_old_and_current_reverse_context_separate`; `tests/test_cli_e2e.py::test_source_review_profile_keeps_repaired_caller_in_history_only_context` aynı dosyada caller düzeltmesini doğruluyor | Sentetik kaynakla sınırlı |
| T04 | PASS | `tests/test_package_extraction.py` package/standalone overload-signature testleri; `tests/test_dependencies_context.py` package ve schema-qualified standalone named/default, incompatible arity, overload candidate testleri; `tests/test_source_review_eval.py` içindeki `optional-parameter-default-compatible` ve `required-parameter-arity-incompatible` corpus sahneleri; `tests/test_oracle_changes.py` standalone procedure/function signature fact testleri | Gerçek model kalite ölçümü, expression type conversion ve runtime binding ayrıca açık |
| T05 | PASS | `tests/test_package_extraction.py` exception/transaction parse testleri ve `tests/test_source_review_eval.py` içindeki `exception-propagation-context` corpus sahnesi; exception handler yokluğu otomatik bulguya çevrilmiyor | Gerçek model kalite ölçümü ve canlı caller propagation doğrulaması ayrıca açık |
| T06 | PASS | Package-body parse testleri ve `tests/test_source_review_eval.py` içindeki `commit-with-external-transaction` corpus sahnesi; `COMMIT` değişikliği koşullu transaction bulgusu ve hedef kaynak kanıtıyla sınanıyor | Gerçek model kalite ölçümü ve Oracle runtime transaction davranışı ayrıca açık |
| T07 | PASS | `tests/test_package_extraction.py::test_dynamic_sql_profiles_distinguish_static_bind_and_runtime_targets` ve `tests/test_oracle_changes.py::test_package_body_dynamic_sql_profile_is_bounded_and_safe_to_display` | Runtime hedef çözümlemesi hâlâ kaynak adayı |
| T08 | PASS | `tests/test_contract_examples.py`, `tests/test_dependencies_context.py` | — |
| T09 | PASS | `tests/test_dependencies_context.py`, `tests/test_view_extraction.py`, `tests/test_real_shapes.py` | — |
| T10 | PASS | `tests/test_research_tools.py::test_symbol_reference_and_history_tools_are_evidence_bound` ve `tests/test_cli_e2e.py::test_source_review_profile_includes_added_and_removed_objects`; base history ile target consumer receipt'leri ayrı korunuyor | Sentetik kaynakla sınırlı |
| T11 | PASS | `tests/test_git_selection.py::test_g05_revert_has_empty_net_but_two_history_transitions` | — |
| T12 | PASS | `tests/test_package_extraction.py` overload/nested routine testleri; `tests/test_dependencies_context.py` local package, unqualified standalone ve qualified CALL testleri | Nested local routine graph hedefi package candidate seviyesindedir; runtime/local scope binding ayrıca sınırlıdır |
| T13 | PASS | Source-review no-finding fixture/e2e akışı ve evaluation corpus içindeki `format-only-clean` sahnesi; bulgu üretme baskısı olmadan tamamlanan inceleme | Gerçek model kalite ölçümü canlı route'ta ayrıca açık |
| T14 | PASS | `tests/test_cli_e2e.py::test_source_review_resumes_after_validation_failure` | Model sağlayıcı davranışı mock |
| T15 | PASS | `tests/test_cli_e2e.py::test_source_review_profile_sends_body_text_changes_to_model` | — |
| T16 | PASS | `tests/test_dependencies_context.py::test_old_reverse_consumers_and_new_neighbors_are_both_selected` | — |
| T17 | PASS | `tests/test_dependencies_context.py::test_synonym_resolution_has_cycle_and_hop_boundaries` | DB link/AUTHID canlı çözümlemesi açık |
| T18 | PASS | `tests/test_workflow_conflicts.py` duplicate/context/order testleri | — |
| T19 | PASS | `tests/test_source_classification.py`, `tests/test_workflow_conflicts.py` | — |
| T20 | PASS | `tests/test_research_tools.py::test_search_cursor_paginates_until_scope_is_complete` | Timeout simülasyonu ayrıca açık |
| T21 | PASS | Dinamik SQL profile (`bind`, `concat`, `validation`, `loop`), `DYNAMIC_SQL_REVIEW` kontrolü; `tests/test_source_review_eval.py` içindeki `dynamic-literal-bind-safe` ve `dynamic-runtime-target-conditional` corpus sahneleri | Gerçek performans/exploit testi ve validation semantiğinin runtime doğrulaması açık |
| T22 | PASS | `tests/test_oracle_coverage.py`, sequence/table/view extractor testleri; `tests/test_source_review_eval.py` içindeki `sequence-start-change` ve `numeric-precision-tightening` corpus sahneleri | Canlı veri hacmi ve Oracle runtime davranışı açık |
| T23 | PASS | `tests/test_policy.py`, `tests/test_archive.py::test_range_archive_fingerprint_is_a_cache_key` | — |
| T24 | PASS | `tests/test_cli_e2e.py::test_source_review_budget_processes_real_scale_without_losing_units[41]` ve `[100]` | — |
| T25 | PASS | CLI model/tool/render/final-validation crash-resume e2e testleri ve `tests/test_cli_e2e.py::test_source_review_budget_progresses_and_final_report_contains_all_units` içinde pending run sırasında remote target ilerlemesi; eski target'a resume reddediliyor. Persisted report ve INVALID unit retry akışları pinli target'ı koruyor | Farklı canlı fetch/credential hata şekilleri ayrıca açık |
| T26 | PASS | `tests/test_corpus_admission.py`, `tests/test_retention.py`, `tests/test_v5_rendering.py`, source-review unsafe text testleri | Kurumsal gateway egress testi açık |
| T27 | PASS | `tests/test_review_contracts.py`, `tests/test_v5_rendering.py::test_fact_evidence_side_cannot_be_swapped` | Geniş temporal claim corpus'u açık |
| T28 | PASS | `tests/test_v5_rendering.py::test_scale_profiles_keep_totals_and_only_live_detail_links` | Native Outlook ölçek testi açık |
| T29 | PASS | Artifact profili CLI e2e ve `tests/test_artifact_consumer.py` | Jenkins üzerinde gerçek tüketim açık |
| T30 | PASS | `tests/test_archive.py::test_legacy_archive_manifest_remains_readable`, config delivery testleri | — |
| T31 | PASS | `tests/test_packaging.py`, `tests/test_v5_catalog.py` ve `tests/test_innova_rendering.py::test_innova_renderer_fails_closed_when_approved_template_is_missing` | Farklı installer/zip bozulma biçimleri ayrıca açık |
| T32 | PASS | `tests/test_research_journal.py`, `tests/test_state_recovery.py`, `tests/test_retention.py` | Windows symlink testi ortam nedeniyle atlandı |

## Görsel, içerik ve model atfı senaryoları

| ID | Durum | Yerel kanıt | Açık sınır |
|---|---|---|---|
| H01 | KISMİ | Sentetik fixture renderer testleri ve Innova profili | Referans HTML'ye karşı bağımsız ekran karşılaştırması açık |
| H02 | PASS | V5 no-change/sequence fixture'ları, 0/1/çok kayıt ölçek testleri ve `tests/test_v5_rendering.py::test_combined_unknown_and_history_only_objects_render_with_explicit_limits` | Native Outlook görsel kabulü ayrıca açık |
| H03 | PASS | `tests/fixtures/v5/01-v5-showcase.view.json` ve dinamik renderer testleri | — |
| H04 | PASS | `tests/test_v5_adapter.py::test_ai_ledger_keeps_each_units_returned_model_labels` | — |
| H05 | PASS | `tests/test_cli_e2e.py::test_source_review_resumes_after_validation_failure` artık başarısız ilk model ile kabul edilen devam modelinin `returned_model` ve `author_execution_id` bağını doğruluyor | Aynı invocation içindeki farklı model repair akışı ayrıca açık |
| H06 | PASS | `tests/test_v5_adapter.py::test_ai_ledger_preserves_multiple_returned_labels_without_version_claim` | — |
| H07 | PASS | Sequence/no-AI renderer ve artifact e2e testleri | — |
| H08 | PASS | Replay emit e2e ve render sidecar binding testleri | — |
| H09 | PASS | `tests/test_innova_rendering.py::test_innova_renderer_escapes_source_text_and_keeps_profiles_semantically_equal` | — |
| H10 | KISMİ | Uzun ad/SHA ve renderer ölçek fixture'ları | Browser görüntüsü/native Outlook 100–200% açık |
| H11 | PASS | `tests/test_innova_rendering.py::test_innova_renderer_separates_standalone_html_from_cid_email` | Mail gateway erişimi açık |
| H12 | PASS | `tests/test_review_contracts.py::test_source_review_rejects_unsafe_finding_text` ve renderer escaping testleri | Jenkins/email-ext ikinci geçişi gerçek ortamda açık |
| H13 | PASS | Archive/restore/emit hash binding e2e'leri | Gece yarısı gerçek replay koşusu açık |
| H14 | KISMİ | `tests/test_jenkins_consumer_contract.py`, artifact consumer negatifleri | Jenkins sunucusunda gerçek emailext çağrısı çalıştırılmadı |
| H15 | PASS | `tests/test_packaging.py::test_wheel_excludes_private_inputs`, catalog/manifest testleri ve `tests/test_innova_rendering.py::test_innova_renderer_fails_closed_when_approved_logo_is_missing` | Eksik template ile kurulum matrisi ayrıca açık |
| H16 | KISMİ | V5 operation/status ve no-AI renderer testleri; Innova kaynak tabanlı risk/etki özeti, nesne bazlı risk rozetleri, silinen nesne için unknown ayrımı ve critical/high/medium/low/unknown renk paleti testleri | İnsan görsel kabulü ve native Outlook kabulü açık |

## Bu turun doğrudan eklediği kanıt

- `search_sources` artık ilk sayfa, devam cursor'ı ve tamamlanmış kapsamı üç
  gerçek sentetik kaynak üzerinde ölçen bir testle doğrulanıyor.
- Source-review açıklama ve bulgu metinleri HTML, Jinja, Groovy code fence ve
  email-ext `$BUILD_URL` biçimlerini kabul etmiyor. Böylece model metninin
  ikinci bir şablon geçişinde çalıştırılabilir veri olarak taşınması
  engelleniyor.
- Dinamik SQL ham ifadeyi mailde göstermeden `mode`, `target`, `bind`, `concat`,
  `validation` ve `loop` profiliyle ayrıştırılıyor; runtime hedef yalnız aday olarak
  işaretleniyor ve `DBMS_ASSERT` görünürlüğü güvenlik sonucu sayılmadan sınırlı
  kontrol önerisine bağlanıyor.
- Silinen veya eklenen nesnelerde araştırma artık base ve target revision'larını
  ayrı `find_references` receipt'leriyle tarıyor; target'ta kalan tüketici
  kanıtı korunuyor. Qualified package/routine çağrıları package spec/body
  adaylarına `CALL` kenarı olarak ekleniyor; overload/runtime bağlama iddiası yapılmıyor. Schema-qualified standalone procedure/function
  repository’de tekil olarak bulunuyorsa statik hedefe bağlanıyor; bounded signature parse ile arity/name uyumu sınıflandırılıyor.
  Candidate çağrılar reverse impact context'e de alınıyor.
- Executable package/procedure/function kaynaklarında bilinen local/nested ve
  unqualified routine çağrıları da `CALL` graph'ına alınır; bilinmeyen local
  çağrılar builtin gürültüsü oluşturmamak için unresolved edge olarak üretilmez.
- T05/T06 için source-review evaluation corpus'una exception handler yokluğu ve
  `COMMIT` eklenmesi sahneleri eklendi. Mock rubric iki tekrar boyunca 8/8
  kabul ile geçti; sonuç model kalite kabulü sayılmaz.
- T04 için varsayılan parametreyle uyumlu imza genişlemesi ve varsayılanı
  olmayan parametreyle arity uyumsuzluğu sahneleri eklendi; named/positional
  çağrı kontrolü kaynak kanıtlı mock rubric'e bağlandı.
- T21/T22 için sabit bind'li ve runtime hedefli dynamic SQL ile sequence
  başlangıç ve numeric precision değişikliği sahneleri eklendi; koşullu etki,
  kanıt ve severity rubric'e bağlandı.

## CP08 kabul sınırı

Bu matris CP08'in yerel ilerlemesini görünür kılar; bütün satırlar PASS
olmadan CP08 tamamlandı sayılmaz. Gerçek model kalite değerlendirmesi,
Oracle metadata etkisi, Jenkins artifact teslimi ve klasik Outlook görsel
onayı ayrı dış ortam kapılarıdır.
