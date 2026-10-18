/** Очередь «Применить правки»: coverage_* остаётся локально, остальное уходит в apply. */

export function isCoverageQueueOp(op: { type: string }): boolean {
  return String(op.type || "").startsWith("coverage_");
}

export function montageOpsToApply<T extends { type: string; frame_number: number }>(
  all: readonly T[],
  frameNumbers?: readonly number[] | null,
): T[] {
  const eligible = all.filter((op) => !isCoverageQueueOp(op));
  if (!frameNumbers || frameNumbers.length === 0) return eligible;
  const wanted = new Set(frameNumbers);
  return eligible.filter((op) => wanted.has(op.frame_number));
}

export function pendingApplyFrameNumbers(
  ops: readonly { type: string; frame_number: number }[],
): number[] {
  const nums = [
    ...new Set(montageOpsToApply(ops).map((op) => op.frame_number)),
  ];
  nums.sort((a, b) => a - b);
  return nums;
}

export function queueAfterCoverageApply<
  T extends { frame_number: number; type: string },
>(prev: readonly T[], numbers: readonly number[], changedTypes: ReadonlySet<string>): T[] {
  return prev.filter(
    (x) => !(numbers.includes(x.frame_number) && changedTypes.has(String(x.type))),
  );
}

/** Ключ операции: слот и инструкция различают два image_ai_change на одном кадре. */
export function montageOpSelectKey(op: {
  type: string;
  frame_number: number;
  shot?: number | string;
  slot?: string;
  instruction?: string;
}): string {
  const slot = op.slot || "";
  const instruction = String(op.instruction || "").trim();
  return `${op.type}:${op.frame_number}:${op.shot ?? ""}:${slot}:${instruction}`;
}

/**
 * Короткий серверный список принимаем, только если это хвост локальной очереди
 * (сделанные ops сняты). Чужой короткий снимок локальную очередь не затирает.
 * Пустой серверный снимок локальную очередь и её данные не затирает:
 * без сигнала sentKeys это не «все ops уже сняты».
 */
export function serverQueueReplacesLocal<T>(
  local: readonly T[],
  restored: readonly T[],
  keyOf: (op: T) => string,
): boolean {
  if (local.length > 0 && restored.length === 0) return false;
  if (restored.length >= local.length) return true;
  const localKeys = local.map(keyOf);
  const restoredKeys = restored.map(keyOf);
  const suffix = localKeys.slice(localKeys.length - restoredKeys.length);
  return restoredKeys.every((key, index) => key === suffix[index]);
}

/**
 * done/idle: пустой сервер снимает ops, которые уже ушли в apply
 * (sentKeys или pending_ops job), даже если эту вкладку не сабмитили.
 * Новые dirty-ops остаются. Без сигнала пустой снимок очередь не трогает.
 */
export function reconcileIdleQueue<T>(
  local: readonly T[],
  server: readonly T[],
  keyOf: (op: T) => string,
  appliedKeys: ReadonlySet<string>,
): T[] {
  if (appliedKeys.size === 0) {
    if (server.length === 0 && local.length > 0) return local.slice();
    if (serverQueueReplacesLocal(local, server, keyOf)) return server.slice();
    return local.slice();
  }
  const serverKeys = new Set(server.map(keyOf));
  const kept = local.filter((op) => {
    const key = keyOf(op);
    return !(appliedKeys.has(key) && !serverKeys.has(key));
  });
  const keptKeys = new Set(kept.map(keyOf));
  const extra = server.filter((op) => !keptKeys.has(keyOf(op)));
  return [...kept, ...extra];
}

export function queueAfterApply<T>(
  all: readonly T[],
  sentKeys: ReadonlySet<string>,
  keyOf: (op: T) => string,
): T[] {
  return all.filter((op) => !sentKeys.has(keyOf(op)));
}
