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

export function queueAfterApply<T>(
  all: readonly T[],
  sentKeys: ReadonlySet<string>,
  keyOf: (op: T) => string,
): T[] {
  return all.filter((op) => !sentKeys.has(keyOf(op)));
}
