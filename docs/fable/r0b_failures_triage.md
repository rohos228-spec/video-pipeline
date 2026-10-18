# Разбор +57F/+22E на R0b

Снимок прироста: дерево `/tmp/r0b-tip-v3`, HEAD `15598ef8`, против базы #356+#354 (`pytest-base-v3.xml`, 46 failure, 0 error). Прирост — 57 failure и 22 error, всего 79 имён.

Эталон: имя есть в `915b932f` или в `3ec7c957`. У 75 из 79 текст падения совпадает с junit #342. Четыре имени в эталоне не было — это регрессии снимка.

Гейт `f027cb5c0e18ce42ca9be5bb6c73adc155f3f0c4`: `pytest tests/ -n auto --timeout=600`, 2026-10-18 01:05 UTC. Итог: 60 failed, 8501 passed, 7 skipped, 1 xfailed, 22 errors. В этом прогоне 82 имени failure/error, и все они входят в эталон. Имён вне `915b932f` ∪ `3ec7c957` нет.

Группы прироста:

| Группа | Имён | На гейте f027cb5c |
|---|---:|---|
| регрессия | 4 | все 4 зелёные, открытых блокеров нет |
| окружение | 49 | падают так же, как на #342 |
| тесты из #342 без нужного кода/фикстур | 26 | падают так же, как на #342 |

Регрессия — блокер мёржа. Четыре блокера снимка на `f027cb5c` уже зелёные, новых коммитов под них нет. Skip и xfail не ставились.

## регрессия

| Имя | Вид | Сейчас | Почему эта группа |
|---|---|---|---|
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_disk_block_waits_without_retry` | failure | зелёный | на снимке не было маршрута 52pay; на f027cb5c зелёный |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_exhausted_budget_does_not_send_http` | failure | зелёный | на снимке не было маршрута 52pay; на f027cb5c зелёный |
| `tests.pipeline_regression.test_scene_boundaries_live_gate::test_live_kill_switch_waits_without_retry` | failure | зелёный | на снимке не было маршрута 52pay; на f027cb5c зелёный |
| `tests.w5_d05_split_image_prompts_retry_test::test_rollback_returns_the_frames_their_artifacts_and_meta` | failure | зелёный | на снимке откат не вернул artifact; на f027cb5c зелёный |

## окружение

