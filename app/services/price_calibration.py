from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    name: str
    value: float | tuple
    status: str
    probes: int
    source: str
    note: str = ""

    @property
    def label_ru(self) -> str:
        return {"measured": "замер", "single_probe": "одна проба", "estimate": "оценка",
                "policy": "политика"}[self.status]


def _suno_credits() -> float:
    from app.services import kie_catalog
    return float(kie_catalog.get_model("suno-music")["pricing"]["default"])


def code_values() -> dict[str, object]:
    from app.services import api_tracker_hook, kie_catalog, media_spend, spend_guard, vibecode_catalog
    from app.services.action_layer import builtin_montage_more as bmm
    return {
        "ELEVENLABS_USD_PER_CHAR": media_spend.ELEVENLABS_USD_PER_CHAR,
        "ELEVENLABS_SFX_USD_PER_SEC": media_spend.ELEVENLABS_SFX_USD_PER_SEC,
        "PRICE_MARKUP": vibecode_catalog.PRICE_MARKUP,
        "DEFAULT_REQUEST_ESTIMATE_USD": spend_guard.DEFAULT_REQUEST_ESTIMATE_USD,
        "MEDIA_SUBMIT_ESTIMATE_USD": spend_guard.MEDIA_SUBMIT_ESTIMATE_USD,
        "PLANNER_ESTIMATE_USD": spend_guard.PLANNER_ESTIMATE_USD,
        "IMAGE_BILLED_ATTEMPTS": bmm.IMAGE_BILLED_ATTEMPTS,
        "VIDEO_BILLED_ATTEMPTS": bmm.VIDEO_BILLED_ATTEMPTS,
        "LLM_USD_IN_OUT": (bmm._LLM_USD_IN, bmm._LLM_USD_OUT),
        "CREDIT_USD": kie_catalog.CREDIT_USD,
        "SUNO_MUSIC_CREDITS": _suno_credits(),
        "GPT_5_6_SOL_RATES": tuple(api_tracker_hook._MEASURED_RATES["gpt-5.6-sol"]),
    }


TABLE: tuple[Calibration, ...] = (
    Calibration("ELEVENLABS_USD_PER_CHAR", 0.0003, "estimate", 1,
                "LIVE_RUNS 2026-10-10: один TTS, цена не откалибрована", "ждёт сверки с кабинетом ElevenLabs"),
    Calibration("ELEVENLABS_SFX_USD_PER_SEC", 0.012, "estimate", 1,
                "LIVE_RUNS 2026-10-10: один SFX", "ждёт сверки с кабинетом ElevenLabs"),
    Calibration("PRICE_MARKUP", 3.0, "estimate", 0, "наценка каталога Vibecode, не сверялась с балансом",
                "нужны данные Vibecode заказчика"),
    Calibration("DEFAULT_REQUEST_ESTIMATE_USD", 0.05, "measured", 3,
                "docs/KIE_COST_CALIBRATION.md: вызовы стоили 0,00005-0,008", "намеренно с запасом"),
    Calibration("MEDIA_SUBMIT_ESTIMATE_USD", 0.30, "estimate", 0,
                "LIVE_RUNS: картинка Outsee ~0,03, клип Veo lite не измерен", "консервативный потолок на отправку"),
    Calibration("PLANNER_ESTIMATE_USD", 0.05, "measured", 10,
                "LIVE_RUNS наборы планировщика: ~0,0025 за запрос", "запас x20, закрывается фактом"),
    Calibration("IMAGE_BILLED_ATTEMPTS", 3, "policy", 0, "допущение оценки плана"),
    Calibration("VIDEO_BILLED_ATTEMPTS", 4, "policy", 0, "допущение оценки плана"),
    Calibration("LLM_USD_IN_OUT", (1.6, 8.4), "measured", 3, "docs/KIE_COST_CALIBRATION.md, одна модель"),
    Calibration("CREDIT_USD", 0.005, "measured", 3, "docs/KIE_COST_CALIBRATION.md, курс 1 кредит = $0,005"),
    Calibration("SUNO_MUSIC_CREDITS", 122.0, "single_probe", 1,
                "LIVE_RUNS 2026-10-12: баланс 4862,39 -> 4740,39; прайс-лист говорит 12",
                "N-11: взят замер как консервативная оценка; между замерами баланс упал ещё на 12 кредитов "
                "без наших вызовов (аккаунт общий), нужен кабинет Kie с историей по task id"),
    Calibration("GPT_5_6_SOL_RATES", (1.6, 8.4, 0.0), "measured", 3, "docs/KIE_COST_CALIBRATION.md"),
)


def by_name() -> dict[str, Calibration]:
    return {c.name: c for c in TABLE}


def label_ru(name: str) -> str:
    c = by_name().get(name)
    return c.label_ru if c else "оценка"


BALANCE_SOURCES = {
    "kie": "автоснимок: kie_http.get_credits() * CREDIT_USD (курс замерян)",
    "outsee": "автоснимок: outsee_http.fetch_balance(); в USD только при явной пометке единицы в ответе, "
              "иначе нет снимка (единица не подтверждена живьём)",
    "elevenlabs": "автоснимок символов: GET /v1/user/subscription; в USD не пересчитывается, пока "
                  "ELEVENLABS_USD_PER_CHAR - оценка (нужно >=3 замеров): статус «нет USD-снимка»",
    "vibecode": "ТОЛЬКО ЖУРНАЛ ПРИЛОЖЕНИЯ: баланса по API нет (gpt_api._credits_hint это подсказка в тексте "
                "ошибки), адаптер не регистрируется",
}
