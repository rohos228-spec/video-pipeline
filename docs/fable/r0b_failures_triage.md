# Разбор +57F/+22E на R0b

Снимок прогона: дерево `/tmp/r0b-tip-v3`, HEAD `15598ef8`. Сравнение с базой #356+#354: junit с 46 failure и 0 error (`pytest-base-v3.xml`). Формула прироста: имена failure/error снимка минус имена этой базы. Получилось 57 failure и 22 error.

Эталоны: `915b932f` (junit базы R0a, 46 failure и 3 error) и `3ec7c957` (junit #342, 56 failure и 26 error). Имя считается эталонным, если оно есть хотя бы в одном из этих двух прогонов.

Вне эталона на снимке было 4 имени. На текущем HEAD `5b314795` все четыре зелёные (`pytest` этих четырёх, 4 passed). Новых блокеров мёржа среди +57F/+22E нет. Остальные 75 имён уже падают в `915b932f` или в `3ec7c957`.

Четыре имени вне эталона:

- `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_disk_block_waits_without_retry`
- `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_exhausted_budget_does_not_send_http`
- `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_kill_switch_waits_without_retry`
- `tests.w5_d05_split_image_prompts_retry_test::test_rollback_returns_the_frames_their_artifacts_and_meta`

| Имя | Вид | Причина | В 915b932f | В 3ec7c957 | Регрессия R0b |
|---|---|---|---|---|---|
| `tests.observe_v1.test_media_contract.Artifacts::test_video_audio_streams_and_duration` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_final_that_is_older_than_the_claim_is_not_this_attempts_result` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_ledger_that_grew_during_the_retry_fails_the_verification` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_second_assemble_of_the_same_output_does_not_create_a_second_approval` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_swallowed_paid_seam_refusal_still_fails_the_verification` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_an_approval_decided_before_verification_fails_the_barrier_check` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_failed_attempt_to_decision_to_one_verified_worker_retry` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_no_paid_request_is_reachable_on_the_assemble_path` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_one_in_node_retry_replaces_the_old_final_only_after_verification_and_leaves_one_pending_approval` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_the_assemble_attempt_emits_no_model_facts` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_without_a_decision_layer_the_same_ticket_is_one_dispatch_and_idempotent` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_frame_failure_is_not_swallowed_so_the_attempt_ends_with_node_error_and_recovery_is_allowed` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_real_step_success_is_verified_by_the_attempt_session_not_failed_too_early` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_the_failed_attempt_may_edit_frame_progress_without_invalidating_the_baseline` | failure | поведение: human_review вместо verified | нет | да | нет |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_disk_block_waits_without_retry` | failure | реальный баг снимка: нет маршрута 52pay; на HEAD зелёный | нет | нет | снята на HEAD |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_exhausted_budget_does_not_send_http` | failure | реальный баг снимка: нет маршрута 52pay; на HEAD зелёный | нет | нет | снята на HEAD |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_kill_switch_waits_without_retry` | failure | реальный баг снимка: нет маршрута 52pay; на HEAD зелёный | нет | нет | снята на HEAD |
| `tests.test_media_tranche_local::test_approve_final_barrier_cannot_be_bypassed_by_auto_mode_or_a_tampered_file` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_tranche_local::test_assemble_dry_run_with_the_real_verifier` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.test_media_verification.OtherMediaNodes::test_assemble_final_digest_and_the_approval_barrier_are_reported` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_verification.OtherMediaNodes::test_audio_counts_and_the_uncertain_marker` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_verification.OtherMediaNodes::test_music` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_verification.VideosVerification::test_good_clips_pass_and_the_provider_trail_is_recorded` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_verification.VideosVerification::test_unknown_submit_outcomes_are_listed_per_provider_and_do_not_fake_a_failure` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.test_media_verification.VideosVerification::test_unreadable_ledger_is_a_fact_and_provenance_says_so` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w40_test_infra_test::test_wait_returns_when_the_holder_finishes` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_silent_voice_file_fails_with_the_silent_code` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_voice_file_with_a_signal_passes` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5_d05_split_image_prompts_retry_test::test_rollback_returns_the_frames_their_artifacts_and_meta` | failure | реальный баг снимка: откат не вернул artifact; на HEAD зелёный | нет | нет | снята на HEAD |
| `tests.w5_d06_local_resume_test::test_audio_finished_file_is_resume_local_and_verified` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.w5_d06_local_resume_test::test_sfx_event_files_on_record_are_resume_local_and_verified` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_dry_run_of_one_node_runs_only_that_node` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_real_dry_run_proves_both_nodes_offline` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5g_media_nodes_route_more_script_test::test_a_real_dry_run_proves_the_video_node_offline` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.w5g_media_nodes_route_more_script_test::test_the_fake_outsee_plays_image_and_video_submit_status_and_download_and_counts` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.w5h_assemble_local_script_test::test_a_real_dry_run_reassembles_once_under_the_ticket_and_leaves_the_approval_to_the_human` | failure | окружение: проверка медиа даёт ValueError | нет | да | нет |
| `tests.w5h_assemble_local_script_test::test_the_made_clip_and_voice_decode_and_have_the_expected_length` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5k_media_resume_poll_script_test::test_a_real_dry_run_resumes_the_known_task_and_pays_nothing` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w5l_assemble_real_speech_test::test_a_frame_is_taken_with_ffmpeg_at_the_requested_time` | failure | окружение: нет numpy | нет | да | нет |
| `tests.w5l_assemble_real_speech_test::test_a_lower_third_band_difference_is_seen_and_noise_elsewhere_is_not` | failure | окружение: нет numpy | нет | да | нет |
| `tests.w5l_assemble_real_speech_test::test_small_codec_noise_below_the_pixel_threshold_does_not_count` | failure | окружение: нет numpy | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_captcha_refuses_with_code_before_upload` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_caption_not_filled_refuses_before_click` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_caption_over_limit_refused_before_opening_page` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_configured_busy_selector_blocks_until_gone` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_configured_captcha_selector` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_empty_account_still_unconfirmed_and_caption_checked` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_hashtags_from_config_are_typed` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_logged_in_selector_absent_means_not_logged_in` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_marker_without_post_url_is_unconfirmed` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_missing_file_input_and_missing_button_are_selector_not_found` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_post_link_selector_gives_url_and_url_marker_without_regex` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_preflight_does_not_upload_or_click` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_processing_timeout_never_clicks` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_rejected_after_click` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_screenshot_name_has_no_secret_parts` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_success_by_configured_markers_is_confirmed_with_post_url_and_screenshot` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_title_field_filled_when_configured` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_two_factor_and_not_logged_in` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_upload_failed_marker_refuses_before_click` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_verify_post_and_feed_check` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_waits_for_processing_then_enables_button` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w6_2_publish_flow_test::test_without_markers_unconfirmed_even_though_clicked` | error | playwright: нет Chromium | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_all_black_clip_is_soft` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_audible_music_passes` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_auto_approve_of_final_is_blocked_by_soft_issue` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_aspect_mismatch_is_soft_not_failed` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_with_matching_or_unset_aspect_passes` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_matching_scenes_passes` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_much_shorter_than_scenes_is_soft` | failure | поведение теста, уже в эталоне | нет | да | нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_silent_music_is_soft` | failure | поведение теста, уже в эталоне | нет | да | нет |

