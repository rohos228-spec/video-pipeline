from app.services.plan_timestamps import (
    format_timecode,
    format_timecode_range,
    parse_timecode_range,
)


def test_format_timecode_with_hundredths() -> None:
    assert format_timecode(0) == "0:00.00"
    assert format_timecode(3.28) == "0:03.28"
    assert format_timecode(157.5) == "2:37.50"
    assert format_timecode(551.67) == "9:11.67"


def test_format_range_preserves_hundredths() -> None:
    label = format_timecode_range(3.28, 5.76)
    assert label == "0:03.28-0:05.76"
    parsed = parse_timecode_range(label)
    assert parsed is not None
    assert abs(parsed[0] - 3.28) < 0.001
    assert abs(parsed[1] - 5.76) < 0.001


def test_parse_legacy_whole_seconds_still_works() -> None:
    assert parse_timecode_range("0:00-2:37") == (0.0, 157.0)


def test_parse_unicode_dash() -> None:
    from app.services.plan_timestamps import normalize_timestamp_label, parse_timecode_range

    assert parse_timecode_range("0:03.28–0:05.76") == (3.28, 5.76)
    assert normalize_timestamp_label(" 0:03.28 — 0:05.76 ") == "0:03.28-0:05.76"


def test_parse_rejects_collapsed_range() -> None:
    assert parse_timecode_range("0:03.28-0:03.28") is None
    assert parse_timecode_range("bad") is None
