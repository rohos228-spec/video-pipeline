"""Pytest не импортирует Windows-only скрипт: на Linux он делает SystemExit при загрузке."""

collect_ignore = ["test_observe_windows_files.py"]
