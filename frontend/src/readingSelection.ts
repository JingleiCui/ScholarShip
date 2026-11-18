/** DOM and backend offsets use different units; API spans are Unicode code points. */
export function selectedSpan(canonical: string, prefix: string, selected: string) {
  if (!selected.trim() || selected.length > 4000 || !canonical.startsWith(prefix)) return null;
  const start = Array.from(prefix).length;
  const end = start + Array.from(selected).length;
  if (Array.from(canonical).slice(start, end).join('') !== selected) return null;
  return { start, end, text: selected };
}
