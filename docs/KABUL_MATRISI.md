+# Kabul Matrisi

Bu tablo S16'daki 129 kabul kimliginin tamamini listeler. `PASS` yalniz normatif senaryonun A/E/K ve state sonucunu dogrudan assert eden calisan test icin kullanilir. Alt bilesen testi bulunan fakat tam state/mail sonucu kurulmamıs senaryo `NOT RUN` kalir; boylece birim kapsami kabul sonucu gibi gosterilmez. Canli servisler acik yetki verilmedigi icin calistirilmadi.

| ID | Test dosyasi / test adi | Beklenen A/E/K | Sonuc | Not |
|---|---|---:|---|---|
| G01 | `tests/test_cli_e2e.py::test_offline_baseline_no_change_out_of_scope_and_dry_run` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G02 | `tests/test_cli_e2e.py::test_offline_baseline_no_change_out_of_scope_and_dry_run` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G03 | `tests/test_cli_e2e.py::test_stateful_analysis_uses_one_model_unit_and_one_smtp_transaction` | 1/1/1 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G04 | `tests/test_cli_e2e.py::—` | 3/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G05 | `tests/test_git_selection.py::test_g05_revert_has_empty_net_but_two_history_transitions` | 2/1/1 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G06 | `tests/test_cli_e2e.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G07 | `tests/test_state_recovery.py::test_g07_init_creates_empty_checkpoint_and_verified_identity` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G08 | `tests/test_state_recovery.py::test_g08_run_open_does_not_recreate_missing_database` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G09 | `tests/test_cli_e2e.py::—` | 1/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G10 | `tests/test_cli_e2e.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G11 | `tests/test_cli_e2e.py::—` | 2/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G12 | `tests/test_cli_e2e.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G13 | `tests/test_cli_e2e.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G14 | `tests/test_cli_e2e.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G15 | `tests/test_cli_e2e.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G16 | `tests/test_git_selection.py::test_g16_divergence_fails_closed` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G17 | `tests/test_cli_e2e.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G18 | `tests/test_git_selection.py::test_g18_backlog_limit_does_not_silently_truncate` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| G19 | `tests/test_cli_e2e.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| G20 | `tests/test_git_selection.py::test_g20_tab_and_newline_path_roundtrips_as_raw_bytes` | 1/1/1 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| S01 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S02 | `tests/test_state_recovery.py::test_s02_owner_uuid_mismatch_fails_closed` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| S03 | `tests/test_state_recovery.py::test_s03_second_scope_lock_is_busy_and_lock_file_is_not_deleted` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| S04 | `tests/test_state_recovery.py::—` | 1/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S05 | `tests/test_state_recovery.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S06 | `tests/test_state_recovery.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S07 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S08 | `tests/test_state_recovery.py::—` | 1/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S09 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S10 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S11 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S12 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S13 | `tests/test_state_recovery.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| S14 | `tests/test_state_recovery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O01 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O02 | `tests/test_oracle_coverage.py::—` | 2/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O03 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O04 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O05 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O06 | `tests/test_oracle_coverage.py::—` | 2/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O07 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| O08 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| O09 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| O10 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P01 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| P02 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| P03 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| P04 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P05 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P06 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P07 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P08 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| P09 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P10 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P11 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P12 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| P13 | `tests/test_oracle_coverage.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N01 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| N02 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| N03 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N04 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N05 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N06 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N07 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| N08 | `tests/test_oracle_coverage.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| D01 | `tests/test_dependencies_context.py::—` | 2/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| D02 | `tests/test_dependencies_context.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| D03 | `tests/test_dependencies_context.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| D04 | `tests/test_dependencies_context.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| C01 | `tests/test_dependencies_context.py::—` | 4/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| C02 | `tests/test_dependencies_context.py::—` | 3/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| C03 | `tests/test_dependencies_context.py::—` | 0/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| C04 | `tests/test_dependencies_context.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| C05 | `tests/test_dependencies_context.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A01 | `tests/test_litellm_validation.py::—` | 2/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A02 | `tests/test_litellm_validation.py::—` | 2/0/0 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A03 | `tests/test_litellm_validation.py::—` | 2/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A04 | `tests/test_litellm_validation.py::—` | 2/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A05 | `tests/test_litellm_validation.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A06 | `tests/test_litellm_validation.py::—` | 1/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A07 | `tests/test_litellm_validation.py::—` | 2/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A08 | `tests/test_litellm_validation.py::—` | 4/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A09 | `tests/test_litellm_validation.py::—` | 1/0/0 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A10 | `tests/test_litellm_validation.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| A11 | `tests/test_litellm_validation.py::—` | 1/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A12 | `tests/test_litellm_validation.py::—` | 2/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A13 | `tests/test_litellm_validation.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| A14 | `tests/test_litellm_validation.py::—` | 2/0/0 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| M01 | `tests/test_smtp_delivery.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M02 | `tests/test_smtp_delivery.py::—` | 1/1/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M03 | `tests/test_smtp_delivery.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M04 | `tests/test_smtp_delivery.py::—` | 0/0/0 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| M05 | `tests/test_smtp_delivery.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M06 | `tests/test_smtp_delivery.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| M07 | `tests/test_smtp_delivery.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M08 | `tests/test_smtp_delivery.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M09 | `tests/test_smtp_delivery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M10 | `tests/test_smtp_delivery.py::—` | 1/1/1 | NOT RUN | Ilgili birim testi PASS; ancak bu ID'nin tam A/E/K + checkpoint entegrasyon senaryosu NOT RUN. |
| M11 | `tests/test_smtp_delivery.py::—` | 20/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M12 | `tests/test_smtp_delivery.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M13 | `tests/test_smtp_delivery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| M14 | `tests/test_smtp_delivery.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| I01 | `tests/test_security_isolation.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| I02 | `tests/test_security_isolation.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| I03 | `tests/test_security_isolation.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| I04 | `tests/test_security_isolation.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| I05 | `tests/test_corpus_admission.py::test_i05_rejects_unsafe_member_paths` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| X01 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X02 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X03 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X04 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X05 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X06 | `tests/test_real_shapes.py::—` | 0/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X07 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X08 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X09 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X10 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X11 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X12 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X13 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X14 | `tests/test_real_shapes.py::—` | 1/1/1 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X15 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X16 | `tests/test_corpus_admission.py::test_x16_application_archive_cannot_be_metadata` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| X17 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X18 | `tests/test_real_shapes.py::test_x18_k4_package_without_slash_keeps_both_headers_without_history_pairing` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| X19 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Saglanan metadata ZIP'i normatif K3 SHA/sayimlariyla eslesmedigi icin fail-closed; baska corpusla ikame edilmedi. |
| X20 | `tests/test_corpus_admission.py::test_x20_provenance_is_not_inferred_from_name_or_mtime` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |
| X21 | `tests/test_real_shapes.py::—` | 0/0/0 | NOT RUN | Tam normatif fault-injection ve state/mail sonucu henuz ayrik kabul testi olarak calistirilmadi. |
| X22 | `tests/test_packaging.py::test_wheel_excludes_private_inputs` | 0/0/0 | PASS | Offline/sentetik fixture ile dogrudan assert edildi. |

Ozet: 16 PASS, 113 NOT RUN, 0 FAIL. Bu sayilar pytest test sayisi degil, normatif kabul ID durumudur. Gercek LiteLLM, SMTP, Jenkins ve production gpu-db history testleri yapilmadi.
