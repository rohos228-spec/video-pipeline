"""Пять вариантов монтажа — пробуем по очереди."""

MONTAGE_VARIANTS = """
ВАРИАНТ 1 — CONCAT+GAP (отключён)
  Excel R15 → gap + clip → concat. Проблемы с длиной concat на Windows.

ВАРИANT 2 — OVERLAY (legacy, медленный)
  Одно видео color=black на всю длительность озвучки.
  filter_complex: overlay каждого clip с setpts offset на start_s из R15.
  Минусы: ~20 мин на 140+ клипов — каждый batch перекодирует всю шкалу.

ВАРИАНТ 3 — OVERLAY+EXTEND (текущий, реализован в variant2.py)
  Чёрное полотно voice_s; каждый клип overlay на start_s (setpts).
  display до start следующего; src короче / gap → clone; без slow-mo.

ВАРИАНТ 4 — EDL / JSON TIMELINE
  Генерируем montage.edl из R15, один ffmpeg-script читает EDL.

ВАРИАНТ 5 — DISK-ONLY INDEX
  Игнор БД/artifacts: только project.xlsx R15 + videos/clip_NNN_*.mp4.
""".strip()