| Имя | Вид | Сейчас | Почему эта группа |
|---|---|---|---|
| `tests.observe_v1.test_media_contract.Artifacts::test_video_audio_streams_and_duration` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_final_that_is_older_than_the_claim_is_not_this_attempts_result` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_ledger_that_grew_during_the_retry_fails_the_verification` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_second_assemble_of_the_same_output_does_not_create_a_second_approval` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_a_swallowed_paid_seam_refusal_still_fails_the_verification` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_an_approval_decided_before_verification_fails_the_barrier_check` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_failed_attempt_to_decision_to_one_verified_worker_retry` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_no_paid_request_is_reachable_on_the_assemble_path` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_one_in_node_retry_replaces_the_old_final_only_after_verification_and_leaves_one_pending_approval` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_the_assemble_attempt_emits_no_model_facts` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_local_nodes.AssembleDryRun::test_without_a_decision_layer_the_same_ticket_is_one_dispatch_and_idempotent` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_frame_failure_is_not_swallowed_so_the_attempt_ends_with_node_error_and_recovery_is_allowed` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_real_step_success_is_verified_by_the_attempt_session_not_failed_too_early` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_the_failed_attempt_may_edit_frame_progress_without_invalidating_the_baseline` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.test_media_tranche_local::test_assemble_dry_run_with_the_real_verifier` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_silent_voice_file_fails_with_the_silent_code` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w5_d06_local_resume_test::test_audio_finished_file_is_resume_local_and_verified` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w5g_media_nodes_route_more_script_test::test_a_real_dry_run_proves_the_video_node_offline` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w5l_assemble_real_speech_test::test_a_frame_is_taken_with_ffmpeg_at_the_requested_time` | failure | падает | нет numpy |
| `tests.w5l_assemble_real_speech_test::test_a_lower_third_band_difference_is_seen_and_noise_elsewhere_is_not` | failure | падает | нет numpy |
| `tests.w5l_assemble_real_speech_test::test_small_codec_noise_below_the_pixel_threshold_does_not_count` | failure | падает | нет numpy |
| `tests.w6_2_publish_flow_test::test_captcha_refuses_with_code_before_upload` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_caption_not_filled_refuses_before_click` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_caption_over_limit_refused_before_opening_page` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_configured_busy_selector_blocks_until_gone` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_configured_captcha_selector` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_empty_account_still_unconfirmed_and_caption_checked` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_hashtags_from_config_are_typed` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_logged_in_selector_absent_means_not_logged_in` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_marker_without_post_url_is_unconfirmed` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_missing_file_input_and_missing_button_are_selector_not_found` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_post_link_selector_gives_url_and_url_marker_without_regex` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_preflight_does_not_upload_or_click` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_processing_timeout_never_clicks` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_rejected_after_click` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_screenshot_name_has_no_secret_parts` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_success_by_configured_markers_is_confirmed_with_post_url_and_screenshot` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_title_field_filled_when_configured` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_two_factor_and_not_logged_in` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_upload_failed_marker_refuses_before_click` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_verify_post_and_feed_check` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_waits_for_processing_then_enables_button` | error | падает | нет Chromium |
| `tests.w6_2_publish_flow_test::test_without_markers_unconfirmed_even_though_clicked` | error | падает | нет Chromium |
| `tests.w70_media_checks_test.SoftMediaChecks::test_all_black_clip_is_soft` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_aspect_mismatch_is_soft_not_failed` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_much_shorter_than_scenes_is_soft` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.w70_media_checks_test.SoftMediaChecks::test_silent_music_is_soft` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |

## тесты из #342 без нужного кода/фикстур

| Имя | Вид | Сейчас | Почему эта группа |
|---|---|---|---|
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | failure | падает | статус human_review вместо verified, так же на #342 |
| `tests.observe_v1.test_media_dry_run_music.MusicDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | failure | падает | статус human_review вместо verified, так же на #342 |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_kill_switch_refuses_before_any_ticket_or_request` | failure | падает | проба медиа даёт ValueError или пустой ffprobe |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent` | failure | падает | статус human_review вместо verified, так же на #342 |
| `tests.observe_v1.test_media_dry_run_videos.VideosDryRun::test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved` | failure | падает | статус human_review вместо verified, так же на #342 |
| `tests.test_media_tranche_local::test_approve_final_barrier_cannot_be_bypassed_by_auto_mode_or_a_tampered_file` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.OtherMediaNodes::test_assemble_final_digest_and_the_approval_barrier_are_reported` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.OtherMediaNodes::test_audio_counts_and_the_uncertain_marker` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.OtherMediaNodes::test_music` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.VideosVerification::test_good_clips_pass_and_the_provider_trail_is_recorded` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.VideosVerification::test_unknown_submit_outcomes_are_listed_per_provider_and_do_not_fake_a_failure` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.test_media_verification.VideosVerification::test_unreadable_ledger_is_a_fact_and_provenance_says_so` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w40_test_infra_test::test_wait_returns_when_the_holder_finishes` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w4_2_c03_images_verification_test.AudioContentCheckIsWired::test_a_voice_file_with_a_signal_passes` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5_d06_local_resume_test::test_sfx_event_files_on_record_are_resume_local_and_verified` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_dry_run_of_one_node_runs_only_that_node` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5f_elevenlabs_nodes_script_test::test_a_real_dry_run_proves_both_nodes_offline` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5g_media_nodes_route_more_script_test::test_the_fake_outsee_plays_image_and_video_submit_status_and_download_and_counts` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5h_assemble_local_script_test::test_a_real_dry_run_reassembles_once_under_the_ticket_and_leaves_the_approval_to_the_human` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5h_assemble_local_script_test::test_the_made_clip_and_voice_decode_and_have_the_expected_length` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w5k_media_resume_poll_script_test::test_a_real_dry_run_resumes_the_known_task_and_pays_nothing` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_audible_music_passes` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_auto_approve_of_final_is_blocked_by_soft_issue` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_clip_with_matching_or_unset_aspect_passes` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
| `tests.w70_media_checks_test.SoftMediaChecks::test_final_matching_scenes_passes` | failure | падает | то же утверждение, что в junit #342; кода или фикстуры под него нет |
