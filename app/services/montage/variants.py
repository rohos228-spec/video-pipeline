"""Пять вариантов монтажа — пробуем по очереди."""

MONTAGE_VARIANTS = """
ВАРИАНТ 1 — CONCAT+GAP (отключён)
  Excel R15 → gap + clip → concat. Проблемы с длиной concat на Windows.

ВАРИANT 2 — OVERLAY (текущий, реализован)
  Одно видео color=black на всю длительность озвучки.
  filter_complex: overlay каждого clip с setpts offset на start_s из R15.
  Плюсы: математически точные секунды без concat drift.
  Минусы: тяжёлый filter_complex при 144+ клипах.

ВАРИАНТ 3 — SLOT-ФАЙЛЫ
  Для каждого кадра: slot_NNN.mp4 = gap+clip ровно [start,end] на шкале.
  Потом concat без логики gap внутри.

ВАРИАНТ 4 — EDL / JSON TIMELINE
  Генерируем montage.edl из R15, один ffmpeg-script читает EDL.

ВАРИАНТ 5 — DISK-ONLY INDEX
  Игнор БД/artifacts: только project.xlsx R15 + videos/clip_NNN_*.mp4.
""".strip()
