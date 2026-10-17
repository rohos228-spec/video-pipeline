/** Custom per-node background URLs (set from canvas tiles, read by the node menu header). */
const store = new Map<string, string>();
export function setNodeCustomBg(nodeKey: string, url: string | null | undefined) {
  if (url) store.set(nodeKey, url);
  else store.delete(nodeKey);
}
export function getNodeCustomBg(nodeKey: string): string | null {
  return store.get(nodeKey) ?? null;
}
