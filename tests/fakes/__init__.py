"""Общие фейки для эмуляции пайплайна (T19).

FakeGptClient — duck-сurfacе ApiGptClient со скриптованными ответами
по шагам (plan/script/split/img_pr/anim_pr) + хаос-хуки.
fake_run_operator_api — excel_gpt/operator boundary: читает db_frames.json
из вложений и отдаёт apply-ops на ВСЕ uuid (N/N) или заданный хаос.
fake media — generate_image/video_with_retries, пишущие реальные tiny
PNG/MP4 в out_path.
"""
