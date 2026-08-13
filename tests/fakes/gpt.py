"""FakeGptClient: скриптованный GPT для эмуляции пайплайна.

Определяет интент по маркерам сообщения (те же хинты, что шлют раннеры):
- plan  → `общий_план` apply-ops
- script → `закадровый_текст` apply-ops
- split → `replace_frames` apply-ops из сценария
- img_pr → строки «кадр N = uuid» в «Адресация:» → ops с промт_картинки
- anim_pr → ask_anim_pr_initial/ask_anim_pr_batch протокол

Хаос-хуки (chaos=...): пустой ответ, partial ops, неизвестный uuid,
обрезанный JSON, исключение на N-м вызове.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_UUID_LINE_RE = re.compile(r"кадр\s+(\d+)\s*=\s*([0-9a-fA-F]{8,})")
_ANIM_ID_RE = re.compile(r"ID изображения: (\[ID: [^\]]+\])")


@dataclass
class FakeScenario:
    """Сценарий эмуляции: что «пишет» GPT."""

    topic: str = "Рачок в неоне"
    plan_text: str = (
        "Эпизод 1: рачок просыпается в бочке с ржавчиной, потягивается "
        "клешнями и выбирается наружу. Перед ним неоновый город: провода, "
        "вывески, мокрый асфальт. Внизу мигает вывеска «ЕДА», и рачок "
        "бредёт к ней через лужи и пар из люков. Три бита: было/стало, "
        "тёплый хэппи-энд у горячего лотка."
    )
    vo_blocks: list[str] = field(
        default_factory=lambda: [
            "Рачок просыпается в бочке с ржавчиной и потягивается клешнями.",
            "Он выбирается наружу и видит неоновый город перед собой.",
            "Где-то внизу мигает вывеска «ЕДА» — и рачок бредёт к ней.",
        ]
    )
    image_prompt_tmpl: str = (
        "knitted wool scene, tiny felt crayfish, neon sign, frame {n}, "
        "soft handmade yarn texture, cozy children's book illustration"
    )
    anim_prompt_tmpl: str = (
        "slow neon push-in on the knitted crayfish, dust motes drift, "
        "gentle parallax, cinematic cozy motion, frame {n}"
    )


@dataclass
class FakeChaos:
    """Хаос-инъекции. Всё выключено по умолчанию."""

    img_pr_empty_batches: int = 0  # первые N батчей img_pr — пустой ответ
    img_pr_partial: bool = False  # img_pr отдаёт только первый uuid батча
    img_pr_unknown_uuid: bool = False  # добавить op с несуществующим uuid
    img_pr_truncated_json: bool = False  # обрезать JSON посередине
    fail_call_numbers: set[int] = field(default_factory=set)  # исключение на вызове №N
    excel_gpt_empty_ops: bool = False  # operator: ops=[]
    excel_gpt_partial: bool = False  # operator: только первый uuid


class FakeGptClient:
    """Duck-surface ApiGptClient для шагов пайплайна."""

    def __init__(
        self,
        scenario: FakeScenario | None = None,
        chaos: FakeChaos | None = None,
    ) -> None:
        self.scenario = scenario or FakeScenario()
        self.chaos = chaos or FakeChaos()
        self.calls: list[dict[str, Any]] = []
        self._call_no = 0
        self._img_pr_batch_no = 0

    # ── session ────────────────────────────────────────────────
    async def new_conversation(self) -> None:
        self.calls.append({"method": "new_conversation"})

    # ── generic ────────────────────────────────────────────────
    async def ask_fresh(
        self, text: str, *, timeout: float = 600, project_id: int | None = None
    ) -> str:
        self._call_no += 1
        self.calls.append({"method": "ask_fresh", "n": self._call_no})
        self._maybe_raise()
        return self._route(text or "", [])

    async def ask_with_files(
        self,
        text: str,
        attachments: list[Path],
        **kwargs: Any,
    ) -> str:
        self._call_no += 1
        self.calls.append(
            {
                "method": "ask_with_files",
                "n": self._call_no,
                "files": [Path(a).name for a in (attachments or [])],
            }
        )
        self._maybe_raise()
        return self._route(text or "", [Path(a) for a in (attachments or [])])

    async def ask_with_file(self, text: str, attachment: Path, **kw: Any) -> str:
        return await self.ask_with_files(text, [attachment], **kw)

    # ── anim_pr протокол ───────────────────────────────────────
    async def ask_anim_pr_initial(self, text: str, file: Path, **kw: Any) -> str:
        self.calls.append({"method": "ask_anim_pr_initial"})
        return "принято"

    async def ask_anim_pr_batch(
        self, msg: str, images: list | None = None, **kw: Any
    ) -> str:
        self._call_no += 1
        self.calls.append({"method": "ask_anim_pr_batch", "n": self._call_no})
        self._maybe_raise()
        ids = _ANIM_ID_RE.findall(msg or "")
        out = []
        for i, image_id in enumerate(ids, start=1):
            out.append(
                f"ID изображения: {image_id}\n"
                f"текст анимации: {self.scenario.anim_prompt_tmpl.format(n=i)}"
            )
        return "\n\n".join(out)

    # ── routing ────────────────────────────────────────────────
    def _maybe_raise(self) -> None:
        if self._call_no in self.chaos.fail_call_numbers:
            raise RuntimeError(f"fake chaos: call #{self._call_no} blew up")

    def _route(self, text: str, attachments: list[Path]) -> str:
        if "replace_frames" in text:
            return self._split_reply()
        if "закадровый_текст" in text:
            return self._script_reply()
        if "Адресация:" in text and _UUID_LINE_RE.search(text):
            return self._img_pr_reply(text)
        if "общий_план" in text:
            return self._plan_reply()
        # check/vision/misc — безобидный ack
        return "ОК"

    def _plan_reply(self) -> str:
        return json.dumps(
            {
                "ops": [
                    {
                        "target": "project",
                        "fields": {"общий_план": self.scenario.plan_text},
                    }
                ]
            },
            ensure_ascii=False,
        )

    def _script_reply(self) -> str:
        vo = "\n\n".join(self.scenario.vo_blocks)
        while len(vo) < 220:
            vo = vo + "\n\n" + vo
        return json.dumps(
            {
                "ops": [
                    {"target": "project", "fields": {"закадровый_текст": vo}}
                ]
            },
            ensure_ascii=False,
        )

    def _split_reply(self) -> str:
        frames = [
            {"закадр": block, "длительность": 3.0 + i}
            for i, block in enumerate(self.scenario.vo_blocks, start=1)
        ]
        return json.dumps(
            {"ops": [{"target": "replace_frames", "frames": frames}]},
            ensure_ascii=False,
        )

    def _img_pr_reply(self, text: str) -> str:
        self._img_pr_batch_no += 1
        if self._img_pr_batch_no <= self.chaos.img_pr_empty_batches:
            return "не могу обработать файл"  # нет ops → rejected reply path
        pairs = list(dict.fromkeys(_UUID_LINE_RE.findall(text)))
        if self.chaos.img_pr_partial and pairs:
            pairs = pairs[:1]
        ops = [
            {
                "frame_uuid": uuid,
                "fields": {
                    "промт_картинки": self.scenario.image_prompt_tmpl.format(n=n)
                },
            }
            for n, uuid in pairs
        ]
        if self.chaos.img_pr_unknown_uuid:
            ops.append(
                {
                    "frame_uuid": "deadbeefdeadbeefdeadbeef",
                    "fields": {"промт_картинки": "ghost frame"},
                }
            )
        payload = json.dumps({"ops": ops}, ensure_ascii=False)
        if self.chaos.img_pr_truncated_json and len(payload) > 40:
            return payload[: len(payload) // 2]  # обрыв без закрытия
        return payload


def make_fake_operator_api(
    scenario: FakeScenario | None = None,
    chaos: FakeChaos | None = None,
):
    """Фейк `gpt_operator_client.run_operator_api` (excel_gpt / check).

    Читает db_frames.json из input_paths и отдаёт apply-ops на ВСЕ uuid
    (N/N) — если хаос не просит иначе.
    """
    from types import SimpleNamespace

    sc = scenario or FakeScenario()
    ch = chaos or FakeChaos()

    async def _fake(
        *,
        project_dir: Path,
        node_key: str,
        role: str,
        output_mode: str,
        prompt: str,
        accompanying: str,
        input_paths: list[Path],
        check_mode: bool = False,
        check_fix: bool = True,
        source_prompt_keys: list[str] | None = None,
        check_streams: int | None = None,
        db_sot_check: bool = False,
    ):
        if check_mode:
            return SimpleNamespace(
                reply_text="ОК\nЗамечаний нет.",
                output_paths=[],
                gate_status="ok",
                analysis=None,
                apply_ops=None,
            )
        frames: list[dict] = []
        for p in input_paths or []:
            path = Path(p)
            if path.name == "db_frames.json" and path.is_file():
                try:
                    frames = json.loads(path.read_text(encoding="utf-8")).get(
                        "frames"
                    ) or []
                except Exception:  # noqa: BLE001
                    frames = []
                break
        uuids = [str(f.get("uuid")) for f in frames if f.get("uuid")]
        if ch.excel_gpt_partial and uuids:
            uuids = uuids[:1]
        ops = (
            []
            if ch.excel_gpt_empty_ops
            else [
                {
                    "frame_uuid": u,
                    "fields": {"смысл": f"fake смысл кадра {i + 1}"},
                }
                for i, u in enumerate(uuids)
            ]
        )
        return SimpleNamespace(
            reply_text=json.dumps({"ops": ops}, ensure_ascii=False),
            output_paths=[],
            gate_status=None,
            analysis=None,
            apply_ops={"ops": ops, "export_xlsx": True},
        )

    return _fake
