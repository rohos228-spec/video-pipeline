"""Каталог kie.ai для Create: цены, валидация, сборка payload."""

from __future__ import annotations

from app.services import kie_catalog as kc


def test_catalog_covers_categories() -> None:
    cat = kc.catalog_for_ui()
    cats = {c["id"] for c in cat["categories"]}
    assert {"video", "image", "music", "sound", "voice", "tools"} <= cats
    by_cat = {c: [] for c in cats}
    for m in cat["models"]:
        by_cat[m["category"]].append(m["id"])
    assert len(by_cat["video"]) >= 15
    assert len(by_cat["image"]) >= 10
    assert "suno-music" in by_cat["music"]
    assert "suno-sounds" in by_cat["sound"]
    assert "elevenlabs-sfx" in by_cat["sound"]
    assert "elevenlabs-tts-turbo" in by_cat["voice"]
    assert "topaz-video-upscale" in by_cat["tools"]
    # у каждой модели есть цена по умолчанию и поля
    for m in cat["models"]:
        assert m["fields"], m["id"]
        assert (m["pricing"].get("default") or 0) > 0, m["id"]


def test_veo_price_by_mode_and_resolution() -> None:
    veo = kc.get_model("veo-3-1")
    assert veo is not None
    lite = kc.estimate_credits(veo, {"model": "veo3_lite", "resolution": "720p"})
    assert lite["credits"] == 30 and lite["usd"] == 0.15
    fast4k = kc.estimate_credits(veo, {"model": "veo3_fast", "resolution": "4k"})
    assert fast4k["credits"] == 180
    quality = kc.estimate_credits(veo, {"model": "veo3", "resolution": "1080p"})
    assert quality["credits"] == 255


def test_seedance_price_per_second_and_video_input() -> None:
    sd = kc.get_model("seedance-2-5")
    assert sd is not None
    plain = kc.estimate_credits(sd, {"resolution": "720p", "duration": 10})
    assert plain["credits"] == 63 * 10
    with_video = kc.estimate_credits(
        sd,
        {
            "resolution": "720p",
            "duration": 10,
            "reference_video_urls": ["https://x/v.mp4"],
        },
    )
    assert with_video["credits"] == 38 * 10
    assert with_video["usd"] < plain["usd"]


def test_tts_price_per_1k_chars() -> None:
    tts = kc.get_model("elevenlabs-tts-turbo")
    assert tts is not None
    assert kc.estimate_credits(tts, {"text": "x" * 999})["credits"] == 6
    assert kc.estimate_credits(tts, {"text": "x" * 2500})["credits"] == 18


def test_suno_flat_price() -> None:
    suno = kc.get_model("suno-music")
    assert suno is not None
    est = kc.estimate_credits(suno, {"prompt": "phonk"})
    assert est["credits"] == 12 and est["usd"] == 0.06


def test_suno_sounds_payload_sends_v5_omits_any_key() -> None:
    spec = kc.get_model("suno-sounds")
    assert spec is not None
    body = kc.build_payload(spec, {"prompt": "удар по металлу"})
    assert body["prompt"] == "удар по металлу"
    assert body["model"] == "V5"
    assert "soundKey" not in body
    assert body.get("soundLoop") is False
    with_key = kc.build_payload(spec, {"prompt": "whoosh", "soundKey": "Dm"})
    assert with_key["soundKey"] == "Dm"
    assert with_key["model"] == "V5"
    errs = kc.validate_values(spec, {"prompt": "x" * 501})
    assert any("max 500" in e for e in errs)


def test_elevenlabs_sfx_routes_to_suno_sounds() -> None:
    spec = kc.get_model("elevenlabs-sfx")
    assert spec is not None
    assert spec["api"] == "suno"
    assert spec["endpoint"] == "/api/v1/generate/sounds"
    body = kc.build_payload(spec, {"prompt": "разбитое стекло"})
    assert body["model"] == "V5"
    assert "input" not in body
    assert body["prompt"].startswith("Foley sound effect only: разбитое стекло")
    assert "no vocals" in body["prompt"]
    assert "duration_seconds" not in body
    with_dur = kc.build_payload(spec, {"prompt": "whoosh", "duration_seconds": 3})
    assert "Duration about 3" in with_dur["prompt"]
    assert "duration_seconds" not in with_dur


def test_validate_required_and_enum() -> None:
    sd = kc.get_model("seedance-2-5")
    assert sd is not None
    errs = kc.validate_values(sd, {"resolution": "999p"})
    assert any("вне списка" in e for e in errs)
    assert any("Промпт" in e for e in errs)
    ok = kc.validate_values(sd, {"prompt": "тест", "resolution": "720p", "duration": 5})
    assert ok == []


def test_validate_max_items() -> None:
    veo = kc.get_model("veo-3-1")
    assert veo is not None
    errs = kc.validate_values(
        veo,
        {
            "prompt": "x",
            "imageUrls": ["https://x/1.png"] * 4,
        },
    )
    assert any("imageUrls" in e for e in errs)


def test_build_payload_jobs_and_i2i_switch() -> None:
    img = kc.get_model("gpt-image-2")
    assert img is not None
    t2i = kc.build_payload(img, {"prompt": "cat"})
    assert t2i["model"] == "gpt-image-2-text-to-image"
    i2i = kc.build_payload(img, {"prompt": "cat", "image_urls": ["https://x/a.png"]})
    assert i2i["model"] == "gpt-image-2-image-to-image"
    assert i2i["input"]["image_urls"] == ["https://x/a.png"]
    # дефолты подставляются
    assert i2i["input"]["resolution"] == "1K"


def test_build_payload_model_map_variant() -> None:
    im = kc.get_model("imagen4")
    assert im is not None
    ultra = kc.build_payload(im, {"prompt": "x", "variant": "ultra"})
    assert ultra["model"] == "google/imagen4-ultra"


def test_build_payload_veo_duration_int() -> None:
    veo = kc.get_model("veo-3-1")
    body = kc.build_payload(veo, {"prompt": "dog", "duration": "8"})
    assert body["duration"] == 8
    assert body["model"] == "veo3_lite"  # дефолт


def test_build_payload_dialogue_lines() -> None:
    dlg = kc.get_model("elevenlabs-dialogue-v3")
    assert dlg is not None
    body = kc.build_payload(
        dlg, {"dialogue": "Rachel | Привет\nAdam | И тебе привет"}
    )
    items = body["input"]["dialogue"]
    assert items == [
        {"voice": "Rachel", "text": "Привет"},
        {"voice": "Adam", "text": "И тебе привет"},
    ]


def test_single_url_fields_become_string() -> None:
    h = kc.get_model("hailuo-2-3-i2v")
    assert h is not None
    body = kc.build_payload(
        h, {"prompt": "x", "image_url": ["https://x/a.png"], "duration": "6"}
    )
    assert body["input"]["image_url"] == "https://x/a.png"
