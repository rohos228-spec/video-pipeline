"use client";
import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError, http} from "@/lib/api";
import {Button} from "@/components/ui/button";
import {cn} from "@/lib/utils";
import {
  actorText, isManualDecisionCode, manualActionText, projectStateLines, publicationCodeText,
  type DerivedState,
} from "@/lib/publication-text";

// «Публикации»: что записано в журнале по каждой площадке и как человеку выйти из состояния «ждёт проверки».
// Сервер: app/web/routers/publications.py. Кнопки не кликают по площадкам: «Подтвердить вручную» и «Не опубликовано»
// только записывают решение человека (ссылка или причина обязательны), «Разрешить публикацию» выдаёт одноразовое «да»
// на эту площадку для этого файла и этой подписи.
interface PublicationManual {action: string; actor: string; reason: string | null; post_url: string | null; created_at: number}
interface PublicationItem {
  platform: string; state: string; state_label: string; status: string | null;
  post_url: string | null; post_id: string | null; error_code: string | null; attempts: number; clicked: boolean;
  approved: boolean; approved_by: string | null; account_id?: string | null; manual: PublicationManual | null;
  can_confirm_manual: boolean; can_mark_failed: boolean; can_approve: boolean;
}
interface PublicationFacts {
  project_id: number; project_status: string; publish_enabled: boolean; sha256: string | null;
  caption: string; caption_sha256: string; video: {artifact_uuid: string; url: string} | null;
  platforms: PublicationItem[]; derived: DerivedState | null;
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
    const d = (e.detail as {detail?: {message?: string}}).detail ?? (e.detail as {message?: string});
    if (d && typeof d.message === "string") return d.message;
  }
  return "Сервер отклонил действие. Обновите список и повторите.";
}

