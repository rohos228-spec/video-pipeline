"""W5-5 / D15: публикация как действие слоя уровня destructive. Сети, браузера и платформ нет.

Контекст исполнения - заглушка с проектом в памяти; публикаторы не запускаются (executor вызывает только
эндпоинт запуска шага, он тоже подменён).
"""
from __future__ import annotations

import pytest

from app.services.action_layer import planner_rules, publish_action
from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.plan import Plan, build_preview
from app.services.action_layer.spec import RISK_DESTRUCTIVE, ActionRegistry
from app.settings import settings

PLATFORMS = ["tiktok", "yt_shorts", "ig_reels", "vk_clips", "likee"]
CAPTION = "Утро в кофейне"


class FakeCtx(ActionContext):
    def __init__(self, project: dict, calls: list | None = None, read_only: bool = False) -> None:
        super().__init__(app=None, operator="op", read_only=read_only)
        self.project = project
        self.calls = calls if calls is not None else []

    async def call(self, method, path, **kw):
        if self.read_only and method != "GET":
            raise AssertionError(f"write in read-only context: {method} {path}")
        self.calls.append((method, path, kw.get("params")))
        if method == "GET" and path.startswith("/projects/"):
            return self.project
        return {}

    def readonly_copy(self):
        return FakeCtx(self.project, self.calls, read_only=True)


def project(**over) -> dict:
    base = {"id": 7, "topic": CAPTION, "slug": "kofe", "status": "assembled", "meta": {}}
    base.update(over)
    return base


@pytest.fixture
def reg(monkeypatch) -> ActionRegistry:
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    monkeypatch.setattr(settings, "morelogin_profile_id", "profile-abcd1234")

    async def not_managed(project_id: int) -> bool:
        return False

    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", not_managed)
    r = ActionRegistry()
    publish_action.register_publish_actions(r)
    return r


def step(**over) -> dict:
    params = {"project_id": 7, "platforms": list(PLATFORMS), "caption": CAPTION}
    params.update(over)
    return {"action_id": "publish.send", "params": params}


async def preview(reg, ctx, **over) -> dict:
    return await build_preview(Plan.from_json([step(**over)], max_steps=5), reg, ctx)


def test_spec_is_destructive_irreversible_and_needs_approval(reg) -> None:
    spec = reg.get("publish.send")
    assert spec.risk == RISK_DESTRUCTIVE and spec.destructive and not spec.reversible
    assert spec.needs_approval and not spec.idempotent and spec.is_job
    assert set(spec.params_schema["required"]) == {"project_id", "platforms", "caption"}
    assert spec.params_schema["properties"]["platforms"]["items"]["enum"] == PLATFORMS
    assert reg.validate_params("publish.send", step()["params"]) == []
    assert reg.validate_params("publish.send", step(platforms=["myspace"])["params"])
    assert reg.validate_params("publish.send", step(caption="")["params"])
    assert reg.validate_params("publish.send", {**step()["params"], "agent": True})


async def test_preview_names_platforms_account_and_caption_verbatim(reg) -> None:
    out = await preview(reg, FakeCtx(project()))
    assert out["ok"] is True and out["needs_approval"] is True and out["risk_max"] == RISK_DESTRUCTIVE
    effect = out["destructive_effects"][0]["text"]
    for name in ("TikTok", "YouTube Shorts", "Instagram Reels", "VK Клипы", "Likee"):
        assert name in effect
    assert f"«{CAPTION}»" in effect  # подпись дословно
    assert "MoreLogin …1234" in effect and "profile-abcd1234" not in effect  # аккаунт без полного id
    assert "нельзя отозвать" in effect and "автоповтора не будет" in effect
    assert out["steps"][0]["destructive"] is True and out["steps"][0]["spends_money"] is False


async def test_preview_never_publishes(reg) -> None:
    ctx = FakeCtx(project())
    await preview(reg, ctx)
    assert [c for c in ctx.calls if c[0] != "GET"] == []


async def test_managed_agent_mode_is_refused(reg, monkeypatch) -> None:
    async def managed(project_id: int) -> bool:
        return True

    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", managed)
    out = await preview(reg, FakeCtx(project()))
    assert out["ok"] is False
    reasons = [f["reason"] for f in out["steps"][0]["precondition_failures"]]
    assert any("агентским" in r and "только человек" in r for r in reasons)
    ctx = FakeCtx(project())
    with pytest.raises(ActionCallError) as err:
        await reg.get("publish.send").executor(ctx, step()["params"])
    assert err.value.status == 409 and "not_managed" in str(err.value.detail)
    assert [c for c in ctx.calls if c[0] == "POST"] == []  # запуска шага не было


