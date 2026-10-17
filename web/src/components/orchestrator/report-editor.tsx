"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  getOrchReport,
  listOrchReports,
  OrchApiError,
  postCreateReport,
  postRollbackReport,
  postUpdateReport,
  readOrchBoot,
  readOrchToken,
  type ReportDetail,
  type ReportSection,
} from "@/lib/orchestratorApi";
import { reportPreviewDocument } from "@/lib/orchestratorSanitize";
import type { OrchReportsRow } from "@/lib/orchestratorTypes";

function stamp(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}

export function ReportEditor() {
  const boot = useMemo(() => readOrchBoot(), []);
  const [projectId, setProjectId] = useState(13);
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [reports, setReports] = useState<OrchReportsRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [sections, setSections] = useState<ReportSection[]>([]);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [createTitle, setCreateTitle] = useState("");
  const [createHeading, setCreateHeading] = useState("");
  const [createHtml, setCreateHtml] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const project = Number(params.get("project") || "13");
    setProjectId(Number.isFinite(project) && project > 0 ? project : 13);
    setToken(readOrchToken());
    setReady(true);
  }, []);

  const cfg = useMemo(
    () => ({ apiBase: boot.apiBase, session: boot.session, token, scenario: boot.scenario }),
    [boot.apiBase, boot.scenario, boot.session, token],
  );

  const loadList = useCallback(async () => {
    const data = await listOrchReports(cfg, projectId);
    setReports(data.reports);
    return data.reports;
  }, [cfg, projectId]);

  const openReport = useCallback(
    async (id: number) => {
      const data = await getOrchReport(cfg, id);
      setSelectedId(id);
      setDetail(data);
      setSections(data.sections.map((section) => ({ ...section })));
    },
    [cfg],
  );

  useEffect(() => {
    if (!ready || !token) return;
    let cancelled = false;
    void (async () => {
      try {
        const list = await loadList();
        if (cancelled) return;
        const first = list[0];
        if (first) await openReport(first.id);
        setError("");
      } catch (err) {
        if (!cancelled) setError(err instanceof OrchApiError ? err.message : "не загрузилось");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadList, openReport, ready, token]);

  useEffect(() => {
    setPreview(reportPreviewDocument(sections));
  }, [sections]);

  const save = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      await postUpdateReport(cfg, {
        report_id: detail.report.id,
        sections_patch: sections,
        reason: "правка из редактора",
      });
      await openReport(detail.report.id);
      await loadList();
      setError("");
    } catch (err) {
      setError(err instanceof OrchApiError ? err.message : "не сохранилось");
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    if (!createTitle.trim()) return;
    setBusy(true);
    try {
      const created = await postCreateReport(cfg, {
        title: createTitle.trim(),
        project_id: projectId,
        sections: [{ heading: createHeading, html: createHtml }],
      });
      setCreateTitle("");
      setCreateHeading("");
      setCreateHtml("");
      await loadList();
      await openReport(created.report_id);
      setError("");
    } catch (err) {
      setError(err instanceof OrchApiError ? err.message : "не создалось");
    } finally {
      setBusy(false);
    }
  };

  const rollback = async (version: number) => {
    if (!detail) return;
    setBusy(true);
    try {
      await postRollbackReport(cfg, detail.report.id, version);
      await openReport(detail.report.id);
      await loadList();
      setError("");
    } catch (err) {
      setError(err instanceof OrchApiError ? err.message : "не откатилось");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background" data-testid="orch-reports">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-white/[0.06] px-4">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-semibold tracking-tight text-white">Отчёты</span>
          <Badge variant="muted" className="normal-case tracking-normal">
            проект {projectId}
          </Badge>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="text-xs"
          onClick={() => {
            const params = new URLSearchParams(window.location.search);
            params.delete("project");
            const q = params.toString();
            window.location.assign(q ? `/orchestrator?${q}` : "/orchestrator");
          }}
        >
          К чату
        </Button>
      </div>

      {!ready ? null : !token ? (
        <p className="p-6 text-xs text-white/50" data-testid="orch-reports-token">
          Нужен токен оркестратора. Откройте чат и сохраните его.
        </p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_340px] overflow-hidden">
          <aside className="flex min-h-0 flex-col overflow-hidden border-r border-white/[0.06]" data-testid="orch-report-list">
            <div className="shrink-0 space-y-2 border-b border-white/[0.06] p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/40">Новый отчёт</div>
              <Input
                data-testid="orch-create-title"
                value={createTitle}
                onChange={(event) => setCreateTitle(event.target.value)}
                placeholder="Название"
                className="h-8 text-xs"
              />
              <Input
                data-testid="orch-create-heading"
                value={createHeading}
                onChange={(event) => setCreateHeading(event.target.value)}
                placeholder="Заголовок секции"
                className="h-8 text-xs"
              />
              <Textarea
                data-testid="orch-create-html"
                value={createHtml}
                onChange={(event) => setCreateHtml(event.target.value)}
                placeholder="HTML секции"
                className="min-h-[72px] text-xs"
                rows={3}
              />
              <Button type="button" size="sm" className="text-xs" data-testid="orch-report-create" disabled={busy || !createTitle.trim()} onClick={() => void create()}>
                Создать
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {reports.length === 0 ? <p className="text-[11px] text-white/35">Нет отчётов.</p> : null}
              {reports.map((report) => (
                <button
                  key={report.id}
                  type="button"
                  data-testid="orch-report-item"
                  className={`mb-2 w-full rounded-lg border px-2.5 py-2 text-left ${
                    report.id === selectedId ? "border-primary/50 bg-white/[0.05]" : "border-white/[0.08]"
                  }`}
                  onClick={() => void openReport(report.id)}
                >
                  <div className="truncate text-xs font-medium text-white/90">{report.title}</div>
                  <div className="mt-1 text-[10px] text-white/40">
                    v{report.version} · {stamp(report.updated_at)}
                  </div>
                </button>
              ))}
            </div>
          </aside>

          <section className="min-h-0 overflow-y-auto px-4 py-3">
            {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
            {detail ? (
              <>
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h1 className="truncate text-sm font-semibold text-white" data-testid="orch-report-title" data-report-id={detail.report.id}>
                    {detail.report.title}
                  </h1>
                  <Button type="button" size="sm" className="text-xs" data-testid="orch-report-save" disabled={busy || detail.report.editable === 0} onClick={() => void save()}>
                    Сохранить
                  </Button>
                </div>
                {sections.map((section, index) => (
                  <div key={`${detail.report.id}-${index}`} className="mb-3 space-y-2">
                    <Input
                      data-testid="orch-section-heading"
                      value={section.heading}
                      onChange={(event) => {
                        const next = sections.slice();
                        next[index] = { ...section, heading: event.target.value };
                        setSections(next);
                      }}
                      className="h-8 text-xs"
                    />
                    <Textarea
                      data-testid="orch-section-html"
                      value={section.html}
                      onChange={(event) => {
                        const next = sections.slice();
                        next[index] = { ...section, html: event.target.value };
                        setSections(next);
                      }}
                      className="min-h-[140px] font-mono text-xs"
                      spellCheck={false}
                    />
                  </div>
                ))}
              </>
            ) : (
              <p className="text-xs text-white/35">Выберите отчёт слева.</p>
            )}
          </section>

          <aside className="flex min-h-0 flex-col border-l border-white/[0.06] bg-black/30">
            <div className="border-b border-white/[0.06] p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/40">Просмотр</div>
              <iframe
                title="Просмотр отчёта"
                data-testid="orch-report-preview"
                sandbox=""
                srcDoc={preview}
                className="mt-2 h-52 w-full rounded-md border border-white/[0.08] bg-white"
              />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/40">Версии</div>
              {detail?.versions.map((version) => (
                <div key={version.id} className="mt-2 flex items-center justify-between gap-2 text-[11px] text-white/70" data-testid="orch-report-version">
                  <span>
                    v{version.version}
                    {version.version === detail.report.version ? " · текущая" : ""} · {stamp(version.created_at)}
                  </span>
                  {version.version !== detail.report.version ? (
                    <Button type="button" variant="outline" size="sm" className="h-7 text-[10px]" data-testid="orch-report-rollback" disabled={busy} onClick={() => void rollback(version.version)}>
                      Откат
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
