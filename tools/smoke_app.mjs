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
const downloads = [];                      // downloadFile 的落地记录（评审 A-R8⑨：不许只测替身）
const anchor = () => {
  const a = { href: "", download: "", style: {}, click() { downloads.push([a.download, a.href]); } };
  return a;
};
const listeners = [];
const document = {
  getElementById: (id) => { if (!els.has(id)) els.set(id, mkEl()); return els.get(id); },
  querySelectorAll: () => [],
  createElement: anchor,
  addEventListener(type, fn) { listeners.push([type, fn]); },
};
const URLMock = { createObjectURL: () => "blob:mock", revokeObjectURL() {} };
class Blob {
  constructor(parts) { this.parts = parts; }
}
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
  key: (i) => Object.keys(store)[i] ?? null,
  get length() { return Object.keys(store).length; },
};
const window = {};

// 去掉页面初始化与自动 load()（改由本脚本显式 await，避免两次并发加载竞写）
const cut = src.indexOf('// ---------- 初始化 ----------');
if (cut < 0) { console.error('[SMOKE FAIL] app.js 初始化段标记变更，请同步本脚本'); process.exit(1); }
const run = new Function('document', 'localStorage', 'window', 'console', 'URL', 'Blob',
  pheno + '\n' + src.slice(0, cut) +
  '\nreturn { load, askLLM, ruleAnswer, buildContext, worstRisk, renderSunset, renderAdvice, ' +
  'idleLabel, ADVICE, adviceOf, adviceItem, adviceText, onAdviceChange, onAdviceInput, renderAdvice, ' +
  'confirmedActions, pendingConfirmList, ruleStamp, RULE_VERSION, DEF_SOLO_CAP, ' +
  'capFarForecast, FAR_LEAD_DAYS, irrCond, setIrrCond, fruitCond, setFruitCond, adviceFor, timingOf, ' +
  'llmCheck, adviceLines, freezeClock, nextCooling, shiftDay, reportHtml, fruitNotice, ' +
  'exportReport, wireLocalControls, clearLocalRecords, wireDelegates, onAdviceInput };');
const api = run(document, localStorage, window, console, URLMock, Blob);

try {
  await api.load();
} catch (e) {
  console.error('[SMOKE FAIL] load() 抛错：', e && e.message);
  process.exit(1);
}

const t = window.__today, all = window.__recent;
const problems = [];
// load() 必须已经把两个按钮与事件委托挂上——否则真机点开什么都没发生，而这条路径原本无人验证
// （评审 A-R12⑧：这两条断言原本写在 `const problems` 之前，一旦触发就是 ReferenceError 崩掉，
//   后面一百多条断言一条都不跑——守卫不能靠崩溃来"通过"）
if (typeof document.getElementById("clear-key").onclick !== "function"
    || typeof document.getElementById("clear-local").onclick !== "function")
  problems.push("load() 之后清除按钮仍无 onclick（绑定只在被切走的初始化段里跑）");
if (!listeners.length) problems.push("load() 之后事件委托没注册：复选框与园块下拉在真机全废");
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
if (idle1 === "今日无该风险触发" || !idle1.includes("旱情")) {
  problems.push(`休眠期干旱没有区分"不判级"与"没旱情"：${idle1}`);
}
const idle2 = api.idleLabel("drought", mkToday("swell", "果实膨大期", {}, { windowDays: 25, windowBad: 0, etOk: true }));
if (idle2 === "今日无该风险触发") problems.push(`窗口不满 30 天仍被写成"没旱情"：${idle2}`);
const idle3 = api.idleLabel("cold", mkToday("swell", "果实膨大期", {}));
if (idle3 !== "这个物候期不涉及该风险") problems.push(`低温在膨大期应标"不适用"，实际：${idle3}`);
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

// 确认开关必须可操作（评审 B-Q5：没有写入路径的 fail-closed 是纸面机制）
const pending = (api.ADVICE.drought.items.swell || []).map(api.adviceOf).find((a) => a.confirm);
if (!pending) problems.push("膨大期干旱建议里没有外部性动作，测不到确认流程");
else {
  if (!adviceHtml.includes("data-confirm-text")) problems.push("待确认清单里没有确认开关（用户无法操作）");
  // 未确认的外部性动作必须出现在给 LLM 的门控清单里，否则 fail-closed 只剩文字（评审 A-Q7）
  const gate0 = JSON.parse(api.buildContext()).gate["需先经当地确认才可视作可执行"] || [];
  if (!Array.isArray(gate0) || !gate0.length) problems.push("LLM 门控清单为空：待确认动作没有进入上下文");
  else if (!gate0.some((s) => String(s).includes(pending.t) || pending.t.includes(String(s))))
    problems.push("未确认的外部性动作没进 LLM 门控清单");
  // 没写确认人 → 不许升格（防止自我勾选，评审 C-Q5）
  api.onAdviceChange({ target: { dataset: { confirmText: pending.t }, checked: true } });
  if (api.confirmedActions().some((r) => r && r.t === pending.t)) problems.push("无署名确认竟然生效");
  api.onAdviceInput({ target: { dataset: { confirmBy: pending.t }, value: "农技站 张站长／口头答复" } });
  api.onAdviceChange({ target: { dataset: { confirmText: pending.t }, checked: true } });
  const rec = api.confirmedActions().find((r) => r && r.t === pending.t);
  if (!rec || !rec.by || !rec.at) problems.push("勾选确认后未记录确认人与日期");
  const html2 = els.get("advice").innerHTML;
  if (!html2.includes("已经当地确认")) problems.push("确认后未升格为可执行建议");
  if (!html2.includes("张站长")) problems.push("升格后的建议没带确认人，打印件无法区分谁确认");
  const ctx = JSON.parse(api.buildContext());
  if (!ctx.gate || !Array.isArray(ctx.gate["需先经当地确认才可视作可执行"])) problems.push("LLM 上下文没有携带确认门控");
  else if (ctx.gate["需先经当地确认才可视作可执行"].includes(pending.t)) problems.push("已确认动作仍留在门控清单里");
  api.onAdviceChange({ target: { dataset: { confirmText: pending.t }, checked: false } });
  if (api.confirmedActions().some((r) => r && r.t === pending.t)) problems.push("取消确认未生效");
  console.log("确认开关：无署名拒绝→署名升格（带确认人/日期）→进 LLM 门控→取消，全链路可用");
}