export function PublicationsSection({projectId}: {projectId: number | null}) {
  const project = projectId ?? 0;
  const [facts, setFacts] = useState<PublicationFacts | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [text, setText] = useState<Record<string, string>>({});
  const base = `/api/projects/${project}/publications`;
  // Какой проект открыт сейчас: ответ, пришедший после переключения, чужим проектом не показывается.
  const activeProject = useRef(project);
  activeProject.current = project;

  // Смена проекта: сообщение, ошибка, факты и введённый текст прошлого проекта не переезжают в новый.
  useEffect(() => {
    setFacts(null); setNote(""); setError(""); setBusy(""); setText({});
  }, [project]);

  const load = useCallback(async () => {
    if (!project) return;
    try {
      const data = await http<PublicationFacts>(base);
      if (activeProject.current !== project) return;
      setFacts(data);
      setError("");
    } catch {
      if (activeProject.current !== project) return;
      setError("Список публикаций недоступен.");
    }
  }, [base, project]);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await load();
      if (!cancelled) timer = setTimeout(tick, 8000);
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [load, project]);

  async function act(platform: string, kind: "confirm-manual" | "mark-failed" | "approve" | "revoke") {
    if (!facts?.sha256) return;
    const value = (text[platform] ?? "").trim();
    let path = `${base}/${platform}/${kind}`;
    let body: Record<string, unknown>;
    if (kind === "approve") {
      path = `${base}/approve`;
      body = {platforms: [platform], sha256: facts.sha256, caption_sha256: facts.caption_sha256};
    } else if (kind === "revoke") {
      body = {sha256: facts.sha256};
    } else if (kind === "confirm-manual") {
      body = /^https?:\/\//i.test(value) ? {sha256: facts.sha256, post_url: value} : {sha256: facts.sha256, reason: value};
    } else {
      body = {sha256: facts.sha256, reason: value};
    }
    const forProject = project;
    setBusy(platform + kind);
    try {
      const res = await http<{message?: string}>(path, {method: "POST", body: JSON.stringify(body)});
      if (activeProject.current !== forProject) return;
      setNote(res.message ?? "Готово.");
      setError("");
      setText((t) => ({...t, [platform]: ""}));
      await load();
    } catch (e) {
      if (activeProject.current === forProject) setError(errorText(e));
    } finally {
      if (activeProject.current === forProject) setBusy("");
    }
  }

  if (!projectId) return null;
  const stateLines = facts ? projectStateLines(facts.derived, facts.publish_enabled) : [];
  return (
    <section data-testid="publications-panel" className="mt-3 rounded border border-white/10 p-2 space-y-2 text-xs text-white/80">
      <h4>Публикации{facts && !facts.publish_enabled ? " · автопубликация выключена" : ""}</h4>
      {stateLines.map((line) => <p key={line} role="status">{line}</p>)}
      {facts?.sha256 && (
        <p className="text-white/50">
          Файл: {facts.sha256.slice(0, 12)}
          {facts.video && <> · <a className="underline" href={facts.video.url} target="_blank" rel="noreferrer">открыть видео</a></>}
          {" "}· подпись: {facts.caption.slice(0, 80)}
        </p>
      )}
      {facts && !facts.sha256 && <p className="text-white/50">Финального видео пока нет.</p>}
      <ul className="space-y-2">
        {facts?.platforms.map((p) => (
          <li key={p.platform} className="rounded border border-white/10 p-1.5 space-y-1">
            <div>
              <span className="font-medium">{p.platform}</span> · {p.state_label}
              {p.approved && <span className="text-emerald-300"> · разрешено вами</span>}
              {p.account_id && <span className="text-white/50"> · аккаунт: {p.account_id}</span>}
            </div>
            {p.approved && (
              <Button size="sm" variant="outline" data-testid={`revoke-${p.platform}`} disabled={busy !== ""}
                title="Отозвать разрешение до того, как шаг публикации его использовал: публикации не будет"
                onClick={() => void act(p.platform, "revoke")}>
                Отозвать разрешение
              </Button>
            )}
            {p.post_url && <div>Ссылка: <a className="underline" href={p.post_url} target="_blank" rel="noreferrer">{p.post_url}</a></div>}
            {p.error_code && !isManualDecisionCode(p.error_code) && (
              <div className="text-amber-200">Причина: {publicationCodeText(p.error_code)}</div>
            )}
            {p.manual && (
              <div className="text-white/50">
                Вручную: {manualActionText(p.manual.action)} · {actorText(p.manual.actor)}{p.manual.reason ? ` · ${p.manual.reason}` : ""}
              </div>
            )}
            {(p.can_confirm_manual || p.can_mark_failed) && (
              <div className="space-y-1">
                <input aria-label={`Ссылка на пост или причина: ${p.platform}`} value={text[p.platform] ?? ""}
                  onChange={(e) => setText((t) => ({...t, [p.platform]: e.target.value}))}
                  placeholder="ссылка на пост (https://…) или причина, не короче 5 символов" maxLength={500}
                  className="w-full rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30"/>
                <span className="space-x-2">
                  {p.can_confirm_manual && (
                    <Button size="sm" variant="outline" disabled={busy !== "" || (text[p.platform] ?? "").trim().length < 5}
                      onClick={() => void act(p.platform, "confirm-manual")}>Подтвердить вручную</Button>
                  )}
                  {p.can_mark_failed && (
                    <Button size="sm" variant="outline" disabled={busy !== "" || (text[p.platform] ?? "").trim().length < 5}
                      onClick={() => void act(p.platform, "mark-failed")}>Не опубликовано</Button>
                  )}
                </span>
              </div>
            )}
            {p.can_approve && !p.approved && (
              <Button size="sm" variant="outline" disabled={busy !== ""} onClick={() => void act(p.platform, "approve")}>
                Разрешить публикацию
              </Button>
            )}
          </li>
        ))}
      </ul>
      {note && <p role="status" className="text-emerald-200">{note}</p>}
      {error && <p role="alert" className={cn("text-amber-200")}>{error}</p>}
    </section>
  );
}
