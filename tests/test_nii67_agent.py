from app.services import nii67_agent as ag
from app.services.nii67_card import default_card


def monster_card():
    c = default_card()
    c["gg"]["name"] = "Миша"
    c["intro"]["text"] = "Миша в тёмной комнате видит две светящиеся двери"
    for d, theme, name in ((0, "Конфетный лес", "Жвачкин"), (1, "Подводный город", "Пузырь")):
        door = c["doors"][d]
        door["theme"] = theme
        for b in door["blocks"]:
            if b["kind"] == "world_character":
                b["character"]["enabled"] = True
                b["character"]["name"] = name
        door["blocks"][4]["text"] = "угощает монстрика"
    c["finale"]["standard"] = "Миша возвращается с трофеями обоих миров"
    return c


def test_parser_slots_and_toggle():
    slots = ag.parse_slots(monster_card())
    assert slots[0]["slot_id"] == "intro" and slots[-1]["slot_id"] == "finale"
    assert {s["gg"] for s in slots} == {"Миша"}
    off = default_card()
    assert not any(s["creature"] for s in ag.parse_slots(off))
    assert not any(s["type"] == "world_character" for s in ag.parse_slots(off))


def test_contract_valid_and_text_woven():
    res = ag.generate(monster_card())
    assert res["errors"] == []
    for f in res["frames"]:
        assert ag.validate_frame(f) == []
    assert any("угощает монстрика" in f["action"] for f in res["frames"])


def test_contract_rejects_bad():
    assert "bad_camera" in ag.validate_frame({"camera": "drone", "camera_feel": "x", "refs": [{"type": "z"}]})


def test_scene_validator_and_rewrite_loop():
    f = ag.build_scene(ag.parse_slots(default_card())[0])
    f["scene"]["hook"] = ""
    f["end_frame"] = f["start_frame"]
    errs = ag.validate_scene(f)
    assert "empty:hook" in errs and "no_change" in errs
    fixed = ag._auto_rewrite(f, errs)
    assert ag.validate_scene(fixed) == []


def test_no_double_close_and_feels():
    frames = [{"slot_id": str(i), "door": "door1", "camera": "close", "camera_feel": "handheld_intimate"} for i in range(3)]
    ag.fix_camera_sequence(frames)
    assert ag.camera_errors(frames) == []
    assert ag.feel_for("far", "entry", "x") == "handheld_soft"
    assert ag.feel_for("mid", "escape", "x") == "handheld_action"
    assert ag.feel_for("close", "gift", "x") == "handheld_intimate"


def test_duration_60s():
    res = ag.generate(monster_card())
    fr = res["frames"]
    shared = sum(f["duration_hint"] for f in fr if f["door"] is None)
    d1 = sum(f["duration_hint"] for f in fr if f["door"] == "door1")
    assert abs(shared + d1 - 60) < 1


def test_new_structure_intro_doors_illustration():
    c = monster_card()
    c["intro"]["video_name"] = "intro__src.mp4"
    c["portal"].update(image_name="doors.png", video_name="morph.mp4",
                       left_design="конфетная арка", right_design="иллюминатор",
                       left_anim="тает карамелью", right_anim="наполняется водой")
    for door in c["doors"]:
        for b in door["blocks"]:
            if b["kind"] == "exit":
                b["text"] = "Миша застрял в карамели и машет рукой"
    res = ag.generate(c)
    assert res["errors"] == []
    ids = [f["slot_id"] for f in res["frames"]]
    assert ids[0] == "intro" and ids[1] == "doors" and ids[-1] == "finale"
    assert not any(".theme" in i or "exit_" in i or "room_after" in i for i in ids)
    intro, doors = res["frames"][0], res["frames"][1]
    assert any(r.get("role") == "source_video" and r["attached"] for r in intro["refs"])
    roles = {r.get("role"): r["attached"] for r in doors["refs"]}
    assert roles["door_image"] and roles["transformation_video"]
    assert "конфетная арка" in doors["action"] and "иллюминатор" in doors["action"]
    ill = [f for f in res["frames"] if f["slot_id"].endswith(".illustration")]
    assert len(ill) == 2
    assert all("карамели" in f["end_frame"] for f in ill)
    assert all(any(r.get("role") == "illustration" for r in f["refs"]) for f in ill)
