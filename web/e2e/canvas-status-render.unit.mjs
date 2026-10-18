import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactFlowProvider } from "@xyflow/react";

const { NodeGenerationBadge } = await import("../src/components/canvas/node-generation-badge.tsx");
const { PipelineNode } = await import("../src/components/canvas/pipeline-node.tsx");

function nodeProps(status, label) {
  return {
    id: "n1",
    type: "pipeline",
    data: {
      nodeKey: "n1",
      type: "plan",
      label,
      status,
      progress: 0,
      progressText: null,
      error: null,
      attempts: 0,
    },
    selected: false,
    dragging: false,
    zIndex: 1,
    selectable: true,
    deletable: true,
    draggable: true,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
  };
}

function renderNode(status, label) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(
        ReactFlowProvider,
        null,
        React.createElement(PipelineNode, nodeProps(status, label)),
      ),
    ),
  );
}

test("бейдж waiting_approval и неизвестный статус рендерятся без исключения", () => {
  const approval = renderToStaticMarkup(
    React.createElement(NodeGenerationBadge, { status: "waiting_approval" }),
  );
  assert.match(approval, /Проверка/);

  const unknown = renderToStaticMarkup(
    React.createElement(NodeGenerationBadge, { status: "some_future_status" }),
  );
  assert.match(unknown, /Ожидание/);
  assert.doesNotMatch(unknown, /undefined/);
});

test("нода waiting_approval и неизвестный статус рендерятся без исключения", () => {
  const approval = renderNode("waiting_approval", "Ждёт апрув");
  assert.match(approval, /Ждёт апрув/);
  assert.match(approval, /проверка/);

  const unknown = renderNode("some_future_status", "Будущий шаг");
  assert.match(unknown, /Будущий шаг/);
  assert.match(unknown, /ожидание/);
  assert.doesNotMatch(unknown, /undefined/);
});
