# Разбор падений R0b

Нормативный эталон имён — объединение падений `915b932f` и `3ec7c957`.

| Срез | SHA | Junit | Цифры |
|---|---|---|---|
| R0a-база | `915b932f` | `/tmp/pytest-base.xml` | 46 failure, 3 error, 2870 testcase |
| #342 | `3ec7c957` | `/tmp/pytest-342.xml` | 56 failure, 26 error, 8417 testcase |
| #356+#354 | не этот эталон | `/tmp/pytest-base-v3.xml` | 46 failure, 0 error, 3050 testcase |
| предок main | `c53a238b` | junit в этом окружении нет | ~1398 функций `test_` в дереве; 99F — цифра ревью |

`c53a238b` — «Fix nodes and SETUP.cmd», merge #325, предок HEAD. 99F на нём и 46F на #356+#354 — разные срезы, не один список. На `c53a238` тестовых функций около 1398, на `915b932f` около 1984, на `3ec7c957` около 3734, в junit #356+#354 собрано 3050 testcase. 46F — узкий прогон #356+#354 без error. 99F относится к старому срезу `c53a238`, для которого здесь нет junit, и нормативным списком блокеров #353 не является. Имена для #353 сверяются только с союзом `915b932f` ∪ `3ec7c957`.

Текст ошибок в таблицах снят с гейта `f027cb5c` (`pytest tests/ -n auto --timeout=600`, 2026-10-18 01:05 UTC) на машине, где уже стояли ffmpeg 6.1.1 и ffprobe: 60 failed, 8501 passed, 7 skipped, 1 xfailed, 22 errors. Имён failure/error вне союза не было.

Полный гейт текущего HEAD `9b76581f` (`pytest tests/ -n auto --timeout=600`, ffmpeg 6.1.1 `/usr/bin/ffmpeg`): 60 failed, 8505 passed, 7 skipped, 1 xfailed, 22 errors, 456 subtests passed, 1393.67 с. В junit 8595 testcase. Имён failure/error вне `915b932f` ∪ `3ec7c957`: 0 (60+22, все в союзе).

Промежуточный прогон `91d29264` дал 62 failed и 8503 passed. Два имени были вне союза: `test_nested_gate_of_same_provider_is_not_counted_twice` и `test_k9_paid_gate_no_false_refusal_for_own_plan`. Их закрыл `9b76581f`.

Полный прогон без ffmpeg в этот срок не снимался: оба полных гейта выше уже с ffmpeg 6.1.1 в PATH. Пачка медиа при установленном ffmpeg всё равно падает текстами из таблиц; отдельные пробы с тем же бинарём зелёные. На ПК Heil с ffmpeg 8.1.2 те же 26 имён зелёные и на #353, и на #342.

Heil прогнал 26 имён бывшей группы «тесты из #342 без кода/фикстур» на ПК с ffmpeg 8.1.2: они зелёные и на #353, и на #342 (`3ec7c957`). Это окружение, не нехватка кода. Шесть строк с `human_review` вместо `verified` и два `kill_switch`, у которых в прошлом разборе стояла причина «проба медиа», тоже окружение: в тексте гейта это `human_review` / `ValueError` пробы, а не отсутствующий код.

Медиа-группа кроме Chromium и numpy на этой машине с ffmpeg 6.1.1:

- точечно зелёные: `test_video_audio_streams_and_duration`, `test_audible_music_passes`, `test_a_swallowed_paid_seam_refusal_still_fails_the_verification`, `test_no_paid_request_is_reachable_on_the_assemble_path` (последние два — в паре);
- пачка 54 имён, `pytest -n auto`: 50 failed, 4 passed за 201 с. Четыре зелёных — бывшие регрессии снимка. Остальные падают тем же текстом, что в гейте `f027cb5c`.

Падение пачки при установленном ffmpeg 6.1.1 не новый код R0b: те же имена и тот же текст уже есть в junit #342. На ПК с ffmpeg 8.1.2 группа зелёная на обоих деревьях. Отдельного фикса под это нет.

## регрессия снимка

Четыре имени были вне эталона на снимке `15598ef8` и на `f027cb5c` уже зелёные. Блокеров мёржа в этой группе нет.

