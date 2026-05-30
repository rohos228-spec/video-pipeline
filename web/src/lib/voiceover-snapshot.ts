import type { ProjectAsset } from "@/lib/api";
import type { NodeResultItem, NodeResultSnapshot } from "@/lib/node-result-resolver";

/** Только файл data/.../voiceover.txt — не кадры, не script_text, не script.txt. */
export function findVoiceoverAsset(assets: ProjectAsset[]): ProjectAsset | undefined {
  return assets.find((a) => a.id === "voiceover.txt" || a.label === "voiceover.txt");
}

export function buildVoiceoverItems(assets: ProjectAsset[]): NodeResultItem[] {
  const voiceFile = findVoiceoverAsset(assets);
  if (!voiceFile?.preview_url) return [];
  return [
    {
      id: voiceFile.id,
      label: "voiceover.txt",
      kind: "file",
      downloadUrl: voiceFile.preview_url,
    },
  ];
}

export function buildVoiceoverSnapshot(assets: ProjectAsset[]): NodeResultSnapshot {
  const items = buildVoiceoverItems(assets);
  return {
    hasResult: items.length > 0,
    itemCount: items.length,
    summary: items.length ? "voiceover.txt" : "voiceover.txt ещё не создан",
    items,
    replaceMode: "none",
    viewMode: "voiceover_wide",
  };
}
