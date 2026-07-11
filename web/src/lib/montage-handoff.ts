/** Agent: проект ждёт забор bundle hub (send_to_main_pc). */
export function isMontageHandoffPending(
  project?:
    | {
        montage_handoff_pending?: boolean;
        meta?: Record<string, unknown>;
      }
    | null,
): boolean {
  if (!project) return false;
  const meta = project.meta;
  if (meta?.fleet_handoff_complete) return false;
  if (meta?.fleet_transfer_aborted || meta?.user_stop) return false;
  if (project.montage_handoff_pending) return true;
  return Boolean(meta?.fleet_montage_deferred && meta?.montage_ready);
}
