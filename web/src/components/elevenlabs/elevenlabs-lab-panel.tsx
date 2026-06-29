"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudioWaveform,
  type TimeRange,
} from "@/components/elevenlabs/audio-waveform";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  backendFetchHint,
  defaultLabSettings,
  defaultLibraryFilters,
  elevenLabsLab,
  extractClipWithFallback,
  LIBRARY_ACCENT_OPTIONS,
  LIBRARY_AGE_OPTIONS,
  LIBRARY_GENDER_OPTIONS,
  LIBRARY_LANGUAGE_OPTIONS,
  LIBRARY_SORT_OPTIONS,
  loadLabSettings,
  type LibraryVoiceFilters,
  proxyUrlFromSettings,
  saveLabSettings,
  type LabSettings,
  type RemoteVoice,
  type SavedVoice,
} from "@/lib/elevenlabs-lab-api";
import { cn } from "@/lib/utils";
import {
  AlertCircle,
  AudioWaveform as AudioIcon,
  CheckCircle2,
  GripHorizontal,
  Loader2,
  Plug,
  RotateCcw,
  Scissors,
  Trash2,
  Undo2,
  Upload,
  Wand2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

const DOCK_HEIGHT_KEY = "vp-elevenlabs-dock-height";

type ConnectStatus = "idle" | "checking" | "ok" | "error";

type PendingPreview = {
  runId: string;
  patchFilename: string;
  patchUrl: string;
  spokenText: string;
  patchDurationS: number;
  voiceId: string;
  replaceRange: TimeRange;
};

type LastCloneResult = {
  voiceId: string;
  name: string;
  saved: boolean;
};

function blobUrl(file: File | null) {
  return file ? URL.createObjectURL(file) : null;
}

export function ElevenLabsLabPanel() {
  const [settings, setSettings] = useState<LabSettings>(defaultLabSettings);
  const [connectStatus, setConnectStatus] = useState<ConnectStatus>("idle");
  const [connectMessage, setConnectMessage] = useState("Не проверено");
  const [accountVerdict, setAccountVerdict] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [envKeyHint, setEnvKeyHint] = useState<string | null>(null);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [cloneSampleFile, setCloneSampleFile] = useState<File | null>(null);
  const [duration, setDuration] = useState(0);
  const [replaceRanges, setReplaceRanges] = useState<TimeRange[]>([]);
  const [cloneRanges, setCloneRanges] = useState<TimeRange[]>([]);
  const [selectedReplaceIdx, setSelectedReplaceIdx] = useState(-1);
  const [selectedCloneIdx, setSelectedCloneIdx] = useState(-1);
  const [replacePast, setReplacePast] = useState<TimeRange[][]>([]);
  const [clonePast, setClonePast] = useState<TimeRange[][]>([]);
  const [activeTool, setActiveTool] = useState<"replace" | "clone">("replace");
  const [lastCutDurationS, setLastCutDurationS] = useState<number | null>(null);
  const [cloneSamplePreview, setCloneSamplePreview] = useState<string | null>(null);
  const [dockHeight, setDockHeight] = useState(280);
  const [voiceName, setVoiceName] = useState("Lab Voice");
  const [fragmentText, setFragmentText] = useState("");
  const [oldWord, setOldWord] = useState("");
  const [newWord, setNewWord] = useState("");
  const [savedVoices, setSavedVoices] = useState<SavedVoice[]>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [pendingPreview, setPendingPreview] = useState<PendingPreview | null>(null);
  const [lastCloneResult, setLastCloneResult] = useState<LastCloneResult | null>(null);
  const [remoteVoices, setRemoteVoices] = useState<RemoteVoice[]>([]);
  const [libraryFilter, setLibraryFilter] = useState("");
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [libraryPages, setLibraryPages] = useState(5);
  const [libraryMeta, setLibraryMeta] = useState<string | null>(null);
  const [libraryFilters, setLibraryFilters] = useState<LibraryVoiceFilters>(defaultLibraryFilters);
  const [ttsText, setTtsText] = useState("Привет! Это тест озвучки выбранного голоса.");
  const [ttsResultUrl, setTtsResultUrl] = useState<string | null>(null);
  const [ttsDuration, setTtsDuration] = useState<number | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const resizeRef = useRef<{ startY: number; startH: number } | null>(null);

  useEffect(() => {
    const loaded = loadLabSettings();
    setSettings(loaded);
    const h = Number(localStorage.getItem(DOCK_HEIGHT_KEY));
    if (h >= 180 && h <= 600) setDockHeight(h);

    if (typeof window !== "undefined" && window.location.protocol === "file:") {
      setBackendOnline(false);
      setConnectStatus("error");
      setConnectMessage(backendFetchHint());
      return;
    }

    void elevenLabsLab.pingBackend().then(
      () => setBackendOnline(true),
      () => {
        setBackendOnline(false);
        setConnectStatus("error");
        setConnectMessage(backendFetchHint());
      },
    );

    elevenLabsLab.savedVoices().then((r) => setSavedVoices(r.voices)).catch(() => {});
    elevenLabsLab.status().then((s) => {
      setEnvKeyHint(s.api_key_hint ?? null);
      if (s.env_key_configured) {
        void checkConnection(false, loaded, "env");
      } else if (loaded.apiKey.trim()) {
        void checkConnection(false, loaded, "ui");
      } else {
        setConnectStatus("idle");
        setConnectMessage("Укажите API key или добавьте ELEVENLABS_API_KEY в .env");
      }
    }).catch(() => {});
  }, []);

  const persistSettings = (patch: Partial<LabSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      saveLabSettings(next);
      return next;
    });
  };

  const apiOpts = useMemo(
    () => ({
      api_key: settings.apiKey.trim() || undefined,
      proxy_url: proxyUrlFromSettings(settings),
    }),
    [settings],
  );

  const refreshSavedVoices = async () => {
    const list = await elevenLabsLab.savedVoices();
    setSavedVoices(list.voices);
  };

  const selectVoice = (voiceId: string, name?: string) => {
    setSelectedVoiceId(voiceId);
    if (name) setVoiceName(name);
  };

  const loadLibrary = async (pages = libraryPages) => {
    if (connectStatus !== "ok") {
      toast.error("Сначала «Проверить API»");
      return;
    }
    setBusy("library");
    try {
      const r = await elevenLabsLab.remoteVoices({
        ...apiOpts,
        scope: "all",
        max_pages: pages,
        search: libraryFilter.trim() || undefined,
        gender: libraryFilters.gender || undefined,
        age: libraryFilters.age || undefined,
        language: libraryFilters.language || undefined,
        accent: libraryFilters.accent || undefined,
        sort: libraryFilters.sort || undefined,
      });
      setRemoteVoices(r.voices);
      setLibraryLoaded(true);
      setLibraryPages(pages);
      const parts = [
        r.api_total_count != null ? `в API ~${r.api_total_count}` : null,
        r.total_count != null ? `показано ${r.total_count}` : `${r.voices.length} голосов`,
        r.account_count != null ? `аккаунт ${r.account_count}` : null,
        r.library_count != null ? `библиотека ${r.library_count}` : null,
      ].filter(Boolean);
      setLibraryMeta(parts.join(" · "));
    } catch (e) {
      toast.error(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(null);
    }
  };

  const runTts = async (textOverride?: string) => {
    const text = (textOverride ?? ttsText).trim();
    if (!selectedVoiceId.trim()) {
      toast.error("Выберите голос (voice_id)");
      return;
    }
    if (!text) {
      toast.error("Введите текст для озвучки");
      return;
    }
    setBusy("tts");
    try {
      const r = await elevenLabsLab.tts({
        text,
        voice_id: selectedVoiceId.trim(),
        ...apiOpts,
      });
      setTtsResultUrl(r.preview_url);
      setTtsDuration(r.duration_s);
      toast.success(`Озвучка готова · ${r.duration_s.toFixed(1)} s`);
    } catch (e) {
      toast.error(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(null);
    }
  };

  const saveRemoteVoice = async (v: RemoteVoice) => {
    try {
      await elevenLabsLab.saveVoice({
        name: v.name,
        voice_id: v.voice_id,
        meta: { source: "library", category: v.category },
      });
      await refreshSavedVoices();
      selectVoice(v.voice_id, v.name);
      toast.success(`В «Мои клоны»: ${v.name}`);
    } catch (e) {
      toast.error(String(e).replace(/^Error:\s*/, ""));
    }
  };

  const filteredRemote = useMemo(() => {
    const q = libraryFilter.trim().toLowerCase();
    if (!q) return remoteVoices;
    return remoteVoices.filter(
      (v) =>
        v.name.toLowerCase().includes(q) ||
        v.voice_id.toLowerCase().includes(q) ||
        (v.category || "").toLowerCase().includes(q) ||
        (v.gender || "").toLowerCase().includes(q) ||
        (v.language || "").toLowerCase().includes(q) ||
        (v.accent || "").toLowerCase().includes(q),
    );
  }, [remoteVoices, libraryFilter]);

  const voiceMetaLine = (v: RemoteVoice) =>
    [v.gender, v.age, v.language, v.accent, v.category].filter(Boolean).join(" · ") || "—";

  const sourcePreview = useMemo(() => blobUrl(sourceFile), [sourceFile]);

  useEffect(() => {
    return () => {
      if (sourcePreview) URL.revokeObjectURL(sourcePreview);
    };
  }, [sourcePreview]);

  const checkConnection = async (
    manual: boolean,
    cfg: LabSettings = settings,
    mode: "ui" | "env" = "ui",
  ) => {
    setConnectStatus("checking");
    setConnectMessage(mode === "env" ? "Проверка ключа из .env…" : "Проверка API…");
    try {
      await elevenLabsLab.pingBackend();
      setBackendOnline(true);
      const r =
        mode === "env" && !cfg.apiKey.trim()
          ? await elevenLabsLab.checkEnv()
          : await elevenLabsLab.connect({
              api_key: cfg.apiKey.trim() || undefined,
              proxy_url: proxyUrlFromSettings(cfg) || cfg.proxyUrl.trim() || undefined,
              proxy_ip: cfg.proxyIp.trim() || undefined,
              proxy_port: cfg.proxyIp.trim() && cfg.proxyPort ? Number(cfg.proxyPort) : undefined,
              proxy_user: cfg.proxyUser.trim() || undefined,
              proxy_password: cfg.proxyPassword.trim() || undefined,
              proxy_scheme: cfg.proxyScheme,
            });
      const src = r.key_source === "ui" ? "форма UI" : r.key_source === "env" ? ".env" : "?";
      const modeLabel =
        r.connection_mode === "direct"
          ? "напрямую"
          : r.connection_mode === "ui_proxy"
            ? "через proxy UI"
            : r.connection_mode === "env_alt"
              ? "через proxy .env (запасной)"
              : r.connection_mode === "env_proxy"
                ? "через proxy .env"
                : "ok";
      setConnectStatus("ok");
      const note = r.note ? ` · ${r.note}` : "";
      const tier = r.subscription_tier ? ` · ${r.subscription_tier}` : "";
      const ivc =
        r.can_use_instant_voice_cloning === true
          ? " · IVC ✓"
          : r.can_use_instant_voice_cloning === false
            ? " · IVC ✗ (Starter $6+)"
            : "";
      setConnectMessage(
        `OK · ключ ${r.key_hint ?? src} (${src}) · ${modeLabel} · аккаунт ${r.voice_count ?? "?"} голосов${tier}${ivc}${note}`,
      );
      try {
        const d = await elevenLabsLab.accountDiag(apiOpts);
        setAccountVerdict(d.verdict ?? d.note ?? null);
        if (d.can_use_instant_voice_cloning === false && manual) {
          toast.error(d.verdict ?? "Instant Voice Clone недоступен для этого API key");
        }
      } catch {
        setAccountVerdict(null);
      }
      if (manual) toast.success("ElevenLabs API доступен");
    } catch (e) {
      setConnectStatus("error");
      const msg = String(e).replace(/^Error:\s*/, "");
      setConnectMessage(msg);
      if (manual) toast.error(msg);
    }
  };

  const useEnvKey = () => {
    persistSettings({ apiKey: "" });
    toast.message("Поле API key очищено — используется ELEVENLABS_API_KEY из .env");
    void checkConnection(true, { ...settings, apiKey: "" }, "env");
  };

  const pushSelectionHistory = (tool: "replace" | "clone", prev: TimeRange[]) => {
    if (tool === "replace") setReplacePast((h) => [...h, prev]);
    else setClonePast((h) => [...h, prev]);
  };

  const activeReplaceRange =
    selectedReplaceIdx >= 0 ? replaceRanges[selectedReplaceIdx] : replaceRanges.at(-1) ?? null;
  const activeCloneRange =
    selectedCloneIdx >= 0 ? cloneRanges[selectedCloneIdx] : cloneRanges.at(-1) ?? null;
  const cutTargetRange = activeCloneRange ?? activeReplaceRange;
  const cutTargetDurationS = cutTargetRange
    ? Math.max(0, cutTargetRange.end - cutTargetRange.start)
    : null;

  const undoSelection = () => {
    if (activeTool === "replace") {
      setReplacePast((h) => {
        if (h.length === 0) return h;
        const next = [...h];
        const prev = next.pop() ?? [];
        setReplaceRanges(prev);
        setSelectedReplaceIdx(prev.length - 1);
        return next;
      });
    } else if (activeTool === "clone") {
      setClonePast((h) => {
        if (h.length === 0) return h;
        const next = [...h];
        const prev = next.pop() ?? [];
        setCloneRanges(prev);
        setSelectedCloneIdx(prev.length - 1);
        return next;
      });
    }
  };

  const resetAllSelections = () => {
    if (replaceRanges.length) pushSelectionHistory("replace", replaceRanges);
    if (cloneRanges.length) pushSelectionHistory("clone", cloneRanges);
    setReplaceRanges([]);
    setCloneRanges([]);
    setSelectedReplaceIdx(-1);
    setSelectedCloneIdx(-1);
  };

  const deleteRange = (tool: "replace" | "clone", index: number) => {
    if (tool === "replace") {
      pushSelectionHistory("replace", replaceRanges);
      const next = replaceRanges.filter((_, i) => i !== index);
      setReplaceRanges(next);
      setSelectedReplaceIdx(next.length ? Math.min(index, next.length - 1) : -1);
    } else {
      pushSelectionHistory("clone", cloneRanges);
      const next = cloneRanges.filter((_, i) => i !== index);
      setCloneRanges(next);
      setSelectedCloneIdx(next.length ? Math.min(index, next.length - 1) : -1);
    }
  };

  const onDockResizeStart = (e: React.PointerEvent) => {
    resizeRef.current = { startY: e.clientY, startH: dockHeight };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onDockResizeMove = (e: React.PointerEvent) => {
    const r = resizeRef.current;
    if (!r) return;
    const next = Math.min(560, Math.max(180, r.startH + (r.startY - e.clientY)));
    setDockHeight(next);
  };

  const onDockResizeEnd = () => {
    if (resizeRef.current) {
      localStorage.setItem(DOCK_HEIGHT_KEY, String(dockHeight));
    }
    resizeRef.current = null;
  };

  const cutCloneSample = async () => {
    if (!sourceFile || !cutTargetRange) {
      toast.error("Загрузите дорожку и выделите фрагмент на waveform");
      return;
    }
    if (cutTargetRange.end - cutTargetRange.start < 0.05) {
      toast.error("Выделение слишком короткое (минимум 0.05 s)");
      return;
    }
    setBusy("cut");
    try {
      const r = await extractClipWithFallback(
        sourceFile,
        cutTargetRange.start,
        cutTargetRange.end,
      );
      const ext = r.blob.type.includes("wav") ? "wav" : "mp3";
      setCloneSampleFile(new File([r.blob], `clone_sample.${ext}`, { type: r.blob.type }));
      setLastCutDurationS(r.duration_s);
      setCloneSamplePreview(URL.createObjectURL(r.blob));
      toast.success(
        `Образец ${r.duration_s.toFixed(2)} s${r.via === "local" ? " (локально)" : ""}`,
      );
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(null);
    }
  };

  const runCloneOnly = async () => {
    const sample = cloneSampleFile;
    if (!sample) {
      toast.error("Нет образца — выделите кусок и «Нарезать для клона»");
      return;
    }
    setBusy("clone");
    setLastCloneResult(null);
    try {
      const r = await elevenLabsLab.clone(voiceName, sample, apiOpts);
      setSelectedVoiceId(r.clone.voice_id);
      await refreshSavedVoices();
      setLastCloneResult({
        voiceId: r.clone.voice_id,
        name: voiceName,
        saved: Boolean(r.saved),
      });
      toast.success(`Клон создан: ${r.clone.voice_id.slice(0, 12)}…`);
    } catch (e) {
      const msg = String(e).replace(/^Error:\s*/, "");
      toast.error(msg.includes("Internal Server Error") ? `${msg} — перезапусти backend (GO.cmd)` : msg);
    } finally {
      setBusy(null);
    }
  };

  const runPreview = async () => {
    if (!sourceFile || !activeReplaceRange) {
      toast.error("Выделите фрагмент замены на дорожке");
      return;
    }
    const sample = cloneSampleFile;
    if (!sample && !selectedVoiceId) {
      toast.error("Нужен образец клона или сохранённый voice_id");
      return;
    }
    if (!fragmentText.trim() || !oldWord.trim() || !newWord.trim()) {
      toast.error("Заполните текст фрагмента и слова");
      return;
    }
    setBusy("preview");
    try {
      const r = await elevenLabsLab.previewRedub({
        voice_name: voiceName,
        sample: sample ?? sourceFile,
        fragment_text: fragmentText,
        old_word: oldWord,
        new_word: newWord,
        voice_id: selectedVoiceId || undefined,
        ...apiOpts,
      });
      setSelectedVoiceId(r.voice_id);
      await refreshSavedVoices();
      setPendingPreview({
        runId: r.run_id,
        patchFilename: r.patch_filename,
        patchUrl: r.patch_preview_url,
        spokenText: r.spoken_text,
        patchDurationS: r.patch_duration_s,
        voiceId: r.voice_id,
        replaceRange: { ...activeReplaceRange },
      });
      toast.success("Превью готово — прослушайте и одобрите");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(null);
    }
  };

  const approvePreview = async () => {
    if (!pendingPreview || !sourceFile) return;
    setBusy("apply");
    try {
      const r = await elevenLabsLab.applyRedub({
        source_audio: sourceFile,
        patch_filename: pendingPreview.patchFilename,
        start_s: pendingPreview.replaceRange.start,
        end_s: pendingPreview.replaceRange.end,
        run_id: pendingPreview.runId,
      });
      setResultUrl(r.preview_url);
      const resp = await fetch(r.preview_url);
      const blob = await resp.blob();
      const merged = new File([blob], sourceFile.name.replace(/(\.\w+)?$/, "_redub.mp3"), {
        type: "audio/mpeg",
      });
      setSourceFile(merged);
      setPendingPreview(null);
      toast.success("Склейка применена — дорожка обновлена");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(null);
    }
  };

  const rejectPreview = () => {
    setPendingPreview(null);
    toast.message("Превью отклонено — исходная дорожка не изменена");
  };

  const onDeleteVoice = async (id: string) => {
    await elevenLabsLab.deleteVoice(id);
    setSavedVoices((v) => v.filter((x) => x.id !== id));
    if (lastCloneResult && savedVoices.find((x) => x.id === id)?.voice_id === lastCloneResult.voiceId) {
      setLastCloneResult(null);
    }
  };

  const loadSource = (f: File | null) => {
    setSourceFile(f);
    setPendingPreview(null);
    setLastCloneResult(null);
    setResultUrl(null);
    setLastCutDurationS(null);
    if (cloneSamplePreview) URL.revokeObjectURL(cloneSamplePreview);
    setCloneSamplePreview(null);
    setCloneSampleFile(null);
    resetAllSelections();
  };

  useEffect(() => {
    return () => {
      if (cloneSamplePreview) URL.revokeObjectURL(cloneSamplePreview);
    };
  }, [cloneSamplePreview]);

  const canvasHeight = Math.max(100, dockHeight - 120);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <div className="border-b border-white/10 bg-card/20 px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/15 text-primary">
              <AudioIcon className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">ElevenLabs Lab</h1>
              <p className="text-xs text-muted-foreground">
                API · waveform · клон · замена с превью
              </p>
            </div>
          </div>
          <ConnectBadge status={connectStatus} message={connectMessage} />
        </div>
        {backendOnline === false ? (
          <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
            {backendFetchHint()} CHECK-PROXY.cmd проверяет только Python — для кнопки «Проверить API» нужен запущенный backend.
          </div>
        ) : null}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-12 gap-4 overflow-auto p-4 pb-2">
        <section className="col-span-12 space-y-4 lg:col-span-3">
          <Panel title="Подключение" icon={Plug}>
            <Field label="API key (или .env)">
              <Input
                type="password"
                value={settings.apiKey}
                onChange={(e) => persistSettings({ apiKey: e.target.value })}
                placeholder={envKeyHint ? `из .env: ${envKeyHint}` : "sk_…"}
                className="h-8 text-xs"
              />
            </Field>
            {envKeyHint ? (
              <p className="text-[10px] text-muted-foreground">
                В .env найден ключ {envKeyHint}. Пустое поле = ключ из .env.
              </p>
            ) : (
              <p className="text-[10px] text-amber-400/90">
                ELEVENLABS_API_KEY в .env не найден — вставьте sk_… в поле или в .env
              </p>
            )}
            <Field label="Proxy — тип">
              <select
                value={settings.proxyScheme}
                onChange={(e) =>
                  persistSettings({ proxyScheme: e.target.value === "http" ? "http" : "socks5" })
                }
                className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-xs"
              >
                <option value="http">HTTP (рекомендуется)</option>
                <option value="socks5">SOCKS5</option>
              </select>
            </Field>
            <Field label="Proxy URL целиком (http://… — необязательно)">
              <Input
                value={settings.proxyUrl}
                onChange={(e) => persistSettings({ proxyUrl: e.target.value })}
                placeholder="http://user:pass@154.196.58.31:64240"
                className="h-8 font-mono text-[11px]"
              />
            </Field>
            <Field label="Proxy IP">
              <Input
                value={settings.proxyIp}
                onChange={(e) => persistSettings({ proxyIp: e.target.value })}
                placeholder="1.2.3.4"
                className="h-8 text-xs"
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Port">
                <Input
                  value={settings.proxyPort}
                  onChange={(e) => persistSettings({ proxyPort: e.target.value })}
                  className="h-8 text-xs"
                />
              </Field>
              <Field label="User">
                <Input
                  value={settings.proxyUser}
                  onChange={(e) => persistSettings({ proxyUser: e.target.value })}
                  className="h-8 text-xs"
                />
              </Field>
            </div>
            <Field label="Proxy pass">
              <Input
                type="password"
                value={settings.proxyPassword}
                onChange={(e) => persistSettings({ proxyPassword: e.target.value })}
                className="h-8 text-xs"
              />
            </Field>
            <p className="text-[10px] text-muted-foreground">
              SOCKS5 или HTTP. В .env: ELEVENLABS_PROXY_URL=http://user:pass@host:port
            </p>
            <Button
              size="sm"
              className="w-full"
              onClick={() => void checkConnection(true, settings, settings.apiKey.trim() ? "ui" : "env")}
              disabled={connectStatus === "checking"}
            >
              {connectStatus === "checking" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              Проверить API
            </Button>
            {settings.apiKey.trim() ? (
              <Button size="sm" variant="outline" className="w-full" onClick={useEnvKey}>
                Использовать ключ из .env
              </Button>
            ) : null}
            <p className="text-[10px] text-muted-foreground">
              IVC на сайте:{" "}
              <a
                href="https://elevenlabs.io/app/voice-library"
                target="_blank"
                rel="noreferrer"
                className="text-primary underline"
              >
                Voice Library → Add → Instant Voice Clone
              </a>
            </p>
            <ConnectBadge status={connectStatus} message={connectMessage} compact />
            {accountVerdict ? (
              <p className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-100/90">
                {accountVerdict}
              </p>
            ) : null}
          </Panel>

          <Panel title="Мои клоны (хранятся локально)">
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                className="flex-1"
                disabled={busy !== null}
                onClick={() => void refreshSavedVoices()}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Обновить
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Файл: data/elevenlabs_lab/voices.json · образец mp3 рядом с клоном
            </p>
            <div className="max-h-56 space-y-2 overflow-auto">
              {savedVoices.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  После «Только клонировать» голос появится здесь навсегда
                </p>
              ) : (
                savedVoices.map((v) => (
                  <div
                    key={v.id}
                    className={cn(
                      "rounded-md border px-2 py-2 text-xs",
                      selectedVoiceId === v.voice_id
                        ? "border-primary/50 bg-primary/10"
                        : "border-white/5 bg-white/[0.02]",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => selectVoice(v.voice_id, v.name)}
                      >
                        <div className="font-medium">{v.name}</div>
                        <div className="font-mono text-[10px] text-muted-foreground">{v.voice_id}</div>
                        {v.created_at ? (
                          <div className="text-[10px] text-muted-foreground">
                            {new Date(v.created_at).toLocaleString("ru-RU")}
                          </div>
                        ) : null}
                      </button>
                      <Trash2
                        className="h-3.5 w-3.5 shrink-0 cursor-pointer text-muted-foreground hover:text-red-400"
                        onClick={() => void onDeleteVoice(v.id)}
                      />
                    </div>
                    {v.sample_preview_url ? (
                      <audio controls src={v.sample_preview_url} className="mt-2 h-8 w-full" />
                    ) : (
                      <p className="mt-1 text-[10px] text-muted-foreground">Образец клона не сохранён</p>
                    )}
                  </div>
                ))
              )}
            </div>
            <Field label="Выбранный voice_id (для озвучки и замены)">
              <Input
                value={selectedVoiceId}
                onChange={(e) => setSelectedVoiceId(e.target.value)}
                className="h-8 font-mono text-[11px]"
              />
            </Field>
          </Panel>

          <Panel title="Библиотека ElevenLabs">
            <div className="grid grid-cols-2 gap-2">
              <select
                value={libraryFilters.gender}
                onChange={(e) => setLibraryFilters((f) => ({ ...f, gender: e.target.value }))}
                className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-[11px]"
              >
                {LIBRARY_GENDER_OPTIONS.map((o) => (
                  <option key={o.value || "any"} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <select
                value={libraryFilters.age}
                onChange={(e) => setLibraryFilters((f) => ({ ...f, age: e.target.value }))}
                className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-[11px]"
              >
                {LIBRARY_AGE_OPTIONS.map((o) => (
                  <option key={o.value || "any"} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <select
                value={libraryFilters.language}
                onChange={(e) => setLibraryFilters((f) => ({ ...f, language: e.target.value }))}
                className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-[11px]"
              >
                {LIBRARY_LANGUAGE_OPTIONS.map((o) => (
                  <option key={o.value || "any-lang"} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <select
                value={libraryFilters.accent}
                onChange={(e) => setLibraryFilters((f) => ({ ...f, accent: e.target.value }))}
                className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-[11px]"
              >
                {LIBRARY_ACCENT_OPTIONS.map((o) => (
                  <option key={o.value || "any-acc"} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <select
              value={libraryFilters.sort}
              onChange={(e) => setLibraryFilters((f) => ({ ...f, sort: e.target.value }))}
              className="h-8 w-full rounded-md border border-input bg-background/50 px-2 text-[11px]"
            >
              {LIBRARY_SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <Input
              value={libraryFilter}
              onChange={(e) => setLibraryFilter(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void loadLibrary(libraryPages);
              }}
              placeholder="Поиск по имени… Enter"
              className="h-8 text-xs"
            />
            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              disabled={busy !== null || connectStatus !== "ok"}
              onClick={() => void loadLibrary(libraryPages)}
            >
              {busy === "library" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Применить фильтры
            </Button>
            <p className="text-[10px] text-muted-foreground">
              Фильтры как на ElevenLabs: пол, возраст, язык, акцент, сортировка
            </p>
            {libraryLoaded ? (
              <>
                {libraryMeta ? (
                  <p className="text-[10px] text-muted-foreground">{libraryMeta}</p>
                ) : null}
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full"
                  disabled={busy !== null || libraryPages >= 20}
                  onClick={() => void loadLibrary(libraryPages + 1)}
                >
                  Загрузить ещё (+100)
                </Button>
                <div className="max-h-56 space-y-2 overflow-auto">
                  {filteredRemote.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Ничего не найдено</p>
                  ) : (
                    filteredRemote.map((v) => (
                      <div
                        key={v.voice_id}
                        className="rounded-md border border-white/5 bg-white/[0.02] px-2 py-2 text-xs"
                      >
                        <div className="font-medium">{v.name}</div>
                        <div className="text-[10px] text-muted-foreground">
                          {voiceMetaLine(v)} · {v.voice_id.slice(0, 10)}…
                        </div>
                        {v.preview_url ? (
                          <audio controls src={v.preview_url} className="mt-1 h-8 w-full" />
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-1">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-[10px]"
                            onClick={() => selectVoice(v.voice_id, v.name)}
                          >
                            Выбрать
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-[10px]"
                            onClick={() => void saveRemoteVoice(v)}
                          >
                            В мои
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-[10px]"
                            disabled={busy !== null}
                            onClick={() => {
                              selectVoice(v.voice_id, v.name);
                              void runTts("Привет! Это пробник этого голоса.");
                            }}
                          >
                            Пробник
                          </Button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </>
            ) : (
              <p className="text-[10px] text-muted-foreground">
                Все голоса вашего аккаунта ElevenLabs — прослушать, выбрать, сохранить локально
              </p>
            )}
          </Panel>
        </section>

        <section className="col-span-12 space-y-3 lg:col-span-5">
          <Panel title="Дорожка">
            <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-white/15 bg-white/[0.02] px-3 py-4 text-sm text-muted-foreground hover:bg-white/[0.04]">
              <Upload className="h-4 w-4" />
              {sourceFile ? sourceFile.name : "Загрузить mp3 / wav (voice_full…)"}
              <input
                type="file"
                accept="audio/*"
                className="hidden"
                onChange={(e) => loadSource(e.target.files?.[0] ?? null)}
              />
            </label>
            {resultUrl ? (
              <a href={resultUrl} download className="text-xs text-primary underline">
                Скачать последний результат
              </a>
            ) : null}
          </Panel>

          {pendingPreview ? (
            <Panel title="Превью замены — одобрите перед склейкой" icon={Wand2}>
              <p className="text-xs text-muted-foreground">
                Интервал {pendingPreview.replaceRange.start.toFixed(2)}–
                {pendingPreview.replaceRange.end.toFixed(2)} s · {pendingPreview.patchDurationS.toFixed(1)}s
              </p>
              <p className="rounded-md bg-white/[0.03] p-2 text-xs">{pendingPreview.spokenText}</p>
              <audio controls src={pendingPreview.patchUrl} className="w-full" />
              <div className="flex gap-2">
                <Button size="sm" className="flex-1" disabled={busy !== null} onClick={() => void approvePreview()}>
                  {busy === "apply" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  Одобрить и склеить
                </Button>
                <Button size="sm" variant="outline" disabled={busy !== null} onClick={rejectPreview}>
                  <XCircle className="h-4 w-4" />
                  Отклонить
                </Button>
              </div>
            </Panel>
          ) : null}
        </section>

        <section className="col-span-12 space-y-4 lg:col-span-4">
          <Panel title="Клон голоса" icon={Scissors}>
            <Field label="Имя голоса">
              <Input value={voiceName} onChange={(e) => setVoiceName(e.target.value)} className="h-8 text-xs" />
            </Field>
            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              disabled={busy !== null || !cutTargetRange}
              onClick={() => void cutCloneSample()}
            >
              {busy === "cut" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scissors className="h-4 w-4" />}
              Нарезать выделение для клона
            </Button>
            {cutTargetDurationS != null ? (
              <p className="font-mono text-xs text-sky-300">
                Выделение: {cutTargetDurationS.toFixed(2)} s
                {activeCloneRange ? " · клон" : " · замена (тоже режется)"}
              </p>
            ) : (
              <p className="text-[10px] text-muted-foreground">
                Выделите отрезок на waveform (кнопка «Клон-sample» или «Замена»)
              </p>
            )}
            {lastCutDurationS != null ? (
              <p className="font-mono text-sm font-medium text-emerald-300">
                Последний вырез: {lastCutDurationS.toFixed(2)} s
              </p>
            ) : null}
            {cloneSampleFile ? (
              <p className="text-[10px] text-muted-foreground">{cloneSampleFile.name}</p>
            ) : null}
            {cloneSamplePreview ? (
              <audio controls src={cloneSamplePreview} className="h-8 w-full" />
            ) : null}
            <Button size="sm" className="w-full" disabled={busy !== null} onClick={() => void runCloneOnly()}>
              {busy === "clone" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Только клонировать
            </Button>
            {lastCloneResult ? (
              <div className="space-y-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3">
                <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-300">
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                  Клон создан
                </p>
                <p className="text-xs text-muted-foreground">
                  Имя: <span className="text-foreground">{lastCloneResult.name}</span>
                  {lastCloneResult.saved ? " · сохранён в списке слева" : ""}
                </p>
                <Field label="voice_id (для «Замена слова»)">
                  <Input
                    readOnly
                    value={lastCloneResult.voiceId}
                    className="h-8 font-mono text-[11px]"
                  />
                </Field>
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full"
                  onClick={() => {
                    void navigator.clipboard.writeText(lastCloneResult.voiceId);
                    toast.success("voice_id скопирован");
                  }}
                >
                  Копировать voice_id
                </Button>
                <p className="text-[10px] text-muted-foreground">
                  Голос сохранён слева в «Мои клоны» с образцом mp3. Дальше — «Озвучка» или «Замена слова».
                </p>
              </div>
            ) : null}
          </Panel>

          <Panel title="Озвучка (TTS)" icon={AudioIcon}>
            <p className="text-[10px] text-muted-foreground">
              Голос:{" "}
              {selectedVoiceId
                ? `${voiceName || "—"} · ${selectedVoiceId.slice(0, 12)}…`
                : "не выбран — кликните голос слева"}
            </p>
            <Field label="Текст">
              <textarea
                value={ttsText}
                onChange={(e) => setTtsText(e.target.value)}
                rows={4}
                className="w-full rounded-md border border-input bg-background/50 px-2 py-1.5 text-xs"
              />
            </Field>
            <Button size="sm" className="w-full" disabled={busy !== null} onClick={() => void runTts()}>
              {busy === "tts" ? <Loader2 className="h-4 w-4 animate-spin" /> : <AudioIcon className="h-4 w-4" />}
              Сгенерировать и прослушать
            </Button>
            {ttsResultUrl ? (
              <div className="space-y-2 rounded-md border border-primary/30 bg-primary/5 p-2">
                {ttsDuration != null ? (
                  <p className="text-xs text-muted-foreground">Длительность: {ttsDuration.toFixed(1)} s</p>
                ) : null}
                <audio controls src={ttsResultUrl} className="w-full" />
                <a href={ttsResultUrl} download className="text-xs text-primary underline">
                  Скачать mp3
                </a>
              </div>
            ) : null}
          </Panel>

          <Panel title="Замена слова" icon={Wand2}>
            <Field label="Текст фрагмента">
              <textarea
                value={fragmentText}
                onChange={(e) => setFragmentText(e.target.value)}
                rows={4}
                className="w-full rounded-md border border-input bg-background/50 px-2 py-1.5 text-xs"
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Было">
                <Input value={oldWord} onChange={(e) => setOldWord(e.target.value)} className="h-8 text-xs" />
              </Field>
              <Field label="Стало">
                <Input value={newWord} onChange={(e) => setNewWord(e.target.value)} className="h-8 text-xs" />
              </Field>
            </div>
            <Button size="sm" className="w-full" disabled={busy !== null} onClick={() => void runPreview()}>
              {busy === "preview" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              Сгенерировать превью
            </Button>
            <p className="text-[10px] text-muted-foreground">
              Сначала прослушайте патч. Склейка — только после «Одобрить».
            </p>
          </Panel>
        </section>
      </div>

      <div
        className="flex shrink-0 flex-col border-t border-white/10 bg-card/30"
        style={{ height: dockHeight }}
      >
        <div
          className="flex h-6 cursor-row-resize items-center justify-center border-b border-white/5 text-muted-foreground hover:bg-white/[0.03]"
          onPointerDown={onDockResizeStart}
          onPointerMove={onDockResizeMove}
          onPointerUp={onDockResizeEnd}
          onPointerLeave={onDockResizeEnd}
        >
          <GripHorizontal className="h-4 w-4" />
        </div>
        <div className="flex min-h-0 flex-1 flex-col px-4 py-2">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <ToolBtn active={activeTool === "replace"} onClick={() => setActiveTool("replace")}>
              Замена
            </ToolBtn>
            <ToolBtn active={activeTool === "clone"} onClick={() => setActiveTool("clone")}>
              Клон-sample
            </ToolBtn>
            <Button
              size="sm"
              variant="outline"
              onClick={undoSelection}
              disabled={(activeTool === "replace" ? replacePast : clonePast).length === 0}
            >
              <Undo2 className="h-3.5 w-3.5" />
              Назад
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={resetAllSelections}
              disabled={replaceRanges.length === 0 && cloneRanges.length === 0}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Сброс всех
            </Button>
          </div>
          <div className="mb-2 flex flex-wrap gap-2 text-[10px]">
            <RangeList
              label="Замена"
              ranges={replaceRanges}
              selected={selectedReplaceIdx}
              onSelect={setSelectedReplaceIdx}
            />
            <RangeList
              label="Клон"
              ranges={cloneRanges}
              selected={selectedCloneIdx}
              onSelect={setSelectedCloneIdx}
            />
          </div>
          <AudioWaveform
            file={sourceFile}
            audioSrc={sourcePreview}
            duration={duration}
            replaceRanges={replaceRanges}
            cloneRanges={cloneRanges}
            activeTool={activeTool}
            selectedReplaceIdx={selectedReplaceIdx}
            selectedCloneIdx={selectedCloneIdx}
            canvasHeight={canvasHeight}
            onReplaceRangesChange={(ranges) => {
              setReplaceRanges(ranges);
              if (ranges.length > replaceRanges.length) setSelectedReplaceIdx(ranges.length - 1);
            }}
            onCloneRangesChange={(ranges) => {
              setCloneRanges(ranges);
              if (ranges.length > cloneRanges.length) setSelectedCloneIdx(ranges.length - 1);
            }}
            onSelectionCommit={pushSelectionHistory}
            onDeleteRange={deleteRange}
            onDuration={setDuration}
            className="min-h-0 flex-1"
          />
        </div>
      </div>
    </div>
  );
}

function RangeList({
  label,
  ranges,
  selected,
  onSelect,
}: {
  label: string;
  ranges: TimeRange[];
  selected: number;
  onSelect: (idx: number) => void;
}) {
  if (ranges.length === 0) {
    return (
      <span className="text-muted-foreground">
        {label}: —
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-muted-foreground">{label}:</span>
      {ranges.map((r, i) => (
        <button
          key={`${label}-${i}-${r.start}`}
          type="button"
          onClick={() => onSelect(i)}
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono",
            selected === i
              ? "border-primary/50 bg-primary/15 text-primary"
              : "border-white/10 bg-white/[0.02] hover:bg-white/[0.05]",
          )}
        >
          {i + 1}: {r.start.toFixed(1)}–{r.end.toFixed(1)}s
        </button>
      ))}
    </div>
  );
}

function ConnectBadge({
  status,
  message,
  compact,
}: {
  status: ConnectStatus;
  message: string;
  compact?: boolean;
}) {
  const cfg = {
    idle: {
      icon: Plug,
      label: "Не проверено",
      className: "border-white/10 bg-white/[0.03] text-muted-foreground",
    },
    checking: {
      icon: Loader2,
      label: "Проверка…",
      className: "border-amber-500/30 bg-amber-500/10 text-amber-200",
    },
    ok: {
      icon: CheckCircle2,
      label: "OK",
      className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    },
    error: {
      icon: AlertCircle,
      label: "Ошибка",
      className: "border-red-500/40 bg-red-500/10 text-red-300",
    },
  }[status];
  const Icon = cfg.icon;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-2",
        compact ? "text-[11px]" : "text-sm",
        cfg.className,
      )}
      title={message}
    >
      <Icon className={cn("h-4 w-4 shrink-0", status === "checking" && "animate-spin")} />
      <div className="min-w-0">
        <div className="font-medium">{cfg.label}</div>
        {!compact ? <div className="truncate text-xs opacity-90">{message}</div> : null}
        {compact ? <div className="truncate opacity-90">{message}</div> : null}
      </div>
    </div>
  );
}

function Panel({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon?: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3 backdrop-blur-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        {Icon ? <Icon className="h-4 w-4 text-primary" /> : null}
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function ToolBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2.5 py-1 text-xs",
        active ? "border-primary/60 bg-primary/15 text-primary" : "border-white/10 bg-white/[0.02]",
      )}
    >
      {children}
    </button>
  );
}
