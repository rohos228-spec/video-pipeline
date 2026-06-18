import { api } from "@/lib/api";
import type { HITLDTO } from "@/lib/types";

export type VisualHitlKind = "approve_images" | "approve_videos";

export async function listPendingVisualHitl(
  projectId: number,
  kind: VisualHitlKind,
): Promise<HITLDTO[]> {
  const hitls = await api.listProjectHitl(projectId);
  return hitls.filter((h) => h.decision === "pending" && h.kind === kind);
}

export async function pendingHitlForFrame(
  projectId: number,
  frameId: number,
  kind: VisualHitlKind,
): Promise<HITLDTO | undefined> {
  const hitls = await listPendingVisualHitl(projectId, kind);
  return hitls.find((h) => h.frame_id === frameId);
}

export async function bulkVisualHitlDecision(
  projectId: number,
  kind: VisualHitlKind,
  decision: "approve" | "reject" | "regenerate",
): Promise<number> {
  const reviewKind = kind === "approve_images" ? "images" : "videos";
  const pending = await listPendingVisualHitl(projectId, kind);
  if (pending.length === 0) {
    return 0;
  }

  if (decision === "approve") {
    const media = await api.listMediaReview(projectId, reviewKind);
    const approvedStatus =
      kind === "approve_images" ? "image_approved" : "video_approved";
    for (const frame of media) {
      if (!frame.preview_url || frame.status === approvedStatus) {
        continue;
      }
      await api.patchFrame(projectId, frame.frame_id, {
        status: approvedStatus,
      });
    }
  }

  for (const h of pending) {
    await api.submitHitlDecision(h.id, { decision });
  }
  return pending.length;
}

export async function frameVisualHitlDecision(
  projectId: number,
  frameId: number,
  kind: VisualHitlKind,
  decision: "approve" | "reject" | "regenerate",
): Promise<void> {
  if (decision === "approve") {
    await api.patchFrame(projectId, frameId, {
      status: kind === "approve_images" ? "image_approved" : "video_approved",
    });
  }
  const hitl = await pendingHitlForFrame(projectId, frameId, kind);
  if (hitl) {
    await api.submitHitlDecision(hitl.id, { decision });
  }
}
