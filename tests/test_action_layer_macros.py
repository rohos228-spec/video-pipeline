"""Макросы: проверка, подстановка, хранение, видимость, недоступные действия."""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.services.action_layer import macros
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env, make_spec  # noqa: F401

SCHEMA = {"type": "object", "properties": {
    "project_id": {"type": "integer", "minimum": 1}, "n": {"type": "integer", "minimum": 0},
    "label": {"type": "string", "maxLength": 20}},
    "required": ["project_id"], "additionalProperties": False}
PARAMS = {"type": "object", "properties": {
    "project_id": {"type": "integer", "minimum": 1}, "n": {"type": "integer", "minimum": 0},
    "note": {"type": "string"}}, "required": ["project_id"], "additionalProperties": False}


@pytest_asyncio.fixture
async def reg(env):  # noqa: F811
    ensure_builtin_actions()

    async def noop(ctx, params):
        return {}

    specs = [make_spec("zz.read", noop, schema=SCHEMA),
             make_spec("zz.paid", noop, risk="paid", spends=True, cost=(1, 2), schema=SCHEMA)]
    for s in specs:
        registry.register(s)
    yield env
    for s in specs:
        registry.unregister(s.id)


def good_steps():
    return [{"action_id": "zz.read", "params": {"project_id": "{{project_id}}"}},
            {"action_id": "zz.paid", "params": {"project_id": "{{project_id}}", "n": "{{n}}"},
             "depends_on": [0]}]


def errs(steps=None, params=PARAMS, title="Тест", require=True):
    return macros.validate_macro(title, good_steps() if steps is None else steps, params,
                                 registry, require_registered=require)


def test_valid_macro_passes() -> None:
    ensure_builtin_actions()
    # Только реальные montage.*: zz.* регистрирует фикстура.
    steps = [{"action_id": "montage.get_board", "params": {"project_id": "{{project_id}}"}}]
    assert errs(steps) == []


