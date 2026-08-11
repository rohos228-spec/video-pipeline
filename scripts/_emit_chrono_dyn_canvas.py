"""Emit canvas TSX with embedded scene×node report for #60."""
from __future__ import annotations

import json
from pathlib import Path

TABLE = Path(
    r"C:\Users\Admin\Desktop\video-pipeline\data\videos\spesivcevy-chrono-dyn\ops\chrono_dyn_agent_scene_table.json"
)
CANVAS = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Desktop-video-pipeline\canvases\chrono-dyn-scene-node-report.canvas.tsx"
)


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    rows = []
    for r in table["rows"]:
        rows.append(
            {
                "scene": r.get("scene") or "",
                "phase": r.get("phase") if r.get("phase") is not None else "",
                "beat": r.get("beat") or "",
                "transition": r.get("transition") or "",
                "link_prev": (r.get("link_prev") or "")[:140],
                "hook_next": (r.get("hook_next") or "")[:140],
                "structure": r.get("structure") or "",
                "scene_transition": r.get("scene_transition") or "",
                "vo": (r.get("vo") or "")[:140],
                "action_subject": (r.get("action_subject") or "")[:80],
                "action_text": (r.get("action_text") or "")[:180],
                "info_change": (r.get("info_change") or "")[:120],
                "camera_size": r.get("camera_size") or "",
                "camera_move": (r.get("camera_move") or "")[:80],
                "camera_who": (r.get("camera_who") or "")[:100],
                "location": (r.get("location") or "")[:100],
            }
        )
    chars = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "look": (c.get("look") or "")[:120],
        }
        for c in table["characters"]
    ]
    locs = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "zones": (c.get("zones") or "")[:100],
        }
        for c in table["locations"]
    ]
    scenes = sorted({r["scene"] for r in rows if r["scene"]})
    payload = json.dumps(
        {
            "counts": table["counts"],
            "scenes": scenes,
            "chars": chars,
            "locs": locs,
            "rows": rows,
        },
        ensure_ascii=False,
    )

    tsx = f"""import {{
  Callout,
  Divider,
  Grid,
  H1,
  H2,
  Pill,
  Row,
  Select,
  Stack,
  Stat,
  Table,
  Text,
  useCanvasState,
}} from "cursor/canvas";

const DATA = {payload} as const;

export default function ChronoDynSceneNodeReport() {{
  const [sceneFilter, setSceneFilter] = useCanvasState<string>("scene", "all");

  const filtered =
    sceneFilter === "all"
      ? DATA.rows
      : DATA.rows.filter((r) => r.scene === sceneFilter);

  const tableRows = filtered.map((r) => [
    r.scene,
    String(r.phase),
    r.beat,
    r.transition,
    r.action_subject,
    r.action_text,
    r.info_change,
    r.camera_size,
    r.camera_move,
    r.camera_who,
    r.link_prev,
    r.hook_next,
    r.location,
  ]);

  return (
    <Stack gap={{20}} style={{{{ padding: 20 }}}}>
      <Stack gap={{6}}>
        <H1>Отчёт chrono_dyn · сцена × нода</H1>
        <Text tone="secondary">
          Проект #60 spesivcevy-chrono-dyn · source: scene_design/*.json ·
          строка = фаза action + shot camera
        </Text>
      </Stack>

      <Grid columns={{5}} gap={{12}}>
        <Stat value={{String(DATA.counts.scenes_action)}} label="Сцен (action)" />
        <Stat value={{String(DATA.counts.shots_camera)}} label="Шотов (camera)" />
        <Stat value={{String(DATA.counts.characters)}} label="Персонажи" />
        <Stat value={{String(DATA.counts.locations)}} label="Локации (world)" />
        <Stat value={{String(DATA.counts.style_beats)}} label="Style beats" />
      </Grid>

      <Callout tone="info" title="Как читать V3">
        У каждой сцены есть связь с прошлой и крючок в следующую. Фазы идут по
        beat: setup → develop → turn → payoff. Между фазами — переход
        (match_cut / eyeline / sound_bridge…). Camera = 1 shot на фазу.
      </Callout>

      <H2>Персонажи (нода characters)</H2>
      <Table
        headers={{["id", "Имя", "Внешность"]}}
        rows={{DATA.chars.map((c) => [c.id, c.name, c.look])}}
        striped
        stickyHeader
      />

      <H2>Локации (нода world)</H2>
      <Table
        headers={{["id", "Название", "Зоны"]}}
        rows={{DATA.locs.map((l) => [l.id, l.name, l.zones])}}
        striped
        stickyHeader
      />

      <Divider />

      <Row gap={{12}} align="center">
        <H2>Сцены × action × camera</H2>
        <Select
          value={{sceneFilter}}
          onChange={{setSceneFilter}}
          options={{[
            {{ value: "all", label: `Все сцены (${{DATA.rows.length}} фаз)` }},
            ...DATA.scenes.map((s) => ({{
              value: s,
              label: `${{s}} (${{DATA.rows.filter((r) => r.scene === s).length}} фаз)`,
            }})),
          ]}}
        />
        <Pill tone="neutral">{{filtered.length}} строк</Pill>
      </Row>

      <Table
        headers={{[
          "Сцена",
          "Фаза",
          "Beat",
          "Переход",
          "Кто (action)",
          "Действие",
          "Что меняется",
          "Крупность",
          "Движение",
          "Кто в кадре",
          "Связь с прошлой",
          "Крючок в следующую",
          "Локация",
        ]}}
        rows={{tableRows}}
        striped
        stickyHeader
        style={{{{ maxHeight: 640 }}}}
      />

      <Text tone="tertiary" size="small">
        Полный JSON: data/videos/spesivcevy-chrono-dyn/ops/chrono_dyn_agent_scene_report.json
      </Text>
    </Stack>
  );
}}
"""
    CANVAS.parent.mkdir(parents=True, exist_ok=True)
    CANVAS.write_text(tsx, encoding="utf-8")
    print(CANVAS)
    print("bytes", CANVAS.stat().st_size)


if __name__ == "__main__":
    main()
