"""Пять вариантов монтажа — пробуем по очереди."""

MONTAGE_VARIANTS = """
ВАРИАНТ 1 — CONCAT+GAP (отключён)
  Excel R15 → gap + clip → concat. Проблемы с длиной concat на Windows.

ВАРИАНТ 2 — OVERLAY (текущий, реализован)
  Одно видео color=black на всю длительность озвучки.
  filter_complex: overlay каждого clip с enable='between(t,start,end)'.
  Плюсы: математически точные секунды без concat drift.
  Минусы: тяжёлый filter_complex при 144 клипах.

ВАРИАНТ 3 — SLOT-ФАЙЛЫ
  Для каждого кадра: slot_NNN.mp4 = gap+clip ровно [start,end] на шкале.
  Потом concat без логики gap внутри.
  Плюсы: каждый слот проверяется ffprobe отдельно.
  Минусы: 144+ файлов на диске.

ВАРИАНТ 4 — EDL / JSON TIMELINE
  Генерируем montage.edl из R15, один ffmpeg-script читает EDL.
  Плюсы: стандарт монтажа, можно открыть в NLE.
  Минусы: нужен надёжный парсер EDL→ffmpeg.

ВАРИАНТ 5 — DISK-ONLY INDEX
  Игнор БД/artifacts: только project.xlsx R15 + videos/clip_NNN_*.mp4.
  Номер кадра = номер файла, без shot2/mapper/ASR.
  Плюсы: ноль скрытых подмен.
  Минусы: нет fallback на artifact paths.
""".strip()
