"""FFmpeg-цепочки для короткого аудио: зациклить под длину ролика + fade-out в конце."""


def loop_trim_fade_expr(
    *,
    total_duration: float,
    fade_out_sec: float = 3.0,
    volume_db: float | None = None,
) -> str:
    """Фильтр для одной аудиодорожки (без меток входа/выхода).

    Короче ролика → aloop с начала; длиннее → обрезка atrim.
    В конце ролика — линейное затухание ``fade_out_sec`` секунд.
    """
    total_duration = max(total_duration, 0.01)
    fade_out_sec = min(max(fade_out_sec, 0.0), total_duration)
    fade_start = max(0.0, total_duration - fade_out_sec)
    parts: list[str] = []
    if volume_db is not None:
        parts.append(f"volume={volume_db}dB")
    parts.extend([
        "aloop=loop=-1:size=2e+09",
        f"atrim=0:{total_duration:.3f}",
        "asetpts=PTS-STARTPTS",
        f"afade=t=out:st={fade_start:.3f}:d={fade_out_sec:.3f}",
    ])
    return ",".join(parts)


def labeled_loop_trim_fade(
    input_label: str,
    output_label: str,
    *,
    total_duration: float,
    fade_out_sec: float = 3.0,
    volume_db: float | None = None,
) -> str:
    """Фрагмент filter_complex: ``[input_label]…[output_label]``."""
    chain = loop_trim_fade_expr(
        total_duration=total_duration,
        fade_out_sec=fade_out_sec,
        volume_db=volume_db,
    )
    return f"[{input_label}]{chain}[{output_label}]"