| Имя | Текст на снимке | Сейчас |
|---|---|---|
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_disk_block_waits_without_retry` | на снимке не было маршрута 52pay; на f027cb5c зелёный | зелёный |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_exhausted_budget_does_not_send_http` | на снимке не было маршрута 52pay; на f027cb5c зелёный | зелёный |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_kill_switch_waits_without_retry` | на снимке не было маршрута 52pay; на f027cb5c зелёный | зелёный |
| `tests.w5_d05_split_image_prompts_retry_test::test_rollback_returns_the_frames_their_artifacts_and_meta` | на снимке откат не вернул artifact; на f027cb5c зелёный | зелёный |

## окружение: нет рабочего проба ffmpeg/ffprobe

Имён: 50. Сюда входят бывшие 26 «без кода», строки `human_review` и оба `kill_switch`. Текст — первая строка failure/error из гейта `f027cb5c`.

| Имя | Текст гейта |
|---|---|
| `tests.observe_v1.test_media_contract.Artifacts::test_video_audio_streams_and_duration` | AssertionError: 'failed' != 'passed' - failed + passed : {'status': 'failed', 'kind': 'video', 'semantic_quality_verified': False, 'bytes': 11743, 'sha256': 'b909630167cb44c406da8ea79519e47b64a8277c7e76416470d7c569d7712c09', 'reason': 'ValueError'} |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_final_that_is_older_than_the_claim_is_not_this_attempts_result` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_ledger_that_grew_during_the_retry_fails_the_verification` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_second_assemble_of_the_same_output_does_not_create_a_second_approval` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_swallowed_paid_seam_refusal_still_fails_the_verification` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_an_approval_decided_before_verification_fails_the_barrier_check` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_failed_attempt_to_decision_to_one_verified_worker_retry` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_no_paid_request_is_reachable_on_the_assemble_path` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_one_in_node_retry_replaces_the_old_final_only_after_verification_and_leaves_one_pending_approval` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_the_assemble_attempt_emits_no_model_facts` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_without_a_decision_layer_the_same_ticket_is_one_dispatch_and_idempotent` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | AssertionError: 'human_review' != 'verified' - human_review + verified : {'operation': 'resume_poll', 'verified': False, 'problems': ['music:file_ValueError'], 'error': None, 'resumed': [{'key': 'music', 'task_id': 'T-1', 'outcome': 'downloaded', 'error': None}], 'stats': {'checked': 1, 'downloaded': 1, 'failed': 0,... |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_frame_failure_is_not_swallowed_so_the_attempt_ends_with_node_error_and_recovery_is_allowed` | AssertionError: 'human_review' != 'verified' - human_review + verified : {'operation': 'resume_poll', 'verified': False, 'problems': ['clip_001:file_ValueError'], 'error': None, 'resumed': [{'key': 'clip_001', 'task_id': 'T-1', 'outcome': 'downloaded', 'error': None}], 'stats': {'checked': 1, 'downloaded': 1, 'faile... |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_real_step_success_is_verified_by_the_attempt_session_not_failed_too_early` | AssertionError: Tuples differ: ('videos', 'failed') != ('videos', 'passed') First differing element 1: 'failed' 'passed' - ('videos', 'failed') ? ^ ^^ + ('videos', 'passed') ? ^ ^^ : {'contract': 'native_media', 'node_type': 'videos', 'status': 'failed', 'reason': 'ValueError', 'summary': 'клипы: не прошли проверку ... |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | AssertionError: 'human_review' != 'verified' - human_review + verified : {'operation': 'resume_poll', 'verified': False, 'problems': ['clip_001:file_ValueError'], 'error': None, 'resumed': [{'key': 'clip_001', 'task_id': 'T-1', 'outcome': 'downloaded', 'error': None}], 'stats': {'checked': 1, 'downloaded': 1, 'faile... |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_the_failed_attempt_may_edit_frame_progress_without_invalidating_the_baseline` | AssertionError: 'human_review' != 'verified' - human_review + verified : {'operation': 'resume_poll', 'verified': False, 'problems': ['clip_001:file_ValueError'], 'error': None, 'resumed': [{'key': 'clip_001', 'task_id': 'T-1', 'outcome': 'downloaded', 'error': None}], 'stats': {'checked': 1, 'downloaded': 1, 'faile... |
| `tests.test_media_tranche_local::test_assemble_dry_run_with_the_real_verifier` | app.orchestrator.steps.assemble.AssembleVerificationError: assemble: проверка итогового видео не пройдена (failed: ValueError); финальный файл не изменён, подтверждение не создаётся |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_silent_voice_file_fails_with_the_silent_code` | AssertionError: Tuples differ: ('failed', 'ValueError') != ('failed', 'silent') First differing element 1: 'ValueError' 'silent' - ('failed', 'ValueError') + ('failed', 'silent') |
| `tests.w5_d06_local_resume_test::test_audio_finished_file_is_resume_local_and_verified` | AssertionError: {'operation': 'resume_local', 'verified': False, 'problems': ['audio:file_ValueError'], 'error': None, ...} assert False is True |
| `tests.w5g_media_nodes_route_more_script_test::test_a_real_dry_run_proves_the_video_node_offline` | AssertionError: null, "duration": null, "streams": null} FAIL clip_is_short_silent_and_not_above_720p detail: {"duration": null, "streams": null, "width": null, "height": null} PASS paid_body_is_the_cheapest_allowed_and_clean PASS worst_case_within_authorisation PASS provider_independently_saw_one_submit PASS dry_no... |
| `tests.w70_media_checks_test.SoftMediaChecks::test_all_black_clip_is_soft` | AssertionError: Tuples differ: ('failed', 'ValueError') != ('unknown', 'soft_clip_black') First differing element 0: 'failed' 'unknown' - ('failed', 'ValueError') + ('unknown', 'soft_clip_black') |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_aspect_mismatch_is_soft_not_failed` | AssertionError: Tuples differ: ('failed', 'ValueError') != ('unknown', 'soft_clip_aspect_mismatch') First differing element 0: 'failed' 'unknown' - ('failed', 'ValueError') + ('unknown', 'soft_clip_aspect_mismatch') |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_much_shorter_than_scenes_is_soft` | AssertionError: Tuples differ: ('failed', 'ValueError') != ('unknown', 'soft_final_shorter_than_scenes') First differing element 0: 'failed' 'unknown' - ('failed', 'ValueError') + ('unknown', 'soft_final_shorter_than_scenes') |
| `tests.w70_media_checks_test.SoftMediaChecks::test_silent_music_is_soft` | AssertionError: Tuples differ: ('failed', 'ValueError') != ('unknown', 'soft_music_silent') First differing element 0: 'failed' 'unknown' - ('failed', 'ValueError') + ('unknown', 'soft_music_silent') |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | AssertionError: 'human_review' != 'verified' - human_review + verified |
| `tests.test_media_tranche_local::test_approve_final_barrier_cannot_be_bypassed_by_auto_mode_or_a_tampered_file` | assert False is True |
| `tests.test_media_verification.OtherMediaNodes::test_assemble_final_digest_and_the_approval_barrier_are_reported` | AssertionError: 'failed' != 'passed' - failed + passed |
| `tests.test_media_verification.OtherMediaNodes::test_audio_counts_and_the_uncertain_marker` | AssertionError: Tuples differ: ('failed', []) != ('passed', []) First differing element 0: 'failed' 'passed' - ('failed', []) ? ^ ^^ + ('passed', []) ? ^ ^^ |
| `tests.test_media_verification.OtherMediaNodes::test_music` | AssertionError: Tuples differ: ('failed', 'S-1') != ('passed', 'S-1') First differing element 0: 'failed' 'passed' - ('failed', 'S-1') ? ^ ^^ + ('passed', 'S-1') ? ^ ^^ |
| `tests.test_media_verification.VideosVerification::test_good_clips_pass_and_the_provider_trail_is_recorded` | AssertionError: Tuples differ: ('failed', 2, 0) != ('passed', 2, 2) First differing element 0: 'failed' 'passed' - ('failed', 2, 0) ? ^ ^^ ^ + ('passed', 2, 2) ? ^ ^^ ^ |
| `tests.test_media_verification.VideosVerification::test_unknown_submit_outcomes_are_listed_per_provider_and_do_not_fake_a_failure` | AssertionError: 'failed' != 'passed' - failed + passed |
| `tests.test_media_verification.VideosVerification::test_unreadable_ledger_is_a_fact_and_provenance_says_so` | AssertionError: Tuples differ: ('failed', 'unavailable') != ('passed', 'unavailable') First differing element 0: 'failed' 'passed' - ('failed', 'unavailable') ? ^ ^^ + ('passed', 'unavailable') ? ^ ^^ |
| `tests.w40_test_infra_test::test_wait_returns_when_the_holder_finishes` | assert (False) |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_voice_file_with_a_signal_passes` | AssertionError: 'failed' != 'passed' - failed + passed |
| `tests.w5_d06_local_resume_test::test_sfx_event_files_on_record_are_resume_local_and_verified` | assert (False is True) |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_dry_run_of_one_node_runs_only_that_node` | AssertionError: de_started": false, "files": [["audio", "failed"], ["audio", "failed"]], "attempts": 1} PASS tts_other_text_under_resume_only_is_refused_before_the_wire PASS tts_timeout_after_send_writes_the_uncertain_marker PASS tts_marker_blocks_the_automatic_resend PASS tts_recovery_denies_a_paid_resend_after_unk... |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_real_dry_run_proves_both_nodes_offline` | AssertionError: ailed"], ["audio", "failed"]], "attempts": 1} PASS tts_other_text_under_resume_only_is_refused_before_the_wire PASS tts_timeout_after_send_writes_the_uncertain_marker PASS tts_marker_blocks_the_automatic_resend PASS tts_recovery_denies_a_paid_resend_after_unknown_outcome PASS tts_uncertain_failure_is... |
| `tests.w5g_media_nodes_route_more_script_test::test_the_fake_outsee_plays_image_and_video_submit_status_and_download_and_counts` | AssertionError: assert ('failed' == 'passed' - passed + failed) |
| `tests.w5h_assemble_local_script_test::test_a_real_dry_run_reassembles_once_under_the_ticket_and_leaves_the_approval_to_the_human` | AssertionError: S nothing_left_the_machine PASS asr_probe_is_reported real: ['application, worker, observer, diagnosis, Decision supervisor, recovery ticket and verification in-process', 'native step assemble.run: recover_before_assemble, the per-frame timeline, the REAL ffmpeg assembly (app.services.assembly.assemb... |
| `tests.w5h_assemble_local_script_test::test_the_made_clip_and_voice_decode_and_have_the_expected_length` | AssertionError: assert ('failed' == 'passed' - passed + failed) |
| `tests.w5k_media_resume_poll_script_test::test_a_real_dry_run_resumes_the_known_task_and_pays_nothing` | AssertionError: ed run leaves them; the original submit never happened in this scratch database', "the original spend_ledger row of the paid submit is not seeded (only the task ledger), so the 'before' money total is zero", 'the provider: a local fake Kie server on loopback (nothing left the machine, nothing was pai... |
| `tests.w70_media_checks_test.SoftMediaChecks::test_audible_music_passes` | AssertionError: Tuples differ: ('failed', []) != ('passed', []) First differing element 0: 'failed' 'passed' - ('failed', []) ? ^ ^^ + ('passed', []) ? ^ ^^ |
| `tests.w70_media_checks_test.SoftMediaChecks::test_auto_approve_of_final_is_blocked_by_soft_issue` | AssertionError: False is not True |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_with_matching_or_unset_aspect_passes` | AssertionError: 'failed' != 'passed' - failed + passed |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_matching_scenes_passes` | AssertionError: 'failed' != 'passed' - failed + passed |

## окружение: нет Chromium

Имён: 22. Бинарь Playwright в контейнере отсутствует. Это не ffmpeg и не регрессия R0b.

| Имя | Текст гейта |
|---|---|
| `tests.w6_2_publish_flow_test::test_captcha_refuses_with_code_before_upload` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_caption_not_filled_refuses_before_click` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_caption_over_limit_refused_before_opening_page` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_configured_busy_selector_blocks_until_gone` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_configured_captcha_selector` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_empty_account_still_unconfirmed_and_caption_checked` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_hashtags_from_config_are_typed` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_logged_in_selector_absent_means_not_logged_in` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_marker_without_post_url_is_unconfirmed` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_missing_file_input_and_missing_button_are_selector_not_found` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_post_link_selector_gives_url_and_url_marker_without_regex` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_preflight_does_not_upload_or_click` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_processing_timeout_never_clicks` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_rejected_after_click` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_screenshot_name_has_no_secret_parts` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_success_by_configured_markers_is_confirmed_with_post_url_and_screenshot` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_title_field_filled_when_configured` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_two_factor_and_not_logged_in` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_upload_failed_marker_refuses_before_click` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_verify_post_and_feed_check` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_waits_for_processing_then_enables_button` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |
| `tests.w6_2_publish_flow_test::test_without_markers_unconfirmed_even_though_clicked` | failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at /home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell ... |

## окружение: нет numpy

Имён: 3.

| Имя | Текст гейта |
|---|---|
| `tests.w5l_assemble_real_speech_test::test_a_frame_is_taken_with_ffmpeg_at_the_requested_time` | ModuleNotFoundError: No module named 'numpy' |
| `tests.w5l_assemble_real_speech_test::test_a_lower_third_band_difference_is_seen_and_noise_elsewhere_is_not` | ModuleNotFoundError: No module named 'numpy' |
| `tests.w5l_assemble_real_speech_test::test_small_codec_noise_below_the_pixel_threshold_does_not_count` | ModuleNotFoundError: No module named 'numpy' |

