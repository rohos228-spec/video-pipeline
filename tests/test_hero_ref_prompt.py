from app.generation_options import OUTSEE_PROMPT_TARGET_BODY_CHARS
from app.services.hero_ref_prompt import fit_prompt_for_outsee, rewrite_hero_ref_prompt


def test_two_refs_lock_maps_each_id() -> None:
    out = rewrite_hero_ref_prompt(
        "Reference: character sheet for c02 and c03 — identity lock "
        "for this ONE person only. Exactly one body of this id — "
        "no twins, no clones, no duplicate face.\n\n"
        "Negative: twins, clones, duplicate identical faces, "
        "mirrored double of same character.",
        ["c02", "c03"],
    )
    assert out.startswith("HARD CAST LOCK:")
    assert "1 ref=1 character" in out
    assert "Use EVERY attached reference" in out
    assert "Do not change look from the reference" in out
    assert "Image 1=c02" in out
    assert "Image 2=c03" in out
    assert "Using only one reference = fail" in out
    assert "ONE person only" not in out
    assert "duplicate identical faces" not in out
    again = rewrite_hero_ref_prompt(out, ["c02", "c03"])
    assert again.count("HARD CAST LOCK:") == 1
    assert again.count("ты обязан указать с каждого референса") == 1


def test_child_keeps_parent_still_lock() -> None:
    raw = (
        "Image 1 is the previous coverage still of the SAME scene.\n\n"
        "Negative: twins, clones, duplicate identical faces, "
        "mirrored double of same character."
    )
    out = rewrite_hero_ref_prompt(raw, ["c02", "c03"], child=True)
    assert out.startswith("Image 1 is the previous coverage still")
    assert "duplicate identical faces" not in out


def test_fit_keeps_lock_and_style_under_limit() -> None:
    scene = "HARD CAST LOCK: keep me.\n\n" + ("visual detail word " * 400)
    style = "STYLE: trash polka lock here. " + ("style word " * 200)
    raw = f"{scene}\n\n{style}"
    assert len(raw) > OUTSEE_PROMPT_TARGET_BODY_CHARS
    out = fit_prompt_for_outsee(raw)
    assert len(out) <= OUTSEE_PROMPT_TARGET_BODY_CHARS
    assert out.startswith("HARD CAST LOCK:")
    assert "STYLE: trash polka lock here." in out
