// 前端主流程烟测：用最小 DOM 打桩在 Node 里真实执行 load()（含联网取数、拼接、封顶、渲染串生成）。
// 目的：app.js 的引擎部分已由 parity_check 覆盖，本脚本补的是"DOM 胶水不会抛错、渲染串结构正确"。
// 用法：node tools/smoke_app.mjs
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const pheno = fs.readFileSync(new URL('../phenology.js', import.meta.url), 'utf8');

const mkEl = () => ({
  innerHTML: "", textContent: "", value: "", disabled: false, onclick: null, style: {},
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
  pheno + '\n' + src.slice(0, cut) +
  '\nreturn { load, askLLM, ruleAnswer, buildContext, worstRisk, renderSunset, renderAdvice, ' +
  'idleLabel, ADVICE, adviceOf, adviceItem, adviceText };');
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

// 停服横幅：留空不显示；填入日期后必须显示且带剩余天数（docs/03 退场承诺的执行件，不能只是措辞）
api.renderSunset();
if (els.get('sunset')?.style.display === 'block') problems.push('SUNSET_DATE 为空却显示了横幅');
api.renderSunset('2027-06-30');
const banner = els.get('sunset');
if (banner?.style.display !== 'block') problems.push('填入日期后横幅未显示');
if (!banner?.textContent?.includes('2027-06-30') || !/\d+ 天/.test(banner?.textContent || '')) {
  problems.push(`横幅文案缺日期或剩余天数：${banner?.textContent}`);
}
console.log('停服横幅样例：', (banner?.textContent || '').slice(0, 60) + '…');

// 外部性动作确认标记（评审 C-Q6：分级必须体现在产物里，不只是文档）
// 语义断言而非数数：凡文案命中外部性关键词（燃烧/覆膜/投入品/退水）的动作，都必须挂确认标记
const EXTERNAL = /熏烟|覆膜|地膜|防寒布|磷酸二氢钾|保水剂|叶面肥|速效肥|喷药|灌透水|冻前灌水|追施/;
let nConfirm = 0, nTotal = 0;
const untagged = [];
for (const [risk, lib] of Object.entries(api.ADVICE)) {
  for (const [ph, arr] of Object.entries(lib.items)) {
    for (const it of arr) {
      nTotal++;
      const text = api.adviceText(it);
      const tagged = text.startsWith("【需当地确认");
      if (tagged) nConfirm++;
      const body = text.replace(/^【需当地确认后才可执行】/, "");
      if (EXTERNAL.test(body) && !tagged) untagged.push(`${risk}.${ph}: ${body.slice(0, 28)}`);
    }
  }
}
if (untagged.length) problems.push(`外部性动作漏挂确认标记：${untagged.join(" | ")}`);
if (nConfirm < 8) problems.push(`标记为"需当地确认"的外部性动作过少（${nConfirm} 条），分级形同虚设`);
const sampleConfirmHtml = api.adviceItem({ t: "傍晚熏烟", confirm: true });
const samplePlainHtml = api.adviceItem({ t: "树干涂白 + 培土壅蔸", confirm: false });
if (!sampleConfirmHtml.includes("已经当地确认")) problems.push("已登记确认项未标注确认状态");
if (samplePlainHtml.includes("确认")) problems.push("普通项被误挂确认字样");

// 纯函数用例（评审 A-Q10 工单）：三态标签、低温取对建议库、fail-closed 拦截，不联网
const mkToday = (phenoId, phenoName, risks, dq = { windowDays: 30, windowBad: 0, etOk: true }) => ({
  date: "2026-04-15", pheno: { id: phenoId, name: phenoName, note: "测试" },
  tmax: 20, tmin: 2, pr: 10, dq, risks,
});
const idle1 = api.idleLabel("drought", mkToday("dormant", "越冬休眠期", {}));
if (!idle1.startsWith("不判级")) problems.push(`休眠期干旱被写成"没旱情"：${idle1}`);
const idle2 = api.idleLabel("drought", mkToday("swell", "果实膨大期", {}, { windowDays: 25, windowBad: 0, etOk: true }));
if (!idle2.includes("数据不完整")) problems.push(`窗口不满 30 天未区分：${idle2}`);
window.__today = mkToday("bloom", "开花期", {
  heat: null, drought: null, cold: { level: "重", why: "花期最低温 2℃ ≤2℃，落花严重" }, freeze: null,
});
window.__recent = [];
const ansCold = api.ruleAnswer("低温来了怎么办？");
if (!ansCold.includes("熏烟")) problems.push(`低温问答没有取开花期(cold)建议库，实际：${ansCold.slice(0, 70)}`);
if (ansCold.includes("关注天气预报，寒潮前做好覆盖防护")) problems.push("低温问答落到了 freeze.default（取错库）");
window.__today = mkToday("swell", "果实膨大期", {
  heat: null, drought: { level: "中", why: "测试" }, cold: null, freeze: null,
});
api.renderAdvice(window.__today);
const adviceHtml = els.get("advice").innerHTML;
const [goPart, waitPart] = adviceHtml.split("待当地确认清单");
if (!waitPart) problems.push("外部性动作没有单独成区（fail-closed 未生效）");
if (goPart.includes("穴贮肥水")) problems.push("未确认的外部性动作仍出现在可执行建议里");
if (waitPart && !waitPart.includes("穴贮肥水")) problems.push("待确认清单里没有该动作");
console.log("fail-closed 检查：可执行段", goPart.length, "字，待确认段", (waitPart || "").length, "字");
for (const q of ["现在要不要灌水？", "寒潮来了防冻要做什么？"]) {
  const ans = api.ruleAnswer(q);
  if (ans.includes("[object Object]")) problems.push(`规则问答把建议对象直接拼进文本：${q}`);
  if (!ans.includes("需当地确认") && q.includes("灌水")) problems.push("规则问答未保留确认标记");
}
console.log(`外部性确认标记：${nConfirm}/${nTotal} 条建议需先经当地确认`);

if (problems.length) {
  console.error('[SMOKE FAIL]\n  ' + problems.join('\n  '));
  process.exitCode = 1;
} else {
  console.log('[SMOKE OK] load() 全流程无异常，渲染串完整，停服横幅可用');
}