// 远期预报封顶（评审 A-Q2：这是唯一会直接改变用户动作的前端安全装置，此前没有任何测试能失败）
{
  const today = "2026-10-13";
  const mk = (date) => ({ date, forecast: true, tmax: 40.2, tmin: 26,
    risks: { heat: { level: "重", why: "最高温 40.2℃ ≥39℃" }, drought: null, cold: null, freeze: null } });
  const days = [mk("2026-10-18"), mk("2026-10-21"), mk("2026-10-23"), mk("2026-10-29")];
  api.capFarForecast(days, today);
  const lv = (d) => d.risks.heat.level;
  if (lv(days[0]) !== "重") problems.push("近期预报（lead 5）被误封顶，用户拿不到真实等级");
  if (lv(days[1]) !== "关注") problems.push(`第 ${api.FAR_LEAD_DAYS + 3} 天预报未封顶为"关注"（实测 ${lv(days[1])}）`);
  if (lv(days[2]) !== "关注") problems.push(`第 ${api.FAR_LEAD_DAYS + 5} 天预报未封顶为"关注"（实测 ${lv(days[2])}）`);
  if (lv(days[3]) !== "关注") problems.push("最远预报日未封顶（烟测里最远那天恰好无风险时，旧断言恒为真）");
  if (!days[1].risks.heat.why.includes("不构成动作依据")) problems.push("封顶后依据文本仍像可执行结论");
  console.log(`远期封顶：lead5=${lv(days[0])} lead8=${lv(days[1])} lead10=${lv(days[2])} lead16=${lv(days[3])}（阈值 ${api.FAR_LEAD_DAYS} 天）`);
}

// 灌溉条件与"红色等级＋空动作清单"（评审 C-Q6：无水山坡园收到做不到的动作、花期低温只有待确认项）
{
  const mkDay = (risk, level, phenoId) => ({
    date: "2026-08-05", pheno: { id: phenoId, name: phenoId === "swell" ? "果实膨大期" : "开花期", note: "" },
    tmax: 38, tmin: 26, pr: 0.2,
    risks: { heat: null, drought: null, cold: null, freeze: null, [risk]: { level, why: "测试用触发依据" } },
  });
  api.setIrrCond("no");
  api.renderAdvice(mkDay("drought", "重", "swell"));
  let html = els.get("advice").innerHTML;
  if (html.includes("<ul><li>有灌溉：立即灌溉补墒")) problems.push("园块标记为无灌溉，却仍把灌溉动作排进可执行段");
  if (!html.includes("不适用（本园块记录为<b>无灌溉</b>）")) problems.push("无灌溉条件下没有给出「不适用」说明，用户不知道这条被谁否掉");
  api.setIrrCond("unknown");
  api.renderAdvice(mkDay("drought", "重", "swell"));
  html = els.get("advice").innerHTML;
  if (!html.includes("未记录本园块是否有灌溉条件")) problems.push("未记录灌溉条件时没有提示用户先确认能否执行");
  api.setIrrCond("yes");
  api.renderAdvice(mkDay("drought", "重", "swell"));
  if (!els.get("advice").innerHTML.includes("有灌溉：立即灌溉补墒")) problems.push("有灌溉条件下反而丢了第一条补墒动作");
  // 花期低温"重"级不得只剩待确认项（红色等级＋空动作清单）
  api.setIrrCond("unknown");
  api.renderAdvice(mkDay("cold", "重", "bloom"));
  const coldHtml = els.get("advice").innerHTML;
  if (coldHtml.includes("暂无可自动执行的动作")) problems.push("花期低温重级仍是空可执行清单（只有待确认动作）");
  if (!coldHtml.includes("拍照留档")) problems.push("花期低温缺可自动执行的观察动作（留档核对用）");
  if (!api.timingOf("freeze", "重").includes("3–5 天")) problems.push("冻害重级时效没有区分降温前 3–5 天灌水与立即物理防护");
  // 三条出口一致性（评审 A-Q1）：看板过滤了，规则问答/LLM 上下文也不能漏
  api.setIrrCond("no");
  const WATER = /灌溉|补水|浇透|浇水|灌透水/;
  for (const [risk, pheno, lvl] of [["drought", "swell", "重"], ["heat", "young", "中"],
                                    ["heat", "swell", "重"], ["freeze", "dormant", "重"]]) {
    window.__today = { date: "2026-08-05", pheno: { id: pheno, name: "X", note: "" },
      tmax: 38, tmin: -4, pr: 0.2,
      risks: { heat: null, drought: null, cold: null, freeze: null, [risk]: { level: lvl, why: "测试" } } };
    const sel = api.adviceFor(risk, pheno, lvl);
    const leakedGo = sel.go.filter((x) => WATER.test(x.t));
    if (leakedGo.length) problems.push(`${risk}.${pheno} 无灌溉园块的可执行段仍含需水动作：${leakedGo[0].t}`);
    if (!sel.na.length) problems.push(`${risk}.${pheno} 需水动作没有被移入"不适用"段（说明关键词判定没生效）`);
    const txt = api.ruleAnswer("现在要不要灌水？");
    if (risk === "drought" && /【需当地确认[^】]*】立即灌溉补墒/.test(txt) === false && /不适用】有灌溉：立即灌溉补墒/.test(txt) === false)
      problems.push("规则问答出口没把需水动作标成待确认或不适用：" + txt.slice(0, 70));
    if (risk === "drought" && new RegExp("^[\s\S]*" + "立即灌溉补墒").test(txt) && sel.go.some((x) => WATER.test(x.t)))
      problems.push("规则问答与看板对同一条动作给出不同结论");
  }
  console.log("灌溉条件分段（三出口）、花期低温非空清单、冻害时效覆写：均可复算");
}

