from app.services.audio_filters import labeled_loop_trim_fade, loop_trim_fade_expr


def test_loop_trim_fade_contains_aloop_and_afade():
    s = loop_trim_fade_expr(total_duration=60.0, fade_out_sec=3.0, volume_db=-17.0)
    assert "aloop" in s
    assert "atrim=0:60.000" in s
    assert "afade=t=out:st=57.000:d=3.000" in s
    assert "volume=-17.0dB" in s


def test_labeled_chain():
    s = labeled_loop_trim_fade("2:a", "bgm", total_duration=10.0, fade_out_sec=3.0)
    assert s.startswith("[2:a]")
    assert s.endswith("[bgm]")
