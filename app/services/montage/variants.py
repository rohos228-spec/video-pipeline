"""Пять вариантов монтажа — пробуем по очереди."""

MONTAGE_VARIANTS = """
ВАРИАНТ 1 — CONCAT+GAP (отключён)
  Excel R15 → gap + clip → concat. Проблемы с длиной concat на Windows.

ВАРИANT 2 — OVERLAY (legacy, медленный)
  Одно видео color=black на всю длительность озвучки.
  filter_complex: overlay каждого clip с setpts offset на start_s из R15.
  Минусы: ~20 мин на 140+ клипов — каждый batch перекодирует всю шкалу.

ВАРИАНТ 3 — SLOT+CONCAT (текущий, реализован)
  gap (чёрный) + clip на каждый R15-слот, параллельное кодирование, concat copy.
  Плюсы: ~5–8 мин, те же абсолютные метки R15, pre_mux backup перед mux.

ВАРИАНТ 4 — EDL / JSON TIMELINE
  Генерируем montage.edl из R15, один ffmpeg-script читает EDL.

ВАРИАНТ 5 — DISK-ONLY INDEX
  Игнор БД/artifacts: только project.xlsx R15 + videos/clip_NNN_*.mp4.
""".strip()
