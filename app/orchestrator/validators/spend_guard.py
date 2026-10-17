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
  ``get_spend_ledger()``. ``spend`` / ``spend_ledger`` / ``ledger`` /
  ``spend_reader`` / ``read_pipeline_spent`` из context не читаются:
  их подсовывает planner. Пока модуля нет, остаётся читатель,
  переданный сервером через ``set_spend_reader``.
  С ``eefc08b4`` пустой ``run_id`` у ``spent`` / ``totals`` даёт 0 и не
  блокирует, а внутренний вызов без ``run_id`` бросает ``ValueError``.
  Без ``run_id`` ledger не зовётся и в ``dry_run`` тоже: результат
  ``blocking`` с ``reason=missing_run_id``. ``dry_run`` проходит те же
  проверки, что и боевой вызов. Тот же ответ, если ledger сам бросил
  ``ValueError`` про ``run_id``. ``TypeError`` при чтении не повторяет
  вызов без аргументов: результат ``blocking`` с ``reason=spend_unreadable``,
  вызова нет.
- spend action layer (#342) через ``SpendReader.read_totals``, который
  сервер кладёт в ``set_spend_reader``. WS2 принимает ключи ``*_spent_usd`` как есть.
  ``actions_spent_usd`` — лимит $40 пайплайна на run, не кредиты сессии.

Потолки плана, если источник их не передал: $200 сутки / $100 проект /
$50 запуск / $10 internal / $40 действия. Явный потолок ``<= 0`` не
выключает проверку: это ``blocking`` с ``reason=invalid_amount``.
Прогноз ``context["forecast_usd"]`` прибавляется к прочитанному до вызова
и никуда не записывается. Агрегат ``run`` / ``project`` / ``daily`` равен
``max(явное значение, internal + actions)``. Нет явного ключа — та же сумма.
Нечисло, inf, NaN, отрицательное, явный ``None`` и любая строка в трате
или потолке — не 0, а ``blocking`` с ``reason=invalid_amount``.
Конечные числа ``>= 0`` в тратах остаются как были.
Есть ``run_id``, но нет серверного снимка и нет сигнала
``budget_exhausted`` — ``blocking`` с ``reason=spend_source_missing``.
Исключение из ``get_spend_ledger()`` — ``spend_unreadable``.

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

from app.orchestrator.validators.result import make_result

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


class _SpendReadError(Exception):
    """Чтение бюджета сорвалось. Повторного вызова без аргументов нет."""


class _InvalidAmount(Exception):
    """Сумма не конечное число >= 0. В ноль её не подменяем."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


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
    """Инъекция читателя action layer сервером. Context это значение не подменяет."""

    global _reader
    _reader = reader


def get_spend_reader() -> SpendReader | None:
    return _reader


def check(project_id: int, context: dict[str, Any]) -> Any:
    """Чтение бюджета. Без ``run_id`` ledger не вызывается, включая dry_run."""

    if not _context_has_run_id(context):
        return _missing_run_id()
    try:
        return _checked(project_id, context)
    except _SpendReadError:
        return _spend_unreadable()
    except _InvalidAmount as exc:
        return _invalid_amount(exc)
    except ValueError as exc:
        if "run_id" in str(exc):
            return _missing_run_id()
        raise


def _checked(project_id: int, context: dict[str, Any]) -> Any:
    signal = _budget_signal(context)
    view = _load_view(project_id, context)
    view = _apply_pipeline_scope(project_id, context, view)
    if view is None and signal is None:
        return _spend_source_missing()
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
        spent = _payload_amount(payload, "spent_usd", default=0.0)
    if cap is None:
        cap = _payload_amount(payload, "budget_usd", default=CAP_INTERNAL_USD)
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
            spent[key] = _field_amount(raw, key, default=0.0)
            known.add(key)
        for key, default in _CAP_DEFAULTS.items():
            caps[key] = _field_cap(raw, key, default=default)
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
    raw = _call_reader(project_id, context)
    if isinstance(raw, Mapping):
        return raw, "action_layer"
    return None


def _call_reader(project_id: int, context: dict[str, Any]) -> Mapping[str, Any] | None:
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
        except TypeError as exc:
            raise _SpendReadError("pipeline") from exc
    return _require_amount(value, field="pipeline_spent_usd")


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
    found = read_pipeline_spent(_reader, project_id=project_id, run_id=run_id)
    if found is not None:
        return found
    return read_pipeline_spent(_process_spend_ledger(), project_id=project_id, run_id=run_id)


def _resolve_ledger(context: dict[str, Any]) -> Any:
    del context
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
    except Exception as exc:  # noqa: BLE001 — сломанный леджер не открывает вызов
        raise _SpendReadError("spend_ledger") from exc


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
        spent = _require_amount(spent_fn(_run_id(context)), field="internal_spent_usd")
    except TypeError as exc:
        raise _SpendReadError("spent") from exc
    budget_raw = getattr(ledger, "internal_budget_usd", CAP_INTERNAL_USD)
    budget = CAP_INTERNAL_USD if budget_raw is None else _require_cap(budget_raw, field="internal_budget_usd")
    return {"spent_usd": spent, "budget_usd": budget, "budget_exhausted": False}


def _read_ledger_totals(ledger: Any, run_id: str | None) -> dict[str, Any] | None:
    """Снимок ``SpendLedger.totals`` (#345 ``eefc08b4``). Pipeline в $10 не входит."""

    totals_fn = getattr(ledger, "totals", None)
    if not callable(totals_fn):
        return None
    try:
        raw = totals_fn(run_id)
    except TypeError as exc:
        raise _SpendReadError("totals") from exc
    if not isinstance(raw, Mapping):
        return None
    if "internal_limit_usd" in raw:
        limit = _require_cap(raw.get("internal_limit_usd"), field="internal_limit_usd")
    else:
        limit_raw = getattr(ledger, "internal_budget_usd", CAP_INTERNAL_USD)
        limit = CAP_INTERNAL_USD if limit_raw is None else _require_cap(limit_raw, field="internal_budget_usd")
    out: dict[str, Any] = {
        "spent_usd": _field_amount(raw, "internal_spent_usd", default=0.0),
        "budget_usd": limit,
        "budget_exhausted": raw.get("budget_exhausted") is True,
        "from_totals": True,
    }
    if "internal_remaining_usd" in raw:
        remaining = _remaining_amount(raw.get("internal_remaining_usd"))
        if remaining is not None:
            out["remaining_usd"] = remaining
    if "pipeline_spent_usd" in raw:
        out["pipeline_spent_usd"] = _require_amount(raw.get("pipeline_spent_usd"), field="pipeline_spent_usd")
    return out


def _remaining_amount(value: Any) -> float | None:
    """Остаток может быть отрицательным. None — поля нет. Мусор — ошибка."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise _InvalidAmount("internal_remaining_usd")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _InvalidAmount("internal_remaining_usd") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise _InvalidAmount("internal_remaining_usd")
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


def _spend_unreadable() -> Any:
    return make_result(
        name=_NAME,
        passed=False,
        blocking=True,
        details={"reason": "spend_unreadable"},
    )


def _invalid_amount(exc: _InvalidAmount) -> Any:
    return make_result(
        name=_NAME,
        passed=False,
        blocking=True,
        details={"reason": "invalid_amount", "field": exc.field},
    )


def _spend_source_missing() -> Any:
    return make_result(
        name=_NAME,
        passed=False,
        blocking=True,
        details={"reason": "spend_source_missing"},
    )


def _forecast(raw: Any) -> dict[str, float]:
    """Прогноз до вызова. Агрегат = max(явный, internal + actions)."""

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise _InvalidAmount("forecast_usd")
    internal = _field_amount(raw, "internal", default=0.0)
    actions = _field_amount(raw, "actions", default=0.0)
    implied = internal + actions

    def aggregate(key: str) -> float:
        if key not in raw:
            return implied
        explicit = _require_amount(raw.get(key), field=f"forecast_usd.{key}")
        return max(explicit, implied)

    return {
        "internal": internal,
        "actions": actions,
        "run": aggregate("run"),
        "project": aggregate("project"),
        "daily": aggregate("daily"),
    }


def _field_amount(raw: Mapping[str, Any], key: str, *, default: float) -> float:
    """Нет ключа — ``default``. Ключ есть, даже если значение None — проверка."""

    if key not in raw:
        return default
    return _require_amount(raw.get(key), field=key)


def _payload_amount(payload: Mapping[str, Any], key: str, *, default: float) -> float:
    if key not in payload:
        return default
    return _require_amount(payload.get(key), field=f"payload.{key}")


def _field_cap(raw: Mapping[str, Any], key: str, *, default: float) -> float:
    if key not in raw:
        return default
    return _require_cap(raw.get(key), field=key)


def _require_cap(value: Any, *, field: str) -> float:
    number = _require_amount(value, field=field)
    if number <= 0:
        raise _InvalidAmount(field)
    return number


def _require_amount(value: Any, *, field: str) -> float:
    """Конечное число >= 0. Строка, даже числовая, — ошибка."""

    if value is None or isinstance(value, bool | str):
        raise _InvalidAmount(field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _InvalidAmount(field) from exc
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        raise _InvalidAmount(field)
    return number
