/**
 * Стабильный цвет (hue) для группы нод по её id — одинаковый на всех ПК
 * и между перезапусками, чтобы рамка группы на канвасе не меняла цвет.
 */
export function groupHue(groupId: string): number {
  let h = 0;
  for (let i = 0; i < groupId.length; i++) {
    h = (h * 31 + groupId.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}
