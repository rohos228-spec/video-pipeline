"""Чтение лимитов D4. Своего журнала расходов здесь нет.

Суммы уже посчитаны снаружи. Валидатор только читает:

- ``SpendLedger`` роутера (#345, ``eefc08b4``): ``spent(run_id)`` — только
  internal, ``internal_budget_usd``, ``pipeline_spent(run_id)``,
  ``read_pipeline_spent(*, project_id=None, run_id=None)``,
  ``totals(run_id=None)`` с ключами ``internal_spent_usd``,
  ``pipeline_spent_usd``, ``internal_limit_usd``, ``internal_remaining_usd``,
  ``budget_exhausted``. Списание (``charge``) не вызывается. Pipeline-scope
  в лимит $10 не входит и приватный ``_pipeline`` не читается. Если модуль
  ``app.orchestrator.routing.ledger`` импортируется, берём
  ``get_spend_ledger()``; явный ledger в context его перекрывает. Пока модуля
  нет, остаётся чтение переданного объекта (``spent`` / ``totals``).
  С ``eefc08b4`` пустой ``run_id`` у ``spent`` / ``totals`` даёт 0 и не
  блокирует, а внутренний вызов без ``run_id`` бросает ``ValueError``.
  Вне ``dry_run`` такой контекст закрывается здесь: ledger не зовётся,
  результат ``blocking`` с ``reason=missing_run_id``. Тот же ответ, если
  ledger сам бросил ``ValueError`` про ``run_id``. В ``dry_run`` чтение
  как раньше.
- spend action layer (#342) через ``SpendReader.read_totals`` или снимок
  ``context["spend"]``. WS2 принимает ключи ``*_spent_usd`` как есть.
  ``actions_spent_usd`` — лимит $40 пайплайна на run, не кредиты сессии.

Потолки плана, если источник их не передал: $200 сутки / $100 проект /
$50 запуск / $10 internal / $40 действия. Потолок ``<= 0`` выключен
(как ``caps()`` в #342). Прогноз ``context["forecast_usd"]`` прибавляется
к прочитанному и никуда не записывается.

``reason=budget_exhausted`` (числа internal: потраченное + прогноз >= $10,
или ответ роутера) всегда даёт ``passed=False``, ``blocking=True`` и
``details["reason"] == "budget_exhausted"``. Контракт WS3 (#346 ``e4c3745b``,
#350 ``19e803df``): ``blocking=True`` и ``details.reason``.
``budget_exhausted`` уводит задачу в ``waiting_approval``, ``missing_run_id``
— в ``failed``. Локальная ``validator_budget_exhausted`` проверяет
``blocking`` и ``reason == "budget_exhausted"`` и не читает contracts.
Ответ роутера: ``context["response"]`` / ``context["llm_response"]`` с
``task_status=waiting_approval`` и ``approval.reason=budget_exhausted``.
Предикат ответа для объекта совпадает с ``contracts.budget_exhausted_response``,
если эта функция есть; иначе то же условие считается здесь.
Жёсткие потолки тоже ``blocking=True``, но с другим ``reason``.
``validators.run`` остаётся sync.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.orchestrator.validators.result import is_dry_run, make_result

_NAME = "spend_guard"

CAP_DAILY_USD = 200.0
CAP_PROJECT_USD = 100.0
CAP_RUN_USD = 50.0
CAP_INTERNAL_USD = 10.0
CAP_ACTIONS_USD = 40.0

_WAITING = "waiting_approval"
_BUDGET_EXHAUSTED = "budget_exhausted"

_SPENT_KEYS = (
    "daily_spent_usd",
    "project_spent_usd",
    "run_spent_usd",
    "internal_spent_usd",
    "actions_spent_usd",
)
_CAP_DEFAULTS = {
    "daily_cap_usd": CAP_DAILY_USD,
    "project_cap_usd": CAP_PROJECT_USD,
    "run_cap_usd": CAP_RUN_USD,
    "internal_cap_usd": CAP_INTERNAL_USD,
    "actions_cap_usd": CAP_ACTIONS_USD,
}
_HARD = (
    ("actions_spent_usd", "actions_cap_usd", "actions", "actions_sub_budget"),
    ("run_spent_usd", "run_cap_usd", "run", "spend_cap_run"),
    ("project_spent_usd", "project_cap_usd", "project", "spend_cap_project"),
    ("daily_spent_usd", "daily_cap_usd", "daily", "spend_cap_daily"),
)

_reader: SpendReader | None = None


class SpendReader(Protocol):
    """Чтение spend action layer (#342). ``read_totals`` WS2 принимает как есть.

    ``read_pipeline_spent`` необязателен: pipeline-scope роутера, когда появится
    публичный геттер. Пока метода нет, адаптер его не требует.
    """

    def read_totals(
        self,
        *,
        project_id: int,
        run_id: str | None = None,
    ) -> Mapping[str, Any]:
        """USD: daily/project/run/internal/actions. ``actions_spent_usd`` — $40 на run."""


class RouterLedgerReader(Protocol):
    """Публичный срез ``SpendLedger`` (#345 ``eefc08b4``). Без ``charge``."""

    internal_budget_usd: float

    def spent(self, run_id: str | None) -> float:
        """Internal по ``run_id``. Pipeline scope сюда не входит."""

    def pipeline_spent(self, run_id: str | None) -> float:
        """Pipeline-scope. В лимит $10 не входит."""

    def read_pipeline_spent(
        self,
        *,
        project_id: int | None = None,
        run_id: str | None = None,
    ) -> float:
        """То же, что ``pipeline_spent``. ``project_id`` леджер не фильтрует."""

    def totals(self, run_id: str | None = None) -> Mapping[str, Any]:
        """internal/pipeline spent, limit, remaining и флаг ``budget_exhausted``."""


def set_spend_reader(reader: SpendReader | None) -> None:
    """Инъекция читателя action layer. Context перекрывает это значение."""

    global _reader
    _reader = reader


def get_spend_reader() -> SpendReader | None:
    return _reader


def check(project_id: int, context: dict[str, Any]) -> Any:
    """Чтение бюджета. Без ``run_id`` вне dry-run ledger не вызывается."""

    if not is_dry_run(context) and not _context_has_run_id(context):
        return _missing_run_id()
    try:
        return _checked(project_id, context)
    except ValueError as exc:
        if "run_id" in str(exc):
            return _missing_run_id()
        raise


def _checked(project_id: int, context: dict[str, Any]) -> Any:
    signal = _budget_signal(context)
    view = _load_view(project_id, context)
    view = _apply_pipeline_scope(project_id, context, view)
    if view is None and signal is None:
        return make_result(
            name=_NAME,
            passed=True,
            blocking=False,
            details={"checked": False, "reason": "no_spend_snapshot"},
        )
    forecast = _forecast(context.get("forecast_usd"))
    violations: list[dict[str, Any]] = []
    if signal is not None:
        violations.append(_signal_violation(signal, view))
    if view is not None and "internal_spent_usd" in view["known"]:
        internal_cap = view["caps"]["internal_cap_usd"]
        internal_spent = view["spent"]["internal_spent_usd"]
        internal_forecast = forecast["internal"]
        over_internal = internal_cap > 0 and internal_spent + internal_forecast >= internal_cap - 1e-9
        if over_internal or view.get("ledger_budget_exhausted"):
            violations.append(
                _violation(
                    _BUDGET_EXHAUSTED,
                    spent=internal_spent,
                    cap=internal_cap,
                    forecast=internal_forecast,
                    scope="internal",
                    source=view["internal_source"],
                )
            )
    if view is not None:
        for spent_key, cap_key, forecast_key, reason in _HARD:
            if spent_key not in view["known"]:
                continue
            cap = view["caps"][cap_key]
            spent = view["spent"][spent_key]
            extra = forecast[forecast_key]
            if cap > 0 and spent + extra > cap + 1e-9:
                violations.append(
                    _violation(
                        reason,
                        spent=spent,
                        cap=cap,
                        forecast=extra,
                        scope=forecast_key,
                        source=view["action_source"],
                    )
                )
        separate = view.get("pipeline_scope_spent_usd")
        if view.get("pipeline_separate") is not None and separate is not None:
            cap = view["caps"]["actions_cap_usd"]
            extra = forecast["actions"]
            if cap > 0 and separate + extra > cap + 1e-9:
                violations.append(
                    _violation(
                        "actions_sub_budget",
                        spent=separate,
                        cap=cap,
                        forecast=extra,
                        scope="actions",
                        source="pipeline_scope",
                    )
                )
    public = _public(view)
    if not violations:
        return make_result(
            name=_NAME,
            passed=True,
            blocking=False,
            details={
                "reason": "ok",
                "totals": public["totals"],
                "internal": public["internal"],
                "pipeline_scope_spent_usd": None if view is None else view.get("pipeline_scope_spent_usd"),
                "forecast_usd": forecast,
                "sources": public["sources"],
            },
        )
    reasons = list(dict.fromkeys(item["reason"] for item in violations))
    hard = [item for item in violations if item["reason"] != _BUDGET_EXHAUSTED]
    reason = _BUDGET_EXHAUSTED if _BUDGET_EXHAUSTED in reasons else hard[0]["reason"]
    # budget_exhausted тоже блокирует: WS3 (#351) ждёт blocking=True и этот reason.
    blocking = True
    details: dict[str, Any] = {
        "reason": reason,
        "reasons": reasons,
        "violations": violations,
        "totals": public["totals"],
        "internal": public["internal"],
        "pipeline_scope_spent_usd": None if view is None else view.get("pipeline_scope_spent_usd"),
        "forecast_usd": forecast,
        "sources": public["sources"],
    }
    if signal is not None:
        details["response"] = signal
    return make_result(name=_NAME, passed=False, blocking=blocking, details=details)


def _signal_violation(signal: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
    spent = None
    cap = None
    if view is not None and "internal_spent_usd" in view["known"]:
        spent = view["spent"]["internal_spent_usd"]
        cap = view["caps"]["internal_cap_usd"]
    payload = signal.get("payload") if isinstance(signal.get("payload"), Mapping) else {}
    if spent is None:
        spent = _num(payload.get("spent_usd"))
    if cap is None:
        raw_budget = payload.get("budget_usd")
        cap = _num(raw_budget) if raw_budget is not None else CAP_INTERNAL_USD
    return _violation(
        _BUDGET_EXHAUSTED,
        spent=spent,
        cap=cap,
        forecast=0.0,
        scope="internal",
        source="llm_response",
    )


def _violation(
    reason: str,
    *,
    spent: float,
    cap: float,
    forecast: float,
    scope: str,
    source: str,
) -> dict[str, Any]:
    remaining = None if cap <= 0 else cap - spent
    return {
        "reason": reason,
        "scope": scope,
        "source": source,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "cap_usd": cap,
        "forecast_usd": forecast,
    }


def _public(view: dict[str, Any] | None) -> dict[str, Any]:
    if view is None:
        return {"totals": {}, "internal": None, "sources": []}
    totals = {key: view["spent"][key] for key in _SPENT_KEYS if key in view["known"]}
    internal = None
    if "internal_spent_usd" in view["known"]:
        cap = view["caps"]["internal_cap_usd"]
        spent = view["spent"]["internal_spent_usd"]
        remaining = view.get("internal_remaining_usd")
        if remaining is None:
            remaining = None if cap <= 0 else cap - spent
        internal = {
            "run_id": view["run_id"],
            "spent_usd": spent,
            "budget_usd": cap,
            "remaining_usd": remaining,
            "source": view["internal_source"],
        }
    return {"totals": totals, "internal": internal, "sources": list(view["sources"])}


def _load_view(project_id: int, context: dict[str, Any]) -> dict[str, Any] | None:
    action = _action_totals(project_id, context)
    ledger = _ledger_totals(context)
    if action is None and ledger is None:
        return None
    spent: dict[str, float] = {}
    caps = dict(_CAP_DEFAULTS)
    known: set[str] = set()
    sources: list[str] = []
    action_source = ""
    internal_source = ""
    if action is not None:
        raw, action_source = action
        for key in _SPENT_KEYS:
            spent[key] = _num(raw.get(key))
            known.add(key)
        for key, default in _CAP_DEFAULTS.items():
            if key in raw and raw.get(key) is not None:
                caps[key] = _num(raw.get(key))
            else:
                caps[key] = default
        sources.append(action_source)
        internal_source = action_source
    pipeline_from_totals = False
    pipeline_spent_usd = None
    ledger_budget_exhausted = False
    internal_remaining = None
    if ledger is not None:
        spent["internal_spent_usd"] = ledger["spent_usd"]
        caps["internal_cap_usd"] = ledger["budget_usd"]
        known.add("internal_spent_usd")
        sources.append("spend_ledger")
        internal_source = "spend_ledger"
        ledger_budget_exhausted = bool(ledger.get("budget_exhausted"))
        if "remaining_usd" in ledger:
            internal_remaining = ledger.get("remaining_usd")
        if "pipeline_spent_usd" in ledger:
            pipeline_from_totals = True
            pipeline_spent_usd = ledger["pipeline_spent_usd"]
            if "actions_spent_usd" not in known:
                spent["actions_spent_usd"] = pipeline_spent_usd
                known.add("actions_spent_usd")
                action_source = "pipeline_scope"
                sources.append("pipeline_scope")
    view = {
        "spent": spent,
        "caps": caps,
        "known": known,
        "sources": sources,
        "action_source": action_source,
        "internal_source": internal_source,
        "run_id": _run_id(context),
        "ledger_budget_exhausted": ledger_budget_exhausted,
        "internal_remaining_usd": internal_remaining,
        "pipeline_from_totals": pipeline_from_totals,
    }
    if pipeline_from_totals:
        view["pipeline_scope_spent_usd"] = pipeline_spent_usd
        if "actions_spent_usd" in known and action_source != "pipeline_scope":
            view["pipeline_separate"] = True
    return view


def _action_totals(
    project_id: int,
    context: dict[str, Any],
) -> tuple[Mapping[str, Any], str] | None:
    if "spend" in context:
        raw = context.get("spend")
        if isinstance(raw, Mapping):
            return raw, "spend_snapshot"
        return None
    raw = _call_reader(project_id, context)
    if isinstance(raw, Mapping):
        return raw, "action_layer"
    return None


def _call_reader(project_id: int, context: dict[str, Any]) -> Mapping[str, Any] | None:
    reader = context.get("spend_reader")
    if reader is None:
        reader = _reader
    if reader is None:
        return None
    run_value = _run_id(context)
    read_totals = getattr(reader, "read_totals", None)
    if callable(read_totals):
        found = read_totals(project_id=int(project_id), run_id=run_value)
    elif callable(reader):
        found = reader(project_id=int(project_id), run_id=run_value)
    else:
        return None
    return found if isinstance(found, Mapping) else None


def read_pipeline_spent(
    source: Any,
    *,
    project_id: int,
    run_id: str | None,
) -> float | None:
    """Необязательное чтение pipeline-scope.

    Берёт публичный метод ``read_pipeline_spent`` или ``pipeline_spent``.
    ``SpendLedger._pipeline`` не читается: пока WS5 не дал геттер, функции
    просто нет и результат ``None``.
    """

    if source is None:
        return None
    for name in ("read_pipeline_spent", "pipeline_spent"):
        fn = getattr(source, name, None)
        if callable(fn):
            return _call_pipeline(fn, project_id=project_id, run_id=run_id)
    return None


def _call_pipeline(fn: Any, *, project_id: int, run_id: str | None) -> float:
    try:
        value = fn(project_id=project_id, run_id=run_id)
    except TypeError:
        try:
            value = fn(run_id)
        except TypeError:
            value = fn()
    return _num(value)


def _apply_pipeline_scope(
    project_id: int,
    context: dict[str, Any],
    view: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if view is not None and view.get("pipeline_from_totals"):
        return view
    amount = _pipeline_amount(project_id, context)
    if amount is None:
        if view is not None:
            view["pipeline_scope_spent_usd"] = None
        return view
    if view is None:
        view = {
            "spent": {},
            "caps": dict(_CAP_DEFAULTS),
            "known": set(),
            "sources": [],
            "action_source": "",
            "internal_source": "",
            "run_id": _run_id(context),
        }
    view["pipeline_scope_spent_usd"] = amount
    if "actions_spent_usd" not in view["known"]:
        view["spent"]["actions_spent_usd"] = amount
        view["known"].add("actions_spent_usd")
        view["action_source"] = "pipeline_scope"
        view["sources"].append("pipeline_scope")
        return view
    # Уже есть actions_spent_usd от WS2 ($40 на run). Число роутера не подменяет
    # его и не суммируется: тот же потолок проверяется отдельно.
    cap = view["caps"]["actions_cap_usd"]
    view["pipeline_separate"] = cap
    return view


def _pipeline_amount(project_id: int, context: dict[str, Any]) -> float | None:
    run_id = _run_id(context)
    explicit = context.get("read_pipeline_spent")
    if callable(explicit):
        return _call_pipeline(explicit, project_id=project_id, run_id=run_id)
    reader = context.get("spend_reader")
    if reader is None:
        reader = _reader
    found = read_pipeline_spent(reader, project_id=project_id, run_id=run_id)
    if found is not None:
        return found
    ledger = context.get("spend_ledger")
    if ledger is None and "ledger" in context:
        ledger = context.get("ledger")
    return read_pipeline_spent(ledger, project_id=project_id, run_id=run_id)


def _resolve_ledger(context: dict[str, Any]) -> Any:
    for key in ("spend_ledger", "ledger"):
        if key in context and context.get(key) is not None:
            return context.get(key)
    return _process_spend_ledger()


def _process_spend_ledger() -> Any:
    """``get_spend_ledger()``, если #345 уже в дереве. Иначе None."""

    try:
        from app.orchestrator.routing.ledger import get_spend_ledger
    except Exception:  # noqa: BLE001 — роутер ещё не в main
        return None
    if not callable(get_spend_ledger):
        return None
    try:
        return get_spend_ledger()
    except Exception:  # noqa: BLE001 — процессный леджер не обязан подниматься
        return None


def _ledger_totals(context: dict[str, Any]) -> dict[str, Any] | None:
    ledger = _resolve_ledger(context)
    if ledger is None:
        return None
    snapshot = _read_ledger_totals(ledger, _run_id(context))
    if snapshot is not None:
        return snapshot
    spent_fn = getattr(ledger, "spent", None)
    if not callable(spent_fn):
        return None
    try:
        spent = _num(spent_fn(_run_id(context)))
    except TypeError:
        spent = _num(spent_fn())
    budget_raw = getattr(ledger, "internal_budget_usd", CAP_INTERNAL_USD)
    budget = CAP_INTERNAL_USD if budget_raw is None else _num(budget_raw)
    return {"spent_usd": spent, "budget_usd": budget, "budget_exhausted": False}


def _read_ledger_totals(ledger: Any, run_id: str | None) -> dict[str, Any] | None:
    """Снимок ``SpendLedger.totals`` (#345 ``eefc08b4``). Pipeline в $10 не входит."""

    totals_fn = getattr(ledger, "totals", None)
    if not callable(totals_fn):
        return None
    try:
        raw = totals_fn(run_id)
    except TypeError:
        raw = totals_fn()
    if not isinstance(raw, Mapping):
        return None
    limit_raw = raw.get("internal_limit_usd", getattr(ledger, "internal_budget_usd", CAP_INTERNAL_USD))
    limit = CAP_INTERNAL_USD if limit_raw is None else _num(limit_raw)
    out: dict[str, Any] = {
        "spent_usd": _num(raw.get("internal_spent_usd")),
        "budget_usd": limit,
        "budget_exhausted": raw.get("budget_exhausted") is True,
        "from_totals": True,
    }
    if "internal_remaining_usd" in raw and not isinstance(raw.get("internal_remaining_usd"), bool):
        remaining = _signed_num(raw.get("internal_remaining_usd"))
        if remaining is not None:
            out["remaining_usd"] = remaining
    if "pipeline_spent_usd" in raw and not isinstance(raw.get("pipeline_spent_usd"), bool):
        out["pipeline_spent_usd"] = _num(raw.get("pipeline_spent_usd"))
    return out


def _signed_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _budget_signal(context: dict[str, Any]) -> dict[str, Any] | None:
    if "response" in context:
        raw = context.get("response")
    elif "llm_response" in context:
        raw = context.get("llm_response")
    else:
        return None
    if raw is None:
        return None
    if not _is_budget_exhausted(raw):
        return None
    status, reason, payload = _response_parts(raw)
    return {
        "task_status": status,
        "approval_reason": reason,
        "payload": dict(payload) if isinstance(payload, Mapping) else {},
    }


def _is_budget_exhausted(response: Any) -> bool:
    if not isinstance(response, Mapping):
        predicate = _contracts_budget_predicate()
        if predicate is not None:
            try:
                if predicate(response):
                    return True
            except Exception:  # noqa: BLE001 — локальный предикат остаётся запасным
                pass
    status, reason, _payload = _response_parts(response)
    return status == _WAITING and reason == _BUDGET_EXHAUSTED


def _contracts_budget_predicate() -> Any:
    try:
        from app.orchestrator import contracts as contracts
    except Exception:  # noqa: BLE001 — #350 ещё не в main
        return None
    predicate = getattr(contracts, "budget_exhausted_response", None)
    return predicate if callable(predicate) else None


def _response_parts(response: Any) -> tuple[Any, str, Any]:
    if isinstance(response, Mapping):
        status = response.get("task_status")
        approval = response.get("approval")
    else:
        status = getattr(response, "task_status", None)
        approval = getattr(response, "approval", None)
    status_value = getattr(status, "value", status)
    if isinstance(approval, Mapping):
        reason = approval.get("reason") or ""
        payload = approval.get("payload") or {}
    elif approval is None:
        reason = ""
        payload = {}
    else:
        reason = getattr(approval, "reason", "") or ""
        payload = getattr(approval, "payload", None) or {}
    return status_value, str(reason), payload


def _run_id(context: dict[str, Any]) -> str | None:
    raw = context.get("run_id")
    if raw is None:
        return None
    return raw if isinstance(raw, str) else str(raw)


def _context_has_run_id(context: dict[str, Any]) -> bool:
    """Ключ, который #345@eefc08b4 не считает пустым.

    Нет ключа, ``None`` и строка из пробелов для леджера — тот же ноль,
    что и ``spent(None)``. Такой контекст бюджет не закрывает.
    """

    if "run_id" not in context:
        return False
    raw = context.get("run_id")
    if raw is None:
        return False
    text = raw if isinstance(raw, str) else str(raw)
    return bool(text.strip())


def _missing_run_id() -> Any:
    return make_result(
        name=_NAME,
        passed=False,
        blocking=True,
        details={"reason": "missing_run_id"},
    )


def _forecast(raw: Any) -> dict[str, float]:
    src = raw if isinstance(raw, Mapping) else {}
    return {
        "internal": _num(src.get("internal")),
        "actions": _num(src.get("actions")),
        "run": _num(src.get("run")),
        "project": _num(src.get("project")),
        "daily": _num(src.get("daily")),
    }


def _num(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number
