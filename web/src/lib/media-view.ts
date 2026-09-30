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

export function artifactLink(uuid: string): string | null {
  return /^[a-f0-9]{32}$/.test(uuid) ? `/api/artifacts/${uuid}/file` : null;
}

export function publicationSummary(status?: string): string {
  const validation = status === "passed" ? "Файл проверен"
    : status === "failed" ? "Файл не прошёл проверку" : "Готовность файла не подтверждена";
  return `${validation}. Публикация выключена — доступен только dry-run.`;
}