async def test_validation_rejects_bad_macros(reg) -> None:
    assert errs() == []
    assert any("unknown action" in e for e in errs(
        [{"action_id": "shell.exec", "params": {}}]))
    assert any("no parameter" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": 1, "evil": 1}}]))
    assert any("requires parameter" in e for e in errs(
        [{"action_id": "zz.read", "params": {"n": 1}}]))
    assert any("not in params_schema" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": "{{ghost}}"}}]))
    assert any("expects integer" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": "{{note}}"}}]))
    assert any("below" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": -1}}]))
    assert any("earlier steps" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": 1}, "depends_on": [0]}]))
    assert any("unknown fields" in e for e in errs(
        [{"action_id": "zz.read", "params": {"project_id": 1}, "approved": True}]))
    assert any("title_ru" in e for e in errs(title="  "))
    assert any("params_schema" in e for e in errs(params={"type": "object"}))
    assert errs(steps=[]) and errs(steps="x")
    many = [{"action_id": "zz.read", "params": {"project_id": 1}}] * 500
    assert any("too many" in e for e in errs(many))
    assert errs([{"action_id": "zz.read", "params": {"project_id": "{{project_id}}",
                                                     "{{k}}": 1}}])


def test_expand_substitutes_typed_values_and_omits_optional() -> None:
    steps = [{"action_id": "montage.queue_ops", "params": {
        "project_id": "{{project_id}}", "ops": [
            {"type": "image_regen", "frame_number": "{{frame}}",
             "prompt": "кадр {{frame}}: {{text}}"}]}}]
    macro = macros.MacroSpec("user.t", "t", "", tuple(steps), {
        "type": "object", "properties": {
            "project_id": {"type": "integer"}, "frame": {"type": "integer"},
            "text": {"type": "string"}, "opt": {"type": "integer"}},
        "required": ["project_id", "frame", "text"], "additionalProperties": False})
    out = macros.expand(macro, {"project_id": 5, "frame": 3, "text": "{{project_id}}"}, registry)
    params = out[0]["params"]
    assert params["project_id"] == 5 and isinstance(params["project_id"], int)
    op = params["ops"][0]
    assert op["frame_number"] == 3
    assert op["prompt"] == "кадр 3: {{project_id}}"  # значения повторно не разворачиваются
    with pytest.raises(macros.MacroError):
        macros.expand(macro, {"project_id": 5}, registry)
    with pytest.raises(macros.MacroError):
        macros.expand(macro, {"project_id": 5, "frame": "3", "text": "x"}, registry)
    with pytest.raises(macros.MacroError):
        macros.expand(macro, {"project_id": 5, "frame": 1, "text": "x", "approved": True},
                      registry)


async def test_optional_param_omitted_from_step(reg) -> None:
    macro = macros.MacroSpec("user.o", "o", "", (
        {"action_id": "zz.read", "params": {"project_id": "{{project_id}}", "n": "{{n}}"}},),
        PARAMS)
    assert macros.expand(macro, {"project_id": 1}, registry)[0]["params"] == {"project_id": 1}
    assert macros.expand(macro, {"project_id": 1, "n": 4}, registry)[0]["params"]["n"] == 4


SAMPLE = {
    "project_id": 7, "frame_id": 3, "frame_number": 2, "text": "реплика", "prompt": "ночь",
    "instruction": "добавь дождь", "max_image_ops": 4, "max_video_ops": 2,
    "image_generator": "gen_a", "image_resolution": "2k", "video_generator": "vid_a",
    "video_resolution": "1080p", "node_key": "n1"}


def test_every_builtin_macro_validates_against_real_registry() -> None:
    ensure_builtin_actions()
    assert {m.id for m in macros.BUILTIN_MACROS} >= {
        "smart.check_board", "smart.scene_from_scratch", "smart.single_frame_fix"}
    for m in macros.BUILTIN_MACROS:
        assert m.builtin
        assert macros.validate_macro(
            m.title_ru, list(m.steps), m.params_schema, registry, require_registered=True,
            description_ru=m.description_ru) == [], m.id
        info = m.to_json(registry)
        assert info["available"] is True and info["missing_action_ids"] == [], m.id
        # Полный набор параметров макроса даёт шаги, валидные по схемам действий.
        params = {k: SAMPLE[k] for k in m.params_schema["properties"]}
        for st in macros.expand(m, params, registry):
            assert registry.validate_params(st["action_id"], st["params"]) == [], (
                m.id, st["action_id"])
        # Только обязательные: необязательные поля опускаются и план всё ещё валиден.
        req = {k: SAMPLE[k] for k in m.params_schema.get("required", [])}
        for st in macros.expand(m, req, registry):
            assert registry.validate_params(st["action_id"], st["params"]) == [], (
                m.id, st["action_id"])


def test_paid_builtin_macros_expose_declared_cost_params() -> None:
    ensure_builtin_actions()
    for mid in ("smart.scene_from_scratch", "smart.single_frame_fix"):
        m = macros._BUILTIN_BY_ID[mid]
        assert "max_image_ops" in m.params_schema["required"], mid
        assert "подтверд" in m.description_ru
    assert "НЕИЗВЕСТНА" in macros._BUILTIN_BY_ID["smart.run_node_step"].description_ru
    board = macros._BUILTIN_BY_ID["smart.check_board"]
    out = macros.expand(board, {"project_id": 7}, registry)
    assert [s["action_id"] for s in out][:2] == ["montage.get_board", "montage.apply_status"]
    assert out[1]["depends_on"] == [0] and all(s["params"] == {"project_id": 7} for s in out)


def test_macro_with_unregistered_action_is_unavailable_not_a_crash() -> None:
    ensure_builtin_actions()
    macro = macros.MacroSpec("smart.future", "f", "", (
        {"action_id": "montage.future_thing_xyz", "params": {"project_id": "{{project_id}}"}},),
        PARAMS, builtin=True)
    info = macro.to_json(registry)
    assert info["available"] is False and info["missing_action_ids"] == [
        "montage.future_thing_xyz"]
    with pytest.raises(macros.MacroUnavailable) as err:
        macros.expand(macro, {"project_id": 1}, registry)
    assert err.value.missing == ["montage.future_thing_xyz"]


async def test_save_get_list_roundtrip_and_visibility(reg) -> None:
    saved = await macros.save_macro("alice", title_ru="Моя кнопка", description_ru="d",
                                    steps=good_steps(), params_schema=PARAMS)
    assert saved.id.startswith("user.") and saved.owner == "alice" and saved.shared is False
    assert (await macros.get_macro(saved.id, "alice")).steps[0]["action_id"] == "zz.read"
    with pytest.raises(LookupError):
        await macros.get_macro(saved.id, "bob")  # по умолчанию виден только владельцу
    assert saved.id in {m.id for m in await macros.list_macros("alice")}
    assert saved.id not in {m.id for m in await macros.list_macros("bob")}
    assert any(m.builtin for m in await macros.list_macros("bob"))

    shared = await macros.save_macro("alice", title_ru="Общая", description_ru="",
                                     steps=good_steps(), params_schema=PARAMS, shared=True,
                                     macro_id="user.common")
    assert (await macros.get_macro("user.common", "bob")).shared is True
    # Обновление только владельцем.
    with pytest.raises(macros.MacroForbidden):
        await macros.save_macro("bob", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS, macro_id=shared.id)
    upd = await macros.save_macro("alice", title_ru="Общая 2", description_ru="",
                                  steps=good_steps(), params_schema=PARAMS, macro_id=shared.id)
    assert upd.title_ru == "Общая 2" and upd.shared is False


async def test_save_rejects_invalid_and_unregistered(reg) -> None:
    with pytest.raises(macros.MacroError) as err:
        await macros.save_macro("alice", title_ru="x", description_ru="",
                                steps=[{"action_id": "shell.exec", "params": {}}],
                                params_schema=PARAMS)
    assert any("unknown action" in e for e in err.value.errors)
    with pytest.raises(macros.MacroError):
        await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS, macro_id="builtin.hack")
    with pytest.raises(macros.MacroError):
        await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS, macro_id="smart.check_board")


async def test_delete_rules(reg) -> None:
    m = await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS, shared=True)
    with pytest.raises(macros.MacroForbidden):
        await macros.delete_macro(m.id, "bob")
    with pytest.raises(macros.MacroForbidden):
        await macros.delete_macro("smart.check_board", "alice")
    await macros.delete_macro(m.id, "alice")
    with pytest.raises(LookupError):
        await macros.get_macro(m.id, "alice")
    with pytest.raises(LookupError):
        await macros.delete_macro(m.id, "alice")
    p = await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS)
    with pytest.raises(LookupError):
        await macros.delete_macro(p.id, "bob")  # чужой приватный: существование не раскрывается


async def test_saved_macro_becomes_unavailable_when_action_disappears(reg) -> None:
    m = await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS)
    registry.unregister("zz.paid")
    listed = {x.id: x for x in await macros.list_macros("alice")}[m.id]
    info = listed.to_json(registry, "alice")
    assert info["available"] is False and info["missing_action_ids"] == ["zz.paid"]
    assert info["mine"] is True
    with pytest.raises(macros.MacroUnavailable):
        macros.expand(listed, {"project_id": 1, "n": 1}, registry)


async def test_per_owner_limit(reg, monkeypatch) -> None:
    monkeypatch.setattr(macros, "MAX_USER_MACROS", 2)
    for _ in range(2):
        await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS)
    with pytest.raises(macros.MacroError):
        await macros.save_macro("alice", title_ru="x", description_ru="", steps=good_steps(),
                                params_schema=PARAMS)
    await macros.save_macro("bob", title_ru="x", description_ru="", steps=good_steps(),
                            params_schema=PARAMS)
