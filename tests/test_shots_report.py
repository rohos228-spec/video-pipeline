"""HTML-отчёт группы: кадры слева, промты/QC справа, без uuid/роли."""

from types import SimpleNamespace

from app.services.shots_report import build_shots_report_model, render_shots_report_html


def _frame(
    *,
    number: int,
    vo: str,
    action_chain: str = "",
    kadry: list | None = None,
    shot_id: str = "",
    scene: int = 1,
    image: str = "",
    video: str = "",
    size: str = "",
    move: str = "",
    nab: str = "",
    uuid: str = "e8ca9ac60d0e4c659e2610b4",
) -> SimpleNamespace:
    attrs = {
        "главное_действие": action_chain,
        "кадры": kadry or [],
        "camera_subdivide": {
            "shot_id": shot_id,
            "сцена": scene,
            "role": "vo_parent",
            "shot_index": 1,
            "shots_in_beat": 2,
            "крупность": size,
            "движение": move,
            "набор": nab,
        },
    }
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text=vo,
        image_prompt=image,
        animation_prompt=video,
        attrs=attrs,
        sort_key=float(number),
    )


def test_report_hides_uuid_role_shot_index() -> None:
    chain = (
        "1. архив — раскрывает папки\n"
        "(Самые странные преступления в истории)\n"
    )
    kadry = [
        {
            "id": "1-S1-K1",
            "parent_id": None,
            "порядок": 1,
            "сцена": 1,
            "шаблон": "T2",
            "план": "ОБЩИЙ",
            "ракурс": "фронт",
            "место": "архив",
            "действие": "Пыльный архив, следователь у стола.",
            "закадр": "Самые странные преступления в истории",
        }
    ]
    fr = _frame(
        number=1,
        vo="Самые странные преступления в истории",
        action_chain=chain,
        kadry=kadry,
        shot_id="1-S1-K1",
        image="wide archive still",
        video="slow push in",
        size="ОБЩИЙ",
        move="наезд",
        nab="архив",
    )
    html = render_shots_report_html(
        build_shots_report_model([fr]), slug="tester", project_id=32
    )
    assert "Пыльный архив" in html
    assert "wide archive still" in html
    assert "slow push in" in html
    assert "наезд" in html
    assert "Кадры схемы" in html
    assert "QC:" in html
    assert "картинка:" in html
    assert "e8ca9ac60d0e4c659e2610b4" not in html
    assert "vo_parent" not in html
    assert "shot_index" not in html
    assert "shots_in_beat" not in html
    assert "background:#000" in html
    assert "background:#fff" in html


def test_report_aligns_prompt_and_qc_per_shot() -> None:
    chain = (
        "1. кухня — моет пол\n(моет пол на кухне)\n"
    )
    kadry = [
        {
            "id": "1-K1",
            "parent_id": None,
            "порядок": 1,
            "сцена": 1,
            "шаблон": "T9",
            "план": "ОБЩИЙ",
            "ракурс": "фронт",
            "место": "кухня",
            "действие": "Кухня целиком.",
            "закадр": "моет",
        },
        {
            "id": "1-K2",
            "parent_id": "1-K1",
            "порядок": 2,
            "сцена": 1,
            "шаблон": "T9",
            "план": "СРЕДНИЙ",
            "ракурс": "3/4",
            "место": "кухня",
            "действие": "Руки с тряпкой.",
            "закадр": "пол на кухне",
        },
    ]
    parent = _frame(
        number=1,
        vo="моет",
        action_chain=chain,
        kadry=kadry,
        shot_id="1-K1",
        image="kitchen wide",
        video="static hold",
        size="ОБЩИЙ",
        move="статика",
        nab="кухня",
    )
    child = _frame(
        number=2,
        vo="пол на кухне",
        kadry=[],
        shot_id="1-K2",
        scene=1,
        image="hands close",
        video="wipe motion",
        size="СРЕДНИЙ",
        move="панорама",
        nab="кухня",
        uuid="bddcb068f5ee4d6293777302",
    )
    model = build_shots_report_model([parent, child])
    shots = model["scenes"][0]["shots"]
    assert len(shots) == 2
    assert shots[0]["prompts"]["картинка"] == "kitchen wide"
    assert shots[1]["prompts"]["картинка"] == "hands close"
    assert shots[1]["qc"]["движение"] == "панорама"
    html = render_shots_report_html(model, slug="x")
    assert html.find("kitchen wide") < html.find("hands close")
    assert "bddcb068f5ee4d6293777302" not in html


