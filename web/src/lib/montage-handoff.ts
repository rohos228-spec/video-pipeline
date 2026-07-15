/** Agent: можно отправить проект на hub (любой статус, если fleet agent). */
export function isMontageHandoffPending(
  project?:
    | {
        montage_handoff_pending?: boolean;
        meta?: Record<string, unknown>;
      }
    | null,
  fleet?: { enabled?: boolean; role?: string } | null,
): boolean {
  if (!project) return false;
  const meta = project.meta;
  if (meta?.fleet_handoff_complete) return false;
  if (meta?.fleet_transfer_aborted || meta?.user_stop) return false;
  if (project.montage_handoff_pending) return true;
  if (fleet?.enabled && String(fleet.role || "").toLowerCase() === "agent") {
    return true;
  }
  return Boolean(meta?.fleet_montage_deferred && meta?.montage_ready);
}
