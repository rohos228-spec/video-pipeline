/**
 * Тексты панели «Публикации»: причины, ручные действия, исполнитель, общее состояние проекта.
 * Чистая логика без React и сети (тесты: web/src/lib/publication-text.test.mjs).
 *
 * PUBLICATION_CODE_RU равна `PUBLICATION_CODE_RU` из app/services/timeline_ru.py: человек видит одну и ту же
 * фразу в панели и в журнале. Равенство проверяет tests/w7_b_ui_texts_test.py.
 */

export const PUBLICATION_CODE_RU: Record<string, string> = {
  manual_confirmed: "Подтверждено человеком вручную.",
  manual_not_published: "Закрыто человеком как «не опубликовано».",
  publish_disabled: "Автопубликация выключена настройкой: ничего не отправлялось.",
  confirm_timeout: "Площадка не подтвердила публикацию вовремя. Проверьте площадку вручную.",
  profile_missing: "Для площадки не задан профиль MoreLogin. Укажите его в data/publish_accounts.json.",
  profile_unavailable: "Профиль MoreLogin недоступен: запустите MoreLogin и проверьте номер профиля.",
  page_unavailable: "Страница площадки не открылась. Проверьте интернет и профиль.",
  not_logged_in: "В профиле нет входа в аккаунт площадки. Войдите вручную и повторите.",
  captcha: "Площадка показывает капчу. Пройдите её вручную, автоповтора не будет.",
  two_factor: "Площадка просит код подтверждения (2FA). Введите его вручную, автоповтора не будет.",
  rate_limited: "Площадка ограничила частоту действий. Подождите и проверьте аккаунт вручную.",
  rejected: "Площадка отклонила публикацию. Проверьте аккаунт вручную.",
  upload_failed: "Ролик не загрузился на площадку.",
  processing_timeout: "Площадка не закончила обработку ролика за отведённое время, публикация не нажималась.",
  selector_not_found: "Не найден нужный элемент страницы (поле файла, подписи или кнопка). Интерфейс площадки мог измениться.",
  platform_limit: "Подпись или публикация не проходит по лимитам площадки.",
  daily_limit: "Достигнут лимит публикаций в сутки для площадки. Публикация перенесена.",
  outside_window: "Сейчас вне разрешённого окна публикации. Публикация перенесена.",
  caption_empty: "Подпись пуста: публикация не начата.",
  caption_field_missing: "Поле подписи не найдено, публикация не нажималась.",
  caption_fill_failed: "Не удалось заполнить подпись, публикация не нажималась.",
  caption_not_filled: "Подпись не появилась в поле после заполнения, публикация не нажималась.",
  accounts_config_invalid: "Конфиг аккаунтов публикации содержит ошибку.",
  click_not_allowed: "Журнал публикаций не разрешил нажатие.",
  no_success_marker: "Кнопка нажата, признак успеха не найден или не настроен. Проверьте площадку вручную.",
  confirmed_without_url: "Признак успеха есть, но ссылка на пост не получена. Проверьте площадку вручную.",
  exception_before_click: "Сбой до нажатия кнопки публикации.",
  exception_after_click: "Сбой после нажатия кнопки публикации: проверьте площадку вручную.",
};

/** Причина по коду. Незнакомый код не показывается латиницей: сказано, что причина записана в журнале. */
export function publicationCodeText(code: string | null | undefined): string {
  const c = (code ?? "").trim();
  if (!c) return "";
  return PUBLICATION_CODE_RU[c] ?? "Причина записана в журнале (код не расшифрован).";
}

/** Служебные коды ручных решений не считаются сбоем и отдельной «причиной» не показываются. */
export function isManualDecisionCode(code: string | null | undefined): boolean {
  return code === "manual_confirmed" || code === "manual_not_published";
}

const MANUAL_ACTION_RU: Record<string, string> = {
  confirm: "подтверждено вручную как опубликованное",
  mark_failed: "помечено вручную как «не опубликовано»",
  mark_failed_republish: "помечено вручную как «не опубликовано», повторная публикация разрешена",
};

export function manualActionText(action: string | null | undefined): string {
  return MANUAL_ACTION_RU[action ?? ""] ?? "ручное решение записано";
}

/**
 * Кто принял решение. Сервер пишет `operator` (имя не передано), `operator:<имя>` (имя передано заголовком
 * X-Action-Layer-Operator) или `telegram:<id>` (кнопка в Telegram). В Studio имени оператора из входа нет,
 * поэтому имя не выдумывается: «оператор».
 */
export function actorText(actor: string | null | undefined): string {
  const a = (actor ?? "").trim();
  if (a.startsWith("telegram:")) return `Telegram, пользователь ${a.slice("telegram:".length) || "?"}`;
  if (a.startsWith("operator:") && a.length > "operator:".length) return `оператор ${a.slice("operator:".length)}`;
  return "оператор";
}

export interface DerivedState {
  state: string;
  busy?: string[];
  needs_human?: string[];
  retry_approved?: string[];
  awaiting_approval?: string[];
  approved_publish_off?: string[];
}

const PROJECT_STATE_TEXT: Record<string, string> = {
  published: "Все площадки подтверждены: проект опубликован.",
  needs_human: "Ждёт проверки человека: откройте площадку и подтвердите вручную или отметьте «Не опубликовано».",
  awaiting_approval: "Публикация не начата: нужно ваше подтверждение на каждую площадку.",
  resolved_unpublished: "Оставшиеся площадки закрыты вручную как «не опубликовано».",
};

/**
 * Общее состояние проекта одной честной фразой. Сервер считает состояние `publishing` и когда площадка занята,
 * и когда она просто разрешена человеком и ждёт шага публикации. Если автопубликация выключена, шаг публикации
 * не пойдёт, поэтому «Публикация идёт» было бы неправдой.
 */
export function projectStateLines(derived: DerivedState | null | undefined, publishEnabled: boolean): string[] {
  if (!derived) return [];
  const lines: string[] = [];
  if (derived.state === "publishing") {
    if ((derived.busy ?? []).length > 0) lines.push("Публикация идёт.");
    else if (!publishEnabled)
      lines.push("Публикация разрешена вами, но выключена настройкой (автопубликация выключена): "
        + "ничего не будет опубликовано, пока её не включат.");
    else lines.push("Публикация разрешена и поставлена в очередь: её выполнит шаг публикации.");
    // «Ждёт проверки человека» не прячем за разрешённой площадкой.
    if ((derived.needs_human ?? []).length > 0) lines.push(PROJECT_STATE_TEXT.needs_human);
    return lines;
  }
  // Сервер при выключенной автопубликации не считает разрешённую площадку «в работе»: состояние awaiting_approval,
  // а разрешённые вами площадки перечислены отдельно.
  if (derived.state === "awaiting_approval" && (derived.approved_publish_off ?? []).length > 0) {
    lines.push("Публикация разрешена вами, но выключена настройкой (автопубликация выключена): "
      + "ничего не будет опубликовано, пока её не включат.");
    if ((derived.awaiting_approval ?? []).length > 0) lines.push(PROJECT_STATE_TEXT.awaiting_approval);
    return lines;
  }
  lines.push(PROJECT_STATE_TEXT[derived.state] ?? "Состояние проекта публикаций уточняется.");
  return lines;
}
