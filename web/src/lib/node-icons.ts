/**
 * Иконки для нод. Реестр в одном месте, чтобы node-canvas мог рендерить
 * иконку по string-key из NODE_CATALOG (избегаем dynamic import).
 */

import {
  FileText,
  ScrollText,
  GitBranch,
  UserRound,
  Package,
  Wand2,
  ImageIcon,
  Film,
  AudioWaveform,
  Scissors,
  Send,
  CheckSquare,
  Sparkles,
  Music2,
  type LucideIcon,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  plan: FileText,
  script: ScrollText,
  split: GitBranch,
  "user-round": UserRound,
  package: Package,
  wand: Wand2,
  image: ImageIcon,
  film: Film,
  "audio-waveform": AudioWaveform,
  music: Music2,
  scissors: Scissors,
  send: Send,
  "check-square": CheckSquare,
  sparkles: Sparkles,
};

export function getNodeIcon(key: string): LucideIcon {
  return ICONS[key] ?? FileText;
}
