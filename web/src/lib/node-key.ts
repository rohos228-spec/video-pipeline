/** Разбор node_key (n_plan, n_plan_1700000000) → node_type. */

import { NODE_CATALOG } from "./node-catalog";
import { stepCodeForNodeType } from "./node-step-map";

const KNOWN_TYPES = Object.keys(NODE_CATALOG).sort((a, b) => b.length - a.length);

export function nodeTypeFromKey(nodeKey: string | null | undefined): string {
  if (!nodeKey) return "";
  if (!nodeKey.startsWith("n_")) return nodeKey;
  const rest = nodeKey.slice(2);
  if (NODE_CATALOG[rest]) return rest;
  for (const typ of KNOWN_TYPES) {
    if (rest === typ || rest.startsWith(`${typ}_`)) return typ;
  }
  return rest;
}

export function stepCodeForNodeKey(nodeKey: string | null | undefined): string | undefined {
  return stepCodeForNodeType(nodeTypeFromKey(nodeKey));
}