// 挂果记录 + 冻害时效钟 + 导出报告正文（评审 C-Q7/C-R8①②③④⑤⑥⑩）
{
  api.setIrrCond("unknown");
  const frzList = (api.ADVICE.freeze.items.dormant || []).map(api.adviceOf);
  const fruitItem = frzList.find((x) => x.fruit);
  if (!fruitItem) problems.push("冻害休眠期建议里没有带挂果标记的条目（挂果输入无处生效）");
  const D0 = "2026-12-05";
  const mkFrz = () => ({
    date: D0, pheno: { id: "dormant", name: "越冬休眠期", note: "" }, tmax: 6, tmin: -3, pr: 5,
    risks: { heat: null, drought: null, cold: null, freeze: { level: "轻", why: "最低温 -3℃ ≤-3℃，注意防护" } },
  });
  const mkCool = (offset, lv) => ({
    date: api.shiftDay(D0, offset), forecast: true, tmax: 8, tmin: -5, pr: 0,
    pheno: { id: "dormant" }, risks: { freeze: { level: lv || "中", why: "" } },
  });
  window.__today = mkFrz();

  // ① 无水园块不能被关键词过滤器夺走"专为无水源写的那条动作"（评审 C-R8①）
  api.setIrrCond("no");
  const dsw = api.adviceFor("drought", "swell", "重");
  if (!dsw.go.some((x) => x.t.includes("拉水浇根")))
    problems.push("记录为无灌溉后，\"拉水浇根\"这条无水源动作被 irr 过滤误删，可执行段反而空了");
  if (!dsw.na.some((x) => x.t.includes("立即灌溉补墒")))
    problems.push("真正的灌溉类动作没被移出可执行段");
  // 同一件事要在**默认物候期**那条上验：它的文本里带"灌溉"二字，是关键词误判的真实落点
  const dflt = api.adviceFor("drought", "bud", "重");
  if (dflt.na.some((x) => x.t.includes("穴贮肥水或拉水")))
    problems.push("无水块把「穴贮肥水或拉水重点浇根」标成不适用——条目显式 irr:false 的覆盖没生效");
  if (!dflt.wait.some((x) => x.t.includes("穴贮肥水或拉水")))
    problems.push("「穴贮肥水或拉水重点浇根」应留在待确认段，而不是被删掉");
  api.setIrrCond("unknown");

  // ② 挂果三态：no=不适用、yes=可执行、unknown=既不给可执行也不说不适用，而是要求先记录
  api.setFruitCond("no");
  let sel = api.adviceFor("freeze", "dormant", "轻");
  if (!sel.na.some((x) => x.fruit && x.naWhy === "已采收完")) problems.push("记录为「已采收完」，抢收条目却没进不适用段");
  if (sel.go.some((x) => x.fruit)) problems.push("已采收完的园块仍收到抢收动作");
  if (!api.adviceLines("freeze", "dormant", "轻").some((s) => s.includes("已采收完，不适用")))
    problems.push("导出/问答出口没带上挂果不适用的原因");
  api.setFruitCond("yes");
  sel = api.adviceFor("freeze", "dormant", "轻");
  if (!sel.go.some((x) => x.fruit)) problems.push("记录为「仍挂果」时抢收条目应回到可执行段");
  api.setFruitCond("unknown");
  sel = api.adviceFor("freeze", "dormant", "轻");
  if (sel.go.some((x) => x.fruit)) problems.push("挂果未记录时抢收（不可逆）仍进可执行段——默认乐观假设");
  if (!sel.wait.some((x) => x.fruit && x.waitWhy === "fruit"))
    problems.push("挂果未记录时抢收条目没落到\"先记录再执行\"段");
  const both = sel.wait.find((x) => x.fruit && x.confirm);
  if (both && !(both.waitTags.includes("confirm") && both.waitTags.includes("fruit")))
    problems.push("既是外部性动作又取决于挂果的条目只挂了一个标签——需当地确认那半句被藏起来了");
  if (!api.adviceLines("freeze", "dormant", "轻").some((s) => s.includes("先在本页记录是否仍挂果")))
    problems.push("问答/导出出口没带挂果未记录的提示标记");

  // ③ 冻害时效钟：只用 8 天内读数，且预报缺失≠没有降温
  window.__recent = [window.__today, mkCool(1), mkCool(6)];
  let clock = api.freezeClock("中", D0);
  if (!clock.includes("距降温只有 1 天") || !clock.includes("不要临冻灌透"))
    problems.push(`冻害钟在不足 3 天时没给出"别灌透"的否决：${clock}`);
  window.__recent = [window.__today, mkCool(4)];
  clock = api.freezeClock("中", D0);
  if (!clock.includes("还有 4 天") || !clock.includes("已到 3–5 天窗口")) problems.push(`冻害钟窗口判断错：${clock}`);
  window.__recent = [window.__today, mkCool(6)];
  clock = api.freezeClock("中", D0);
  if (!clock.includes(api.shiftDay(api.shiftDay(D0, 6), -5))) problems.push(`冻害钟没算出灌水日期区间：${clock}`);
  window.__recent = [window.__today, mkCool(4, "轻")];
  // 钟指向的那一天只是"轻"级：即使今天自己出的是"中"，也不许下灌水指令（评审 A-R13②：
  // 上一版 (level || r.level) 只在无等级屏生效，中级屏仍对轻级命中日照发"今明两天灌透水"）
  clock = api.freezeClock("中", D0);
  if (!clock.includes("不据此安排灌透水"))
    problems.push(`命中日只是"轻"级却仍下灌水指令（钟按今日等级说话）：${clock}`);
  clock = api.freezeClock("轻", D0);
  if (!clock.includes("不据此安排灌透水")) problems.push(`"轻"级不该下灌水指令：${clock}`);
  window.__recent = [window.__today, mkCool(9)];
  clock = api.freezeClock("中", D0);
  if (!clock.includes("内的可靠预报里没有降温日") || clock.includes("灌透水安排在"))
    problems.push(`8 天以外的封顶读数被钟当成灌水依据：${clock}`);
  window.__recent = [window.__today, { date: api.shiftDay(D0, 3), forecast: true, tmin: 4, tmax: 9,
    pheno: { id: "dormant" }, risks: { freeze: null } }];
  clock = api.freezeClock("中", D0);
  if (!clock.includes("天内没有触发冻害的日")) problems.push(`预报里确无降温日时应照实说：${clock}`);
  window.__recent = [window.__today];
  clock = api.freezeClock("中", D0);
  if (!clock.includes("预报序列取不到")) problems.push(`预报缺失被写成"没有降温"：${clock}`);
  window.__recent = [window.__today, mkCool(4)];
  if (!api.adviceFor("freeze", "dormant", "中").timing.includes("冻害时效钟"))
    problems.push("冻害时效没附距离天数（3–5 天规则无法执行）");
  if (api.adviceFor("drought", "swell", "中").timing.includes("冻害时效钟"))
    problems.push("冻害钟被错挂到干旱时效");

  // ④ 仍挂果＋非休眠期：等级口径不适用，必须显式提示而不是沉默（评审 C-R8⑤）
  const ripe = {
    date: "2026-11-20", pheno: { id: "ripe", name: "成熟采收期", note: "" }, tmax: 8, tmin: -3, pr: 4,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: { level: "中", why: "最低温 -3℃，晚熟果易受冻" } },
  };
  window.__today = ripe; window.__recent = [ripe];
  api.setFruitCond("yes");
  api.renderAdvice(ripe);
  const ripeHtml = els.get("advice").innerHTML;
  if (!ripeHtml.includes("带果树的抗冻能力更低")) problems.push("挂果园块在采收期遇 −3℃ 时，看板没提示判级口径不适用");
  if (!api.ruleAnswer("寒潮来了防冻要做什么？").includes("带果树的抗冻能力更低"))
    problems.push("规则问答出口漏了挂果口径提示");
  if (!JSON.parse(api.buildContext()).blockNotice) problems.push("LLM 上下文没带挂果口径提示，模型会照低口径答题");
  if (!api.reportHtml(ripe).includes("带果树的抗冻能力更低")) problems.push("导出报告漏了挂果口径提示");
  api.setFruitCond("no");
  if (api.fruitNotice(window.__today)) problems.push("已采收完却仍提示挂果口径差异");

  // ⑤ 旱情未判级时问答不许按"中"级给灌水动作（评审 C-R8⑩）
  const nod = {
    date: "2026-07-05", pheno: { id: "swell", name: "果实膨大期", note: "" }, tmax: 34, tmin: 25, pr: 3,
    dq: { windowDays: 20, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: null },
  };
  window.__today = nod; window.__recent = [nod];
  const ansNoD = api.ruleAnswer("现在要不要浇水？");
  if (ansNoD.includes("立即灌溉补墒") || ansNoD.includes("浇透根系分布层"))
    problems.push("旱情判不了，问答仍输出灌溉指令：" + ansNoD.slice(0, 90));
  if (!ansNoD.includes("不据此给灌水动作")) problems.push("旱情未判级时问答没有说明为什么不给动作");

  // ⑥ 导出报告正文（此前从没被执行过的出口）
  const d2 = mkFrz(); window.__today = d2; window.__recent = [d2, mkCool(4)];
  api.setFruitCond("no");
  const html = api.reportHtml(d2);
  if (html.includes("undefined") || html.includes("NaN")) problems.push("导出报告正文含 undefined/NaN");
  if (!html.includes("冻害时效钟")) problems.push("导出报告没带冻害时效钟");
  if (!html.includes("挂果情况记录为：已采收完")) problems.push("导出报告没写明挂果记录");
  if (!html.includes("按本园块记录不适用的动作")) problems.push("导出报告没把挂果不适用条目单独成段");
  api.setFruitCond("unknown");
  const html2 = api.reportHtml(d2);
  if (!html2.includes("本园块未记录是否仍挂果")) problems.push("挂果未记录时导出报告没有提示先确认");
  if (!html2.includes("本条归属取决于")) problems.push("挂果未记录的条目在报告里仍带签字栏（把记录问题当成审批问题）");
  // 前置钟：今天没冻、未来 8 天内有冻害日时，无等级那一屏也必须看见钟（评审 A-R11①）
  const calm = { date: D0, pheno: { id: "dormant", name: "越冬休眠期", note: "" }, tmax: 6, tmin: 1.5, pr: 4,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: null } };
  window.__today = calm; window.__recent = [calm, mkCool(4)];
  api.setFruitCond("unknown");
  api.renderAdvice(calm);
  const calmHtml = els.get("advice").innerHTML;
  if (!calmHtml.includes("冻害时效钟")) problems.push("今日无冻害级但 4 天后有，看板却只写「当前无中重度风险」");
  if (!calmHtml.includes("不是冻害等级")) problems.push("前置钟没和等级区分开，农户会读成已触发");
  if (!api.ruleAnswer("寒潮来了防冻要做什么？").includes("冻害时效钟"))
    problems.push("规则问答在未触发时也不给前置钟");
  // 无水园在采收期冻害下必须还剩做得到的动作（评审 A-R11⑤）
  api.setIrrCond("no");
  const rsel2 = api.adviceFor("freeze", "ripe", "中");
  if (!rsel2.go.some((x) => x.t.includes("树冠覆盖防寒")))
    problems.push("无灌溉园块在采收期冻害下连「覆盖防寒」都被连坐移走，又回到「红色等级＋空动作清单」");
  api.setIrrCond("unknown");
  // ⑦ 无水园块不许从钟里拿到灌水指令；前置钟不许把等级写死（评审 A-R12①）
  api.setIrrCond("no");
  const clockNoIrr = api.freezeClock("中", D0);
  if (clockNoIrr.includes("要灌透水") || clockNoIrr.includes("灌透水安排在"))
    problems.push(`记录为无灌溉的园块仍从时效钟拿到灌水指令：${clockNoIrr.slice(0, 70)}`);
  api.setIrrCond("unknown");
  // ⑧ 挂果口径提示必须在休眠期出现——冻害等级只在休眠期与采收期出，关掉这两档等于永久藏住这句话（评审 A-R12②）
  const dor = { date: D0, pheno: { id: "dormant", name: "越冬休眠期", note: "" }, tmax: 2, tmin: -4, pr: 4,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: { level: "中", why: "最低温 -4℃，休眠期冻害" } } };
  window.__today = dor; window.__recent = [dor]; api.setFruitCond("yes");
  if (!api.fruitNotice(dor)) problems.push("休眠期＋仍挂果＋−4℃ 时挂果口径提示为空，docs/01 承诺的那句落不了地");
  if (!api.reportHtml(dor).includes("带果树的抗冻能力更低")) problems.push("导出报告在休眠期丢了挂果口径提示");
  // ⑨ "重级须现场核实"必须四处出口都在，不许只写在 docs 里（评审 C-R12④）
  const sev = { date: D0, pheno: { id: "swell", name: "果实膨大期", note: "" }, tmax: 37, tmin: 26, pr: 0,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: { level: "重", why: "最高温 37℃" }, drought: { level: "重", why: "连续 20 天无有效降水" }, cold: null, freeze: null } };
  window.__today = sev; window.__recent = [sev]; api.setFruitCond("unknown");
  if (!api.reportHtml(sev).includes("尚未对表")) problems.push("导出报告没有重级现场核实句");
  if (!api.adviceLines("drought", "swell", "重").join("").includes("尚未对表")) problems.push("问答/LLM 出口没有重级现场核实句");
  api.renderAdvice(sev);
  if (!els.get("advice").innerHTML.includes("尚未对表")) problems.push("看板没有重级现场核实句");
  if (!api.reportHtml(calm).includes("冻害时效钟")) problems.push("无等级日的导出报告丢了前置钟，四出口不同串");
  // ⑨b 连日同级不许重复发同一条紧急指令（评审 A-R12②：留出段 2025 有连续 36 天花期"重"级）
  const run = ["2026-07-01", "2026-07-02", "2026-07-03"].map((dt) => ({
    date: dt, pheno: { id: "swell", name: "果实膨大期", note: "" }, tmax: 37, tmin: 26, pr: 0,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: { level: "重", why: "最高温 37℃" }, drought: { level: "重", why: "连续 20 天无有效降水" }, cold: null, freeze: null } }));
  window.__today = run[2]; window.__recent = run;
  const t3 = api.adviceFor("drought", "swell", "重").timing;
  if (!t3.includes("已连续 3 天")) problems.push("连续 3 天同级却没有去重提示，第 3 天仍照发 24–48 小时紧急指令：" + t3.slice(0, 70));
  if (!api.reportHtml(run[2]).includes("已连续 3 天")) problems.push("导出报告没有连日去重提示（四出口不同串）");
  window.__today = run[0]; window.__recent = [run[0]];
  if (api.adviceFor("drought", "swell", "重").timing.includes("已连续"))
    problems.push("只有第一天却也报连日提示（连日计数是写死的）");
  window.__today = run[2]; window.__recent = [run[0], run[2]];
  if (api.adviceFor("drought", "swell", "重").timing.includes("已连续 2 天"))
    problems.push("断日（缺 07-02）被当成连日累加");
  window.__today = sev; window.__recent = [sev];
  // ⑩ 越权动作核对与日期数字白名单（评审 C-R12②⑤）：gate 里的动作被写成今天都做、
  // 且全篇没有一句「先确认/咨询」，必须点名；带咨询措辞的合规回答不许误报。
  {
    const gatedList = api.pendingConfirmList();
    if (!gatedList.length) problems.push("膨大期重级下门控清单为空，越权动作核对这一层无从验证");
    else {
      const kws = ["熏烟", "覆膜", "地膜", "喷药", "喷施", "保水剂", "叶面肥", "速效肥", "追施", "灌透水", "疏除", "焚烧"];
      const k = kws.find((x) => String(gatedList[0]).includes(x));
      if (!k) problems.push(`门控条目「${String(gatedList[0]).slice(0, 24)}」不含任何被核对的关键词，这条守卫打不响`);
      else {
        const bad = api.llmCheck(`今天立即${k}一次，另外每株灌 2026 升。`);
        if (!bad.some((p) => p.includes(k))) problems.push(`越权动作「${k}」没被抓到（当前命中：${bad.join("；")}）`);
        if (!bad.some((p) => p.includes("2026"))) problems.push("年份数字仍从日期串混进白名单：凭空编的用量抓不到");
        // 否定式 hedge 不许豁免（评审 A-R13⑩：全篇判时"不必咨询农技员"反而替越权句放行）
        const neg = api.llmCheck(`今晚立即${k}一次，不必咨询农技员。`);
        if (!neg.some((p) => p.includes(k))) problems.push(`否定式 hedge 被当成合规放行：${neg.join("；")}`);
        const okAns = api.llmCheck(`建议先咨询农技员，再决定是否${k}；拿不准就先不做。`);
        if (okAns.some((p) => p.includes(k))) problems.push(`带「咨询农技员」措辞的合规回答被误判：${okAns.join("；")}`);
        // 时效钟串里内嵌 ISO 日期（"下次降温在 2026-12-09"）：这些数字不许进白名单（评审 A-R13⑨）
        const frz = { date: "2026-12-05", pheno: { id: "dormant", name: "越冬休眠期", note: "" }, tmax: 2, tmin: -4, pr: 4,
          dq: { windowDays: 30, windowBad: 0, etOk: true },
          risks: { heat: null, drought: null, cold: null, freeze: { level: "中", why: "最低温 -4℃，休眠期冻害" } } };
        window.__today = frz; window.__recent = [frz,
          { date: "2026-12-09", forecast: true, tmax: 8, tmin: -6, pr: 0, pheno: { id: "dormant" },
            risks: { freeze: { level: "中", why: "" } } }];
        const clk = api.adviceFor("freeze", "dormant", "中").timing;
        if (!clk.includes("2026-12-09")) problems.push("构造的冻害钟串里没有 ISO 日期，这条泄漏测试是空跑：" + clk.slice(0, 60));
        // M42 的样本必须放在**时效串里真的有 ISO 日期**的状态：日期被剥掉时 2026 不在 misc、
        // "每 2026 天"必须被抓；M42 把剥除关掉后 2026 混进 misc、这句就漏——膨大期时效串没有日期，
        // 样本放那里等于没测（变异闸门抓到的真逃逸）。
        const bigDay = api.llmCheck("此后每 2026 天检查一次树势。");
        if (!bigDay.some((p) => p.includes("2026")))
          problems.push(`日期数字仍可通过时间类单位放行（M42 无牙）：${bigDay.join("；")}`);
        const leaky = api.llmCheck("每株灌 2026 升，三天后再灌一次。");
        if (!leaky.some((p) => p.includes("2026")))
          problems.push(`串内日期数字混进白名单，凭空用量抓不到（当前命中：${leaky.join("；")}）`);
        window.__today = sev; window.__recent = [sev];
        const cite = api.llmCheck(`12 月 5 日的判级为重度风险，请以看板为准。`);
        if (cite.some((p) => p.includes("上下文里的数"))) problems.push("合规的日期引用被误判越权：" + cite.join("；"));
        // 第四出口（模型上下文）也必须拿到重级现场核实限定词（评审 A-R13⑥）
        if (!("verify" in (JSON.parse(api.buildContext()).actions[0] || {})))
          problems.push("给 LLM 的 actions 条目没有 verify 字段，重级限定词到不了模型");
        if (!api.buildContext().includes("尚未对表"))
          problems.push("重级现场核实串没进 LLM 上下文——四出口里第四处落空");
        // 用量类单位只认建议条目本身的数字（评审 R14②：4/9/12 曾从时效串与"约 9km"混进用量白名单）
        const dose = api.llmCheck("每株灌 9 升，再灌 100 升，喷 12 次。");
        ["9 升", "100 升", "12 次"].forEach((x) => {
          if (!dose.some((p) => p.includes(x))) problems.push(`用量数字没按单位分桶，${x} 被放行：${dose.join("；")}`);
        });
        // 分桶之后，两条老变异各失去了牙：M33（misc 清空）与 M42（日期混进白名单）都不再影响用量单位。
        // 所以必须为"时间类单位"补样本：合规引用要落在 misc（M33 一清就误报），凭空大数要抓得住（M42 一放就漏）。
        const citeTiming = api.llmCheck("按执行时效，重级要在 48 小时内完成，并在 24 小时内留痕。");
        if (citeTiming.length) problems.push(`引用自家时效串的数字被判越权（misc 桶被抽空的信号）：${citeTiming.join("；")}`);
        const tagQuote = api.llmCheck(`【需当地确认后才可执行】${k}，今晚就做。`);
        if (!tagQuote.some((p) => p.includes(k)))
          problems.push(`原样引用自家确认标签即豁免越权动作：${tagQuote.join("；")}`);
      }
    }
  }
  console.log("挂果三态与口径提示、无水园动作保全、冻害钟 7 条分支、未判级不编动作、导出正文、重级现场核实四出口：均可复算");
}

