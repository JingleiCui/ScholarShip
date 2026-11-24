export type ConversationRun = {
  id: string;
  kind: string;
  status: string;
  question?: string;
  created_at?: string;
  mode?: string;
  source_version_ids?: string[];
  conversation_id?: string | null;
  parent_run_id?: string | null;
};
export function sourceKey(ids: string[]): string {
  return [...new Set(ids)].sort().join(',');
}
export function conversationGroups<T extends ConversationRun>(runs: T[], sources: string[], mode: string) {
  const qa = runs.filter((r) => r.kind === 'research_qa');
  const byId = new Map(qa.map((r) => [r.id, r]));
  function key(run: T): string {
    const seen = new Set<string>();
    let current = run;
    while (!seen.has(current.id)) {
      seen.add(current.id);
      if (current.conversation_id) return 'conversation:' + current.conversation_id;
      if (!current.parent_run_id) return 'run:' + current.id;
      const parent = byId.get(current.parent_run_id);
      if (!parent) return 'run:' + current.parent_run_id;
      current = parent;
    }
    return 'run:' + run.id;
  }
  const groups = new Map<string, T[]>();
  for (const run of qa) {
    if (sourceKey(run.source_version_ids ?? []) !== sourceKey(sources) || run.mode !== mode) continue;
    const id = key(run);
    groups.set(id, [...(groups.get(id) ?? []), run]);
  }
  return [...groups.entries()]
    .map(([id, items]) => ({
      id,
      turns: items.sort(
        (a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? '') || a.id.localeCompare(b.id),
      ),
    }))
    .sort((a, b) => (b.turns.at(-1)?.created_at ?? '').localeCompare(a.turns.at(-1)?.created_at ?? ''));
}
