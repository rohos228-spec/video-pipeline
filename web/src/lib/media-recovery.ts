/** Read-only client of the media recovery layer. Writes (resume, release) are NOT here: they go through the action layer. */
import { http } from "./api";
import type { RecoveryAssessment, UnknownOutcomes } from "./media-recovery-view";

const base = (project: number) => `/api/projects/${project}/media-recovery`;

export const mediaRecovery = {
  assessment: (project: number, nodeType: string) =>
    http<RecoveryAssessment>(`${base(project)}/${encodeURIComponent(nodeType)}`),
  unknownOutcomes: (project: number) => http<UnknownOutcomes>(`${base(project)}/unknown-outcomes`),
};
