// 前端主流程烟测：用最小 DOM 打桩在 Node 里真实执行 load()（含联网取数、拼接、封顶、渲染串生成）。
// 目的：app.js 的引擎部分已由 parity_check 覆盖，本脚本补的是"DOM 胶水不会抛错、渲染串结构正确"。
// 用法：node tools/smoke_app.mjs
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const pheno = fs.readFileSync(new URL('../phenology.js', import.meta.url), 'utf8');

const mkEl = () => ({
  innerHTML: "", textContent: "", value: "", disabled: false, onclick: null,
  classList: { add() {}, remove() {} }, dataset: {},
});
const els = new Map();
const document = {
  getElementById: (id) => { if (!els.has(id)) els.set(id, mkEl()); return els.get(id); },
  querySelectorAll: () => [],
  createElement: () => mkEl(),
};
const store = {};
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
const window = {};

// 去掉页面初始化与自动 load()（改由本脚本显式 await，避免两次并发加载竞写）
const cut = src.indexOf('// ---------- 初始化 ----------');
if (cut < 0) { console.error('[SMOKE FAIL] app.js 初始化段标记变更，请同步本脚本'); process.exit(1); }
const run = new Function('document', 'localStorage', 'window', 'console',
  pheno + '\n' + src.slice(0, cut) + '\nreturn { load, askLLM, ruleAnswer, buildContext, worstRisk };');
const api = run(document, localStorage, window, console);

try {
  await api.load();
} catch (e) {
  console.error('[SMOKE FAIL] load() 抛错：', e && e.message);
  process.exit(1);
}

const t = window.__today, all = window.__recent;
const problems = [];
if (!t) problems.push('window.__today 未设置');
if (!Array.isArray(all) || all.length < 60) problems.push(`序列长度异常：${all && all.length}`);
for (const k of ['phenology-now', 'risk-now', 'timeline', 'advice']) {
  const html = els.get(k)?.innerHTML || '';
  if (!html || html.includes('undefined') || html.includes('NaN')) {
    problems.push(`#${k} 渲染内容异常或为空：${html.slice(0, 80)}`);
  }
}
if (t) {
  if (!t.pheno?.name) problems.push('今日物候名缺失');
  if (Object.keys(t.risks).length !== 4) problems.push('风险项数不是 4');
}
const far = (all || []).filter((d) => d.forecast).slice(-1)[0];
if (far && Object.values(far.risks).some((r) => r && ['重', '中', '轻'].includes(r.level))) {
  problems.push('末位预报日未被封顶为"关注"');
}
console.log('今日：', t?.date, t?.pheno?.name, JSON.stringify(Object.fromEntries(
  Object.entries(t?.risks || {}).map(([k, v]) => [k, v?.level ?? '无']))));
console.log('序列天数：', all?.length, '其中预报日：', (all || []).filter((d) => d.forecast).length);
console.log('最远预报日等级：', far?.date, JSON.stringify(Object.fromEntries(
  Object.entries(far?.risks || {}).map(([k, v]) => [k, v?.level ?? '无']))));
console.log('#advice 前 60 字：', (els.get('advice')?.innerHTML || '').replace(/\s+/g, ' ').slice(0, 60));

if (problems.length) {
  console.error('[SMOKE FAIL]\n  ' + problems.join('\n  '));
  process.exit(1);
}
console.log('[SMOKE OK] load() 全流程无异常，渲染串完整');
