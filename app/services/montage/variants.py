"""Пять вариантов монтажа — пробуем по очереди."""

MONTAGE_VARIANTS = """
ВАРИАНТ 1 — CONCAT+GAP (отключён)
  Excel R15 → gap + clip → concat. Проблемы с длиной concat на Windows.

ВАРИANT 2 — OVERLAY (legacy, медленный)
  Одно видео color=black на всю длительность озвучки.
  filter_complex: overlay каждого clip с setpts offset на start_s из R15.
  Минусы: ~20 мин на 140+ клипов — каждый batch перекодирует всю шкалу.

ВАРИАНТ 3 — SLOT+CONCAT (текущий, реализован)
  Параллельное кодирование каждого R15-слота, concat, mux.
  Абсолютная R15 (таймкод = приоритет): out_end = start следующего;
  src короче → clone в окне; gap → clone suffix; src длиннее → trim на 1x.

ВАРИАНТ 4 — EDL / JSON TIMELINE
  Генерируем montage.edl из R15, один ffmpeg-script читает EDL.

ВАРИАНТ 5 — DISK-ONLY INDEX
  Игнор БД/artifacts: только project.xlsx R15 + videos/clip_NNN_*.mp4.
""".strip()