def test_report_keeps_every_kadry_scene() -> None:
    fr1 = _frame(
        number=1,
        vo="первая",
        action_chain="1. архив — открывает папку\n(первая)\n",
        kadry=[
            {
                "id": "1-S1-K1",
                "порядок": 1,
                "сцена": 1,
                "шаблон": "T2",
                "план": "ОБЩИЙ",
                "ракурс": "фронт",
                "место": "архив",
                "действие": "Открывает папку.",
                "закадр": "первая",
            }
        ],
        shot_id="1-S1-K1",
        image="a",
        video="va",
    )
    fr2 = _frame(
        number=3,
        vo="вторая",
        action_chain="2. кабинет — раскладывает улики\n(вторая)\n",
        kadry=[
            {
                "id": "1-S2-K1",
                "порядок": 1,
                "сцена": 2,
                "шаблон": "T5",
                "план": "СРЕДНИЙ",
                "ракурс": "3/4",
                "место": "кабинет",
                "действие": "Раскладывает улики.",
                "закадр": "вторая",
            }
        ],
        shot_id="1-S2-K1",
        scene=2,
        image="b",
        video="vb",
        uuid="aaaaaaaaaaaaaaaaaaaaaaaa",
    )
    model = build_shots_report_model([fr1, fr2])
    assert [s["n"] for s in model["scenes"]] == [1, 2]
    html = render_shots_report_html(model, slug="x")
    assert "1-S1-K1" in html
    assert "1-S2-K1" in html
    assert "Открывает папку" in html
    assert "Раскладывает улики" in html
    assert "when из таблицы" in html
    assert "Листы xlsx" in html


def test_report_drops_stale_expand_ladder() -> None:
    current = _frame(
        number=7,
        vo="Лимонный сок,",
        action_chain="3. стол следствия — выкладывает улики\n(лимонный сок)\n",
        kadry=[
            {
                "id": "1-S3-K1",
                "parent_id": None,
                "порядок": 1,
                "сцена": 3,
                "шаблон": "T9",
                "план": "ОБЩИЙ",
                "ракурс": "фронт",
                "место": "стол следствия",
                "действие": "Ставит сок.",
                "закадр": "Лимонный сок,",
            },
            {
                "id": "1-S3-K2",
                "parent_id": "1-S3-K1",
                "порядок": 2,
                "сцена": 3,
                "шаблон": "T9",
                "план": "СРЕДНИЙ",
                "ракурс": "3",
                "место": "стол следствия",
                "действие": "Двигает очки.",
                "закадр": "забытые очки,",
            },
        ],
        shot_id="1-S3-K1",
        scene=3,
        image="now",
        video="vnow",
    )
    stale = _frame(
        number=24,
        vo="Лимонный сок,",
        kadry=[
            {
                "id": "1-K7",
                "parent_id": "1-K4",
                "порядок": 1,
                "сцена": 3,
                "шаблон": "T9",
                "план": "ОБЩИЙ",
                "ракурс": "фронт",
                "место": "стол следствия",
                "действие": "Старый expand.",
                "закадр": "Лимонный сок,",
            }
        ],
        shot_id="1-K7",
        scene=3,
        image="old",
        video="vold",
        uuid="bbbbbbbbbbbbbbbbbbbbbbbb",
    )
    model = build_shots_report_model([current, stale])
    shots = model["scenes"][0]["shots"]
    ids = [s["id"] for s in shots]
    assert ids == ["1-S3-K1", "1-S3-K2"]
    html = render_shots_report_html(model, slug="x")
    assert "1-K7" not in html
    assert html.count("Лимонный сок,") == 1