// 产品真入口与本轮新增守卫（评审 A-R8④⑤⑥⑧⑨）
{
  const D0 = "2026-12-05";
  const frz = {
    date: D0, pheno: { id: "dormant", name: "越冬休眠期", note: "" }, tmax: 6, tmin: -4, pr: 4,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: { level: "中", why: "最低温 -4℃ ≤-5℃？测试" } },
  };
  const mkCool = (offset) => ({
    date: api.shiftDay(D0, offset), forecast: true, tmax: 8, tmin: -6, pr: 0,
    pheno: { id: "dormant" }, risks: { freeze: { level: "中", why: "" } },
  });
  // ① 导出按钮的真入口：必须真的走到 downloadFile 并带上日期文件名（不是只测正文函数）
  window.__today = frz; window.__recent = [frz, mkCool(4)];
  api.setFruitCond("yes");
  const before = downloads.length;
  api.exportReport();
  if (downloads.length !== before + 1) problems.push("exportReport 没有真的调用下载：用户点导出不会有文件");
  else if (!/^丹橘知旱-建议报告-2026-12-05\.html$/.test(downloads[downloads.length - 1][0]))
    problems.push("导出文件名不对：" + downloads[downloads.length - 1][0]);

  // ② 清除本机记录：绑定存在、点下去真的删掉 key 与园块条件
  api.setIrrCond("no"); api.setFruitCond("yes");
  localStorage.setItem("confirmed_actions", JSON.stringify([{ t: "x", by: "y", at: D0 }]));
  localStorage.setItem("deepseek_key", "sk-test");
  api.wireLocalControls();
  for (const id of ["clear-key", "clear-local"]) {
    if (typeof document.getElementById(id).onclick !== "function")
      problems.push(`#${id} 按钮没有绑定处理函数（材料承诺的"撤回方式"落空）`);
  }
  document.getElementById("clear-key").onclick();
  if (localStorage.getItem("deepseek_key") !== null) problems.push("清除 Key 按钮没删掉 deepseek_key");
  document.getElementById("clear-local").onclick();
  if (localStorage.getItem("confirmed_actions") !== null) problems.push("清除记录按钮没删掉确认清单");
  if (Object.keys(store).some((k) => k.startsWith("irr_cond::") || k.startsWith("fruit_cond::")))
    problems.push("清除记录按钮没删掉园块条件");

  // ③ 钟不许用被封顶的第 8 天读数（capFarForecast 的口径是 lead<8 才不动，两边必须同一边界）
  window.__today = frz; window.__recent = [frz, mkCool(8)];
  const c8 = api.freezeClock("中", D0);
  if (!c8.includes("内的可靠预报里没有降温日") || c8.includes("灌透水安排在"))
    problems.push(`lead=8 的封顶读数仍被钟当依据：${c8}`);

  // ④ 挂果过滤要覆盖采收期那条"抢收已熟果"，不能只管休眠期（评审 A-R8④）
  const ripe = {
    date: "2026-11-20", pheno: { id: "ripe", name: "成熟采收期", note: "" }, tmax: 8, tmin: -5, pr: 4,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: { level: "中", why: "最低温 -5℃，晚熟果易受冻" } },
  };
  api.setFruitCond("no");
  const rsel = api.adviceFor("freeze", "ripe", "中");
  if (rsel.go.some((x) => x.t.includes("抢收已熟果")))
    problems.push("记录为已采收完，采收期的抢收条目仍可执行（fruit 标记漏打）");
  if (!rsel.na.some((x) => x.t.includes("抢收已熟果")))
    problems.push("采收期的抢收条目没进不适用段");

  // ⑤ 引用本工具自己的动作文本不许被判越权（评审 A-R8⑥）——用幼果期热害库里的"10cm"条目实测
  window.__today = {
    date: "2026-06-01", pheno: { id: "young", name: "幼果期", note: "" }, tmax: 36.2, tmin: 25, pr: 0.2,
    dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: { level: "中", why: "最高温 36.2℃ ≥35℃" }, drought: { level: "中", why: "连续 8 天无有效降水，30 天累计 44.0 mm" },
             cold: null, freeze: null },
  };
  window.__recent = [window.__today];
  api.setIrrCond("yes");
  const quoted = "按建议：树盘覆盖秸秆或杂草 10cm 保湿降温，傍晚少量多次补水，本周内完成。";
  const bad = api.llmCheck(quoted).filter((p) => p.includes("10cm") || p.includes("本周"));
  if (bad.length) problems.push("模型引用本工具自己给的动作数字被判越权：" + bad.join("；"));
  const invented = api.llmCheck("明天最高温会到 45.6℃，风险升到重级 4 天。");
  if (!invented.length) problems.push("编造的未来读数不再被拦（白名单放宽过头）");
  console.log("导出真入口、清除按钮实际生效、lead=8 边界、采收期挂果过滤、引用自身文本不误报：均可复算");
}