async def test_managed_check_failure_is_fail_closed(reg, monkeypatch) -> None:
    async def broken(project_id: int) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", broken)
    out = await preview(reg, FakeCtx(project()))
    assert out["ok"] is False
    assert any("не удалось проверить" in f["reason"] for f in out["steps"][0]["precondition_failures"])


@pytest.mark.parametrize("over,needle", [
    ({"status": "publishing"}, "статус publishing"),
    ({"status": "new"}, "assembled"),
    ({"topic": "Другая тема"}, "не совпадает с темой"),
])
async def test_preconditions_block_wrong_state(reg, over, needle) -> None:
    out = await preview(reg, FakeCtx(project(**over)))
    assert out["ok"] is False
    assert needle in " ".join(f["reason"] for f in out["steps"][0]["precondition_failures"])


async def test_disabled_publication_is_not_pretended_to_work(reg, monkeypatch) -> None:
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    out = await preview(reg, FakeCtx(project()))
    assert out["ok"] is False
    assert any("SOCIAL_PUBLISH_ENABLED=false" in f["reason"] for f in out["steps"][0]["precondition_failures"])


async def test_platform_subset_is_refused_with_the_real_list(reg) -> None:
    out = await preview(reg, FakeCtx(project()), platforms=["tiktok"])
    assert out["ok"] is False
    reason = " ".join(f["reason"] for f in out["steps"][0]["precondition_failures"])
    assert "все площадки" in reason and "yt_shorts" in reason


async def test_already_published_platforms_are_left_out_of_the_expected_list(reg) -> None:
    meta = {"published_platforms": {"tiktok": "https://t/1"}}
    ctx = FakeCtx(project(meta=meta))
    assert (await preview(reg, ctx))["ok"] is False  # tiktok уже есть: его в списке быть не должно
    ok = await preview(reg, ctx, platforms=[p for p in PLATFORMS if p != "tiktok"])
    assert ok["ok"] is True and "TikTok" not in ok["destructive_effects"][0]["text"]


async def test_plan_hash_covers_platforms_and_caption(reg) -> None:
    ctx = FakeCtx(project())
    a = (await preview(reg, ctx))["plan_hash"]
    b = (await preview(reg, FakeCtx(project(topic="Иначе")), caption="Иначе"))["plan_hash"]
    c = (await preview(reg, ctx, platforms=PLATFORMS[:2]))["plan_hash"]
    assert len({a, b, c}) == 3


async def test_executor_starts_the_existing_publish_step_only(reg) -> None:
    ctx = FakeCtx(project())
    out = await reg.get("publish.send").executor(ctx, step()["params"])
    posts = [c for c in ctx.calls if c[0] == "POST"]
    assert posts == [("POST", "/projects/7/steps/publish/run", {"mode": "resume"})]
    assert out == {"started": True, "platforms": PLATFORMS}


async def test_poll_reports_honestly(reg) -> None:
    spec = reg.get("publish.send")
    params = step()["params"]
    running = await spec.poll(FakeCtx(project(status="publishing")), params, {})
    assert running.done is False
    meta = {"published_platforms": {p: f"u/{p}" for p in PLATFORMS}}
    done = await spec.poll(FakeCtx(project(status="published", meta=meta)), params, {})
    assert done.done and done.ok and done.result["published"] == sorted(PLATFORMS)
    partial = {"published_platforms": {"tiktok": "u"}, "publish_unconfirmed": {"likee": ""}}
    stuck = await spec.poll(FakeCtx(project(status="assembled", meta=partial)), params, {})
    assert stuck.done and not stuck.ok and "вручную" in stuck.error
    assert stuck.result["unconfirmed"] == ["likee"]


def test_planner_knows_the_action_but_guards_it() -> None:
    assert "publish.send" in planner_rules.GUARD_HINTS
    r = ActionRegistry()
    publish_action.register_publish_actions(r)
    spec = r.get("publish.send")
    assert planner_rules.is_guarded(spec) and planner_rules.unguarded_risky(r) == []
    assert planner_rules.request_hints_action("опубликуй ролик на все площадки", spec)
    assert planner_rules.request_hints_action("выложи готовое видео", spec)
    assert not planner_rules.request_hints_action("покажи доску", spec)
    kept, dropped = planner_rules.drop_unrequested(
        [{"action_id": "publish.send", "params": {}, "depends_on": []}], "покажи доску монтажа", r)
    assert kept == [] and dropped == [spec.title_ru]
