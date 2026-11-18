import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseRoute, routeHash, modules, validIds } from './navigation';
import { cloudAvailable } from './execution';
import { selectedSpan } from './readingSelection';
import { Tools } from './Home';
import { resultOrigin } from './Results';
const first = '00000000-0000-4000-8000-000000000001',
  second = '00000000-0000-4000-8000-000000000002';
describe('research workspace continuity', () => {
  it('restores source scope, reading task and selected historical result', () => {
    const route = {
      page: 'read' as const,
      versions: [first, second],
      tab: 'translation' as const,
      run: first,
    };
    expect(parseRoute(routeHash(route))).toEqual(route);
  });
  it('preserves old links without putting QA back under discovery', () => {
    expect(parseRoute('#module=1')).toMatchObject({ page: 'read', tab: 'translation' });
    expect(parseRoute('#module=2').page).toBe('discover');
    expect(parseRoute('#module=4').page).toBe('write');
  });
  it('rejects malformed identifiers and deduplicates source selections', () => {
    expect(validIds([first, '<script>', first, second])).toEqual([first, second]);
    expect(parseRoute('#page=unknown&run=javascript:alert(1)').page).toBe('home');
    expect(parseRoute('#page=read&run=javascript:alert(1)').run).toBeUndefined();
  });
  it('keeps all six requirements reachable including charts under F6', () => {
    expect(modules.map((m) => m.code)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5', 'F6']);
    const html = renderToStaticMarkup(<Tools onNavigate={() => {}} />);
    for (const m of modules) expect(html).toContain(m.title);
    expect(html).toContain('数据绘图');
  });
});
describe('execution admission stays distinct from model selection', () => {
  const now = Date.parse('2026-11-18T00:00:00Z');
  const c = {
    roles: { chat: { provider: 'test', model: 'model' } },
    production_policies: [
      {
        role: 'chat',
        provider: 'test',
        model: 'model',
        enabled: true,
        request_limit: 2,
        requests_used: 1,
        ends_at: '2026-11-19T00:00:00Z',
      },
    ],
  };
  const e = {
    requested_external_flag: true,
    shared_pool: { live_enabled: true, blocked: false, available: '1' },
  };
  it('requires both gates and an unexpired matching role policy', () => {
    expect(cloudAvailable(c, e, ['chat'], now)).toBe(true);
    expect(cloudAvailable(c, { ...e, requested_external_flag: false }, ['chat'], now)).toBe(false);
    expect(
      cloudAvailable(c, { ...e, shared_pool: { ...e.shared_pool, live_enabled: false } }, ['chat'], now),
    ).toBe(false);
    expect(cloudAvailable(c, e, ['chat', 'embedding'], now)).toBe(false);
  });
  it('does not treat sealed, expired or mismatched quota as permission', () => {
    for (const patch of [
      { enabled: false },
      { requests_used: 2 },
      { model: 'other' },
      { ends_at: '2026-11-17T00:00:00Z' },
    ])
      expect(
        cloudAvailable(
          { ...c, production_policies: [{ ...c.production_policies[0], ...patch }] },
          e,
          ['chat'],
          now,
        ),
      ).toBe(false);
    expect(
      cloudAvailable(c, { ...e, shared_pool: { ...e.shared_pool, available: '0' } }, ['chat'], now),
    ).toBe(false);
  });
});
describe('exact original text selection', () => {
  it('uses code-point offsets, including emoji and repeated phrases', () => {
    expect(selectedSpan('前😀一段一段', '前😀一段', '一段')).toEqual({ start: 4, end: 6, text: '一段' });
  });
  it('refuses transformed math, cross-block and empty selections', () => {
    expect(selectedSpan('alpha beta', 'wrong', 'beta')).toBeNull();
    expect(selectedSpan('$x^2$', '', 'x²')).toBeNull();
    expect(selectedSpan('   ', '', '   ')).toBeNull();
  });
});
describe('result provenance', () => {
  it('does not label human comparison, simulation or unknown execution as model success', () => {
    expect(resultOrigin({ id: first, status: 'succeeded', operation: 'direction_review' })).toBe(
      '本地处理 / 人工对照',
    );
    expect(
      resultOrigin({ id: first, status: 'succeeded', result_json: { model_simulated: true } }),
    ).toContain('模拟');
    expect(resultOrigin({ id: first, status: 'failed' })).toBe('尚无完成结果');
  });
});