// R9 落地核验（评审 A-R9③④⑤⑦⑩）：委托事件真的派发、引用看板依据不误报、
// 换单位写法仍被拦、清除后不留署名后门、陈旧的挂果记录不得继续门控不可逆动作
{
  const D0 = "2026-12-05";
  const day = (o) => ({ date: o.date, pheno: { id: o.pheno || "dormant", name: o.name || "越冬休眠期", note: "" },
    tmax: o.tmax ?? 6, tmin: o.tmin ?? -4, pr: o.pr ?? 3, dq: { windowDays: 30, windowBad: 0, etOk: true },
    risks: { heat: null, drought: null, cold: null, freeze: null, ...(o.risks || {}) } });
  // ① 委托：把事件派发进去，而不是直接调处理函数
  listeners.length = 0;
  api.wireDelegates();
  const changeFns = listeners.filter(([ty]) => ty === "change").map(([, f]) => f);
  if (changeFns.length < 2) problems.push(`change 委托只注册了 ${changeFns.length} 条（条件下拉＋确认开关应各一条）`);
  const inputFns = listeners.filter(([ty]) => ty === "input").map(([, f]) => f);
  if (!inputFns.length) problems.push("input 委托没注册，确认人输入框不会写进缓存");
  changeFns.forEach((f) => f({ target: { id: "fruit", value: "no" } }));
  if (api.fruitCond() !== "no") problems.push("派发 fruit 事件后挂果记录没变（委托没通）");
  // ② 引用看板自己的触发依据不该被判越权
  window.__today = day({ date: D0, tmin: -4, risks: { freeze: { level: "中", why: "最低温 -4℃ ≤-5℃，秋梢与晚熟果易受冻" },
    drought: { level: "重", why: "连续 8 天无有效降水，30 天累计 44.0 mm，超该物候期定标分位中级线 36.9 mm" } } });
  window.__recent = [window.__today];
  const basis = "看板依据：连续 8 天无有效降水，30 天累计 44.0 mm，超该物候期定标分位中级线 36.9 mm；最低温 -4℃。";
  const falsePos = api.llmCheck(basis).filter((s) => s.includes("白名单外"));
  if (falsePos.length) problems.push("原样引用看板的触发依据仍被判凭空数字：" + falsePos.join("；"));
  // ③ 换单位写法不许绕过
  for (const [名称, 句子] of [["毫米写法", "今天降水 24 毫米，需立即排水。"], ["小时写法", "提前 999 小时启动防护。"],
                              ["度写法", "明天最高温会到 45.6 度。"]]) {
    if (!api.llmCheck(句子).length) problems.push(`越权样本「${名称}」没被抓：${句子}`);
  }
  // ④ 清除记录后，本次会话的确认人不该还能免署名复活确认
  const pend = (api.pendingConfirmList()[0] || null);
  if (!pend) problems.push("当前没有待确认动作，测不到确认后门");
  else {
    const ev = { target: { dataset: { confirmBy: pend }, value: "农技站 张站长" } };
    inputFns.forEach((f) => f(ev));
    const box = { dataset: { confirmText: pend }, checked: true };
    changeFns.forEach((f) => f({ target: box }));
    if (!api.confirmedActions().some((r) => r.t === pend)) problems.push("署名后没写入确认记录（确认链路断了）");
    api.clearLocalRecords();
    const box2 = { dataset: { confirmText: pend }, checked: true };
    changeFns.forEach((f) => f({ target: box2 }));
    if (api.confirmedActions().some((r) => r.t === pend))
      problems.push("清除本机记录后，未重新署名就复活了确认——撤回方式没做到底");
  }
  // ⑤ 陈旧的挂果记录按未记录处理（不可逆动作的门控不建立在过期输入上）
  const old = new Date(Date.now() - 200 * 86400000 + 8 * 3600000).toISOString().slice(0, 10);
  Object.keys(store).filter((k) => k.startsWith("fruit_cond::")).forEach((k) => delete store[k]);
  api.setFruitCond("yes");
  const fk = Object.keys(store).find((k) => k.startsWith("fruit_cond::"));
  store[fk] = JSON.stringify({ v: "yes", at: old });
  if (api.fruitCond() !== "unknown") problems.push("200 天前的挂果记录仍被当现行事实（门控建立在陈旧输入上）");
  // ⑥ index.html 与 app.js 的 id 耦合必须核对，而不是靠 DOM 桩自动造元素
  const fs2 = (await import('node:fs')).default;
  const html = fs2.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const used = [...new Set([...src.matchAll(/\$\("([^"]+)"\)|getElementById\("([^"]+)"\)/g)]
    .map((m) => m[1] || m[2]))];
  const missing = used.filter((id) => !new RegExp(`id="${id}"`).test(html));
  if (missing.length) problems.push(`app.js 引用的这些 id 在 index.html 里不存在（桩会自动造元素掩盖）：${missing.join(",")}`);
  console.log(`委托派发、依据引用不误报、三种单位写法均拦、清除不留后门、陈旧记录降级、id 耦合核对 ${used.length} 个：均可复算`);
}

