"use client";

import { useEffect, useRef } from "react";
import { useReactFlow, useStoreApi } from "@xyflow/react";

/** Рамка выделения правой кнопкой (XYFlow по умолчанию — только ЛКМ). */
export function RightButtonMarquee() {
  const store = useStoreApi();
  const { setNodes, getNodes } = useReactFlow();
  const dragging = useRef(false);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 2) return;

      const { domNode } = store.getState();
      const pane = domNode?.querySelector(".react-flow__pane");
      if (!pane || event.target !== pane) return;

      event.preventDefault();
      dragging.current = false;

      const bounds = pane.getBoundingClientRect();
      const startX = event.clientX - bounds.left;
      const startY = event.clientY - bounds.top;

      store.setState({
        userSelectionRect: { width: 0, height: 0, startX, startY, x: startX, y: startY },
        userSelectionActive: true,
      });

      const onPointerMove = (moveEvent: PointerEvent) => {
        const rect = store.getState().userSelectionRect;
        if (!rect) return;

        const mouseX = moveEvent.clientX - bounds.left;
        const mouseY = moveEvent.clientY - bounds.top;
        const distance = Math.hypot(mouseX - rect.startX, mouseY - rect.startY);
        if (distance <= 1 && !dragging.current) return;

        if (!dragging.current) {
          dragging.current = true;
          store.getState().resetSelectedElements();
        }

        const nextRect = {
          startX: rect.startX,
          startY: rect.startY,
          x: mouseX < rect.startX ? mouseX : rect.startX,
          y: mouseY < rect.startY ? mouseY : rect.startY,
          width: Math.abs(mouseX - rect.startX),
          height: Math.abs(mouseY - rect.startY),
        };

        const { transform, nodeLookup } = store.getState();
        const [tx, ty, zoom] = transform;
        const flowRect = {
          x: (nextRect.x - tx) / zoom,
          y: (nextRect.y - ty) / zoom,
          width: nextRect.width / zoom,
          height: nextRect.height / zoom,
        };

        const selected = new Set<string>();
        for (const node of nodeLookup.values()) {
          const userNode = node.internals.userNode;
          const width = node.measured.width ?? userNode.width ?? userNode.initialWidth ?? 0;
          const height = node.measured.height ?? userNode.height ?? userNode.initialHeight ?? 0;
          if (!width || !height) continue;
          const nx = node.internals.positionAbsolute.x;
          const ny = node.internals.positionAbsolute.y;
          const overlaps =
            nx + width >= flowRect.x &&
            nx <= flowRect.x + flowRect.width &&
            ny + height >= flowRect.y &&
            ny <= flowRect.y + flowRect.height;
          if (overlaps) selected.add(node.id);
        }

        setNodes(
          getNodes().map((node) => ({
            ...node,
            selected: selected.has(node.id),
          })),
        );

        store.setState({
          userSelectionRect: nextRect,
          userSelectionActive: true,
          nodesSelectionActive: false,
        });
      };

      const onPointerUp = (upEvent: PointerEvent) => {
        if (upEvent.button !== 2) return;
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);

        const hadDrag = dragging.current;
        dragging.current = false;
        store.setState({
          userSelectionActive: false,
          userSelectionRect: null,
          nodesSelectionActive: hadDrag && getNodes().some((n) => n.selected),
        });
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    };

    let root: HTMLElement | null = null;
    const bind = () => {
      root = store.getState().domNode ?? null;
      if (!root) return false;
      root.addEventListener("pointerdown", onPointerDown);
      return true;
    };

    let timer: number | undefined;
    if (!bind()) {
      timer = window.setTimeout(bind, 0);
    }

    return () => {
      if (timer != null) window.clearTimeout(timer);
      root?.removeEventListener("pointerdown", onPointerDown);
    };
  }, [getNodes, setNodes, store]);

  return null;
}
