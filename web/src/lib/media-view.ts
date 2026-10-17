export function mediaPhase(phase: string): string {
  return ({
    reserved: "Запрос разрешён", submitted: "Запрос отправлен",
    queued: "В очереди провайдера", running: "Генерация выполняется",
    completed: "Провайдер сообщил о готовности", downloading: "Скачивание",
    verifying: "Проверка файла", verified: "Файл проверен",
    failed: "Результат отклонён", cancelled: "Отмена подтверждена провайдером",
    unknown: "Результат неизвестен — нужен человек",
  } as Record<string, string>)[phase] ?? "Состояние неизвестно";
}

export function mediaVerification(status?: string): string {
  return status === "passed" ? "Структура файла проверена; качество содержания не оценивалось"
    : status === "failed" ? "Проверка файла не пройдена"
    : "Проверка результата не подтверждена";
}

export function artifactLink(uuid: string, projectId: number | null | undefined): string | null {
  return /^[a-f0-9]{32}$/.test(uuid) && typeof projectId === "number"
    && Number.isSafeInteger(projectId) && projectId > 0
    ? `/api/artifacts/${uuid}/file?project_id=${projectId}` : null;
}

export function imagePreviewLink(
  kind: string,
  job: {phase: string; verification?: {status?: string}; artifact_uuid?: string | null},
  projectId: number | null | undefined,
): string | null {
  if (kind !== "image" || job.phase !== "verified" || job.verification?.status !== "passed"
      || typeof job.artifact_uuid !== "string") return null;
  return artifactLink(job.artifact_uuid, projectId);
}

export function publicationSummary(status?: string): string {
  const validation = status === "passed" ? "Файл проверен"
    : status === "failed" ? "Файл не прошёл проверку" : "Готовность файла не подтверждена";
  return `${validation}. Публикация выключена — доступен только dry-run.`;
}