// LLM 解释层：动作全文进上下文 + 事后一致性核对（评审 C-Q10）
{
  window.__today = { date: "2026-08-05", pheno: { id: "swell", name: "果实膨大期", note: "" },
    tmax: 38.4, tmin: 26, pr: 0.2,
    risks: { heat: { level: "中", why: "测试" }, drought: { level: "重", why: "测试" },
             cold: null, freeze: null } };
  window.__recent = [];
  api.setIrrCond("no");
  const ctx = JSON.parse(api.buildContext());
  const act = (ctx.actions || []).find(x => x.risk === "干旱");
  if (!act) problems.push("LLM 上下文没有 actions 字段（模型只能自己编农事动作）");
  else {
    if (!act.timing) problems.push("LLM 上下文的可执行动作没带执行时效");
    if (!act.executable.length) problems.push("LLM 上下文的可执行动作为空，但看板是重级");
    if (!act.notApplicable.some(s => s.includes("立即灌溉补墒")))
      problems.push("无灌溉条件下，被排除的动作没告诉模型");
  }
  api.setIrrCond("unknown");
  const good = api.llmCheck("今天最高温 38.4℃、最低温 26℃，膨大期缺水，本周内完成树盘覆盖。");
  if (good.length) problems.push("合规回答被误报：" + good.join("、"));
  // 评委 A 复算出的 8 条越权样本：白名单若含"整坨上下文数字"，前四条全会放过
  const attacks = [
    ["凭空高温", "今天预计 48℃，达到重级。"],
    ["凭空雨量", "今天降水 24mm，需立即排水。"],
    ["日期当温度", "最高气温 2026℃。"],
    ["凭空用量", "每株灌 120 升，连灌 9 天。"],
    ["凭空等级", "看板当前无轻级风险，但明天会降到关注级以下。"],
    ["凭空阈值", "膨大期 p99 为 250.0mm，已超线。"],
    ["凭空温度", "明天最高温将达 41.7℃，请灌水 200mm 浇透。"]];
  const misses = attacks.filter(([, txt]) => api.llmCheck(txt).length === 0).map(([n]) => n);
  if (misses.length) problems.push("LLM 事后核对放过越权回答：" + misses.join("、"));
  const neg = api.llmCheck("不必按中级处理，本周内择机即可。");
  if (neg.length) problems.push("否定句被误判为越权等级：" + neg.join("、"));
  console.log("LLM 事后核对：合规 0 项；越权样本 " + (attacks.length - misses.length) + "/" + attacks.length + " 被抓");
}

// 页脚/报告的判级口径串必须带规则版本与佐证上限（评审 C-Q：截图里看不出跑的是哪版规则）
const stamp = api.ruleStamp();
if (!stamp.includes(api.RULE_VERSION)) problems.push("ruleStamp() 未包含规则版本号");
if (!stamp.includes(api.DEF_SOLO_CAP)) problems.push("ruleStamp() 未说明蒸散口径单独出级上限");
console.log("判级口径串：", stamp);

if (problems.length) {
  console.error('[SMOKE FAIL]\n  ' + problems.join('\n  '));
  process.exitCode = 1;
} else {
  console.log('[SMOKE OK] load() 全流程无异常，渲染串完整，停服横幅可用');
}
