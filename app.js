// 丹橘知旱 · 主逻辑
// 数据：Open-Meteo Archive（ERA5-Land 再分析）+ Forecast（ECMWF）——浏览器直连，免密钥
// 引擎：phenology.js 中的物候与规则库（确定性计算）；LLM 仅做解释层（可选）

const $ = (id) => document.getElementById(id);
// 所有外部/用户文本进 innerHTML 前一律转义（大模型输出与用户提问都是不可信内容，评审 A-Q4）
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LEVEL_COLOR = { "重": "var(--red)", "中": "var(--orange)", "轻": "var(--yellow)", "关注": "var(--blue)", "无": "var(--gray)" };
const RISK_NAME = { heat: "高温热害", drought: "干旱", cold: "花期/幼果低温", freeze: "越冬冻害" };
const RISK_SHORT = { heat: "高温", drought: "干旱", cold: "低温", freeze: "冻害" };

let currentSite = SITES[0];

// ---------- 数据获取 ----------
// Archive API 有约 2~5 天滞后；Forecast API 覆盖今天起 16 天。两者拼接保证"今天"有值。
// 外部依赖只有一个（Open-Meteo），故对它做显式超时+一次重试+返回契约校验（评审 A-Q8）：
// 无密钥时无法用配额换可靠性，至少要失败得可见、可归因，而不是静默半张图。
async function httpJson(url, timeoutMs = 12000, tries = 2) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { signal: ctl.signal });
      if (!res.ok) throw new Error("HTTP " + res.status);
      return await res.json();
    } catch (e) {
      lastErr = e.name === "AbortError" ? `超时（${timeoutMs / 1000}s）` : e.message;
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error(lastErr || "请求失败");
}

function pickDaily(j, vars) {
  // 返回契约校验：字段缺失/长度不齐即显式报错，不让 undefined 静默变成等级
  const d = j && j.daily;
  if (!d || !Array.isArray(d.time)) throw new Error("数据格式不符（缺少 daily.time）");
  for (const v of vars) {
    if (!Array.isArray(d[v])) throw new Error(`数据格式不符（缺少 ${v}）`);
    if (d[v].length !== d.time.length) throw new Error(`数据格式不符（${v} 长度不齐）`);
  }
  return d;
}

async function fetchArchive(lat, lon, startDate, endDate) {
  const url = `https://archive-api.open-meteo.com/v1/archive?latitude=${lat}&longitude=${lon}` +
    `&start_date=${startDate}&end_date=${endDate}` +
    `&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration&timezone=Asia%2FShanghai`;
  const VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "et0_fao_evapotranspiration"];
  const d = pickDaily(await httpJson(url), VARS);
  return d.time.map((t, i) => ({
    date: t,
    tmax: d.temperature_2m_max[i],
    tmin: d.temperature_2m_min[i],
    pr: d.precipitation_sum[i],
    et: d.et0_fao_evapotranspiration[i], // 可为 null：缺 ET0 时蒸散口径自动退出，只走降水口径
  })).filter(x => x.tmax != null && x.tmin != null && x.pr != null); // 丢弃滞后的空尾日
}

async function fetchForecast(lat, lon) {
  // past_days=6 回填 archive 滞后期（now-6d~now-1d），避免合并序列出现空洞
  // （空洞会使 streak 跨洞累积、rain30 窗口被拉长——评审发现的工程缺陷）
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
    `&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration&past_days=6&forecast_days=16&timezone=Asia%2FShanghai`;
  const VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "et0_fao_evapotranspiration"];
  const d = pickDaily(await httpJson(url), VARS);
  // 与 fetchArchive 同规则剔除缺测日（评审 A-Q4：两条取数路径此前不一致，
  // 前端会把 null 降水当 0mm 记进累计、当"有水"清零连旱，从而虚高干旱等级）
  return d.time.map((t, i) => ({
    date: t,
    tmax: d.temperature_2m_max[i],
    tmin: d.temperature_2m_min[i],
    pr: d.precipitation_sum[i],
    et: d.et0_fao_evapotranspiration[i],
    forecast: true,
  })).filter(x => x.tmax != null && x.tmin != null && x.pr != null);
}

function mergeSeries(past, forecast) {
  const seen = new Set(past.map(x => x.date));
  return [...past, ...forecast.filter(x => !seen.has(x.date))];
}

// ---------- 风险引擎 ----------
// 输入按时间升序的逐日序列（需覆盖计算日的前 ≥30 天），输出每日风险
function assess(days) {
  const out = [];
  let heatStreak = 0, dryStreak = 0;
  for (let i = 0; i < days.length; i++) {
    const day = days[i];
    const date = parseDay(day.date);
    // 日期连续性检查：断日即清零连续计数（防止 streak 跨数据洞累积、窗口被拉长）
    if (i > 0) {
      const prev = parseDay(days[i - 1].date);
      if ((date - prev) / 86400000 !== 1) { heatStreak = 0; dryStreak = 0; }
    }
    const pheno = phenologyOf(date);
    heatStreak = (day.tmax != null && day.tmax >= 35) ? heatStreak + 1 : 0;
    dryStreak = (day.pr != null && day.pr < 1) ? dryStreak + 1 : 0;
    // 回看窗口按日历天数截取（防数据洞拉长实际跨度——评审 Q5）：
    // rain30 = 日期 ≥ 当日-29 天的元素和；windowDays = 窗口内实际天数
    let rain30 = 0, windowDays = 0, windowBad = 0, et30 = 0, etMissing = 0;
    let rain7 = 0;
    for (let j = i; j >= 0; j--) {
      const span = (date - parseDay(days[j].date)) / 86400000;
      if (span > 29) break;
      windowDays++;
      // 显式缺测语义：窗口内任一日降水或最高温缺失，则该日不判干旱（不当 0mm，也不静默丢日）
      if (days[j].pr == null || days[j].tmax == null) { windowBad++; continue; }
      rain30 += days[j].pr;
      if (span <= 6) rain7 += days[j].pr;
      if (days[j].et == null) etMissing++; else et30 += days[j].et;
    }
    const ctx = { pheno, heatStreak, dryStreak, rain30, rain7, windowDays, windowBad,
                  dayIndex: i, et30: etMissing === 0 ? et30 : null };
    const risks = {};
    for (const r of RISKS) risks[r.id] = r.eval(day, ctx);
    out.push({ ...day, pheno, risks, dq: { windowDays, windowBad, etOk: etMissing === 0 } });
  }
  return out;
}

function worstRisk(risks) {
  const order = ["重", "中", "轻", "关注"];
  for (const lv of order) for (const k in risks) if (risks[k] && risks[k].level === lv) return { id: k, ...risks[k] };
  return null;
}

// ---------- 渲染 ----------
function renderSites() {
  $("site-list").innerHTML = SITES.map((s, i) =>
    `<button class="site-btn ${i === 0 ? "active" : ""}" data-i="${i}" title="${s.note}（${s.lat}, ${s.lon}）">${s.name}</button>`).join("");
  document.querySelectorAll(".site-btn").forEach(b => b.onclick = () => {
    document.querySelectorAll(".site-btn").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    currentSite = SITES[+b.dataset.i];
    load();
  });
  $("use-custom").onclick = () => {
    const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
    currentSite = { name: `自定义园块（${lat}, ${lon}）`, lat, lon, note: "用户输入坐标" };
    document.querySelectorAll(".site-btn").forEach(x => x.classList.remove("active"));
    load();
  };
}

function renderPhenology(today, note = "") {
  $("phenology-now").innerHTML =
    `当前物候：<b>${today.pheno.name}</b>（${today.pheno.note}）｜园块：<b>${esc(currentSite.name)}</b>｜数据日期：<b>${today.date}${today.forecast ? "（预报）" : ""}</b>` +
    (note ? `<br><span class="err">⚠️ ${esc(note)}</span>` : "");
}

// "无等级"有三种含义，对用户必须分开说，且要说人话（评审 A-Q10/C-Q6：工程术语与"没旱情"同样误导）
const NOT_APPLICABLE = { cold: ["swell", "ripe", "dormant"], freeze: ["bud", "bloom", "young", "swell"] };
function idleLabel(id, today) {
  const dq = today.dq || {};
  if (id === "drought") {
    if (dq.windowDays < 30 || dq.windowBad > 0) return "今天判断不了旱情：近 30 天数据不完整";
    if (!dq.etOk) return "今天判断不了旱情：缺少蒸散数据";
    if (today.pheno.id === "dormant") return "冬季不按降雨量判旱情；蒸散亏缺（30 天 ET0−降水）未达休眠期 p90 线——该口径在冬季最高只出「轻」";
  }
  if ((NOT_APPLICABLE[id] || []).includes(today.pheno.id)) return "这个物候期不涉及该风险";
  return "今日无该风险触发";
}

function renderRisks(today) {
  $("risk-now").innerHTML = Object.keys(RISK_NAME).map(id => {
    const r = today.risks[id];
    const lv = r ? r.level : "无";
    return `<div class="risk-item lv-${lv === "无" ? "" : lv}">
      <div class="name">${RISK_NAME[id]}</div>
      <div class="level">${lv}</div>
      <div class="why">${r ? esc(r.why) : esc(idleLabel(id, today))}</div>
    </div>`;
  }).join("");
}

function renderTimeline(all) {
  const recent = all.slice(-97);
  $("timeline").innerHTML = recent.map((d, i) => {
    const w = worstRisk(d.risks);
    const color = w ? LEVEL_COLOR[w.level] : LEVEL_COLOR["无"];
    const tip = `${d.date}${d.forecast ? "（预报）" : ""}｜${d.pheno.name}` +
      (w ? `｜${RISK_SHORT[w.id]}·${w.level}` : "｜无风险") +
      `｜最高 ${d.tmax ?? "—"}℃ / 最低 ${d.tmin ?? "—"}℃ / 降水 ${d.pr ?? "—"}mm`;
    const mark = (i === 0 || d.date.endsWith("-01")) ? "月分界" : "";
    return `<div class="day ${mark}" style="background:${color}" data-tip="${esc(tip)}"></div>`;
  }).join("");
}

// 外部性动作与来源措辞（评审 C-Q6/C-Q8）：页面不替用户做合规判断，含外部性的动作一律前置标记；
// 规则来源只是公开技术文件的摘录整理，不能读成官方背书。
const CONFIRM_TAG = "【需当地确认后才可执行】";
const FRUIT_ASK_TAG = "【先在本页记录是否仍挂果，再决定是否执行】";
// 一条动作可能同时挂两个标签（需当地确认 + 需先记录挂果），四条出口都按同一规则拼标签
const waitTagOf = (x) => (x.waitTags || []).map((t) => (t === "fruit" ? FRUIT_ASK_TAG : CONFIRM_TAG)).join("");
const SRC_NOTE = "（本项目按公开技术文件自行整理的规则摘录，与发文单位无关联，未经当地核验）";
const adviceText = (it) => { const a = adviceOf(it); return (a.confirm ? CONFIRM_TAG : "") + a.t; };

// 事后一致性核对（评审 C-Q10）：模型写的等级词与带单位数值必须能在上下文里找到出处。
// 提示词约束不可验证，这一层可验证：抓到就在答案后标注，不靠"请以看板为准"一句兜底。
function llmCheck(text) {
  // 白名单只取"气象与阈值字段"的数字：整坨上下文里混着日期、条目数与"24–48 小时"这类时效串，
  // 用它们当白名单会让凭空出现的 48℃/24mm 全部通过（评审 A-Q2 实测 8 条越权全放过）
  let ctx;
  try { ctx = JSON.parse(buildContext()); }
  catch (e) { return ["无法核对：上下文构建失败（" + e.message + "）——按不放行处理"]; }
  const tempRain = new Set(), misc = new Set();
  const add = (set, v) => {
    const n = parseFloat(v);
    if (Number.isNaN(n)) return;
    [n, Math.round(n), Math.round(n * 10) / 10].forEach((x) => set.add(String(x)));
  };
  const addBoth = (v) => { add(tempRain, v); add(misc, v); };
  const walk = (o, key) => {
    if (o == null) return;
    if (typeof o === "number") { if (/tmax|tmin|pr|rain30|def30|轻|中|重/.test(key || "")) addBoth(o); return; }
    if (typeof o !== "object") return;
    Object.entries(o).forEach(([k, v]) => walk(v, k));
  };
  walk({ today: ctx.today, recent7: ctx.recent7, forecast7: ctx.forecast7 }, "tmax");
  // 触发依据里印出的数字就是权威读数，一并放行
  Object.values(ctx.today.risks).forEach((v) => v &&
    (String(v.why || "").match(/-?\d+(?:\.\d+)?/g) || []).forEach((x) => addBoth(x)));
  (JSON.stringify(ctx.today).match(/-?\d+(?:\.\d+)?(?=℃|mm)/g) || []).forEach(addBoth);
  Object.values(DEF_THRESH || {}).forEach((row) => Object.values(row).forEach(addBoth));
  // 本工具自己印出去的数字（建议串里的"10cm""固定 5 株""3–5 天""24–48 小时"）只放行给
  // **非温度/雨量**单位：模型原样引用建议库不该被判越权（评审 A-R8⑥），但凭空的新温度/新雨量
  // 仍然只认看板与预报字段，否则 48℃、24mm 这类编造读数会被时效串顺手放行。
  const daySet = new Set();   // 日期里的"几号/几月"，只供 X 日／X 月 引用
  // 模型被要求"必须引用数据日期"，所以日历数字要能为 日/月 两个单位放行；但只在 日/月 上放行，
  // 用量单位（升/次/倍/%）仍然进不去（评审 A-R13⑤③：全剥会误杀合规日期引用，不剥则放行凭空用量）
  const collectDates = (o) => {
    if (typeof o === "string") {
      (o.match(/\d{4}-(\d{2})-(\d{2})/g) || []).forEach((s) => {
        const p = s.split("-");
        add(daySet, String(parseInt(p[1], 10)));
        add(daySet, String(parseInt(p[2], 10)));
      });
      return;
    }
    if (o && typeof o === "object") Object.values(o).forEach(collectDates);
  };
  collectDates({ date: ctx.today.date, pheno: ctx.today.pheno, recent7: ctx.recent7, forecast7: ctx.forecast7 });
  const addStrings = (o, key, bucket) => {
    // 日期不进白名单（评审 C-R12⑤、A-R13⑨）：不光滤掉 key 为 date 的字段——时效钟与建议串里
    // 也内嵌 ISO 日期（"下次降温在 2026-12-08"），那些 2026／12／08 一样是"串里的日期"，
    // 不剥掉就等于把年份和日子重新塞回白名单，凭空编的"每株灌 2026 升"照样放行。
    if (typeof o === "string") {
      if (key === "date") return;
      const clean = o.replace(/\d{4}-\d{2}-\d{2}/g, " ");
      (clean.match(/-?\d+(?:\.\d+)?/g) || []).forEach((x) => add(bucket, x)); return;
    }
    if (o && typeof o === "object") Object.entries(o).forEach(([k, v]) => addStrings(v, k, bucket));
  };
  // 白名单按**单位**分桶（评审 A-R13②、R14②：misc 原来单位盲——凡上下文里出现过的整数都成了
  // 合法用量，时效串的"还有 4 天/3–5 天"、建议串的"12–1 月"、甚至我在 verify 串里写的"约 9km"，
  // 都把 4/9/12 变成"每株灌 9 升"的通行证）。adviceNums 只收**建议条目本身**的数字（浇多少、盖多厚、
  // 标记几株），用量类单位认它；misc 收时效/依据/门控说明里的数字，时间类单位认它。
  const adviceNums = new Set();
  (ctx.actions || []).forEach((a) => ["executable", "pendingConfirm", "notApplicable"]
    .forEach((k) => addStrings(a[k] || [], "x", adviceNums)));
  if (ctx.blockNotice) addStrings(ctx.blockNotice, "x", adviceNums);
  addStrings({ actions: ctx.actions, risks: ctx.today.risks, pheno: ctx.today.pheno, gate: ctx.gate }, "x", misc);
  const DOSE_UNITS = new Set(["升", "L", "倍", "%", "公斤", "kg", "克", "吨", "cm", "厘米", "次"]);
  const problems = [];
  const levels = new Set(Object.values(ctx.today.risks).map((r) => r && r.level).filter(Boolean));
  for (const lv of ["重", "中", "轻", "关注"]) {
    const re = new RegExp("(?<![不无未非])" + lv + "级");
    if (re.test(text || "") && !levels.has(lv)) problems.push(`看板没有「${lv}」级，回答却这么写`);
  }
  const UNITS = "℃|°C|度|毫米|mm|厘米|cm|米|升|L|%|倍|小时|分钟|天|日|次|公斤|kg|克|吨";
  // 温度与水量是"预测类"数字，只认看板/预报/阈值；换中文单位（毫米、度）不许绕过（评审 A-R9④）
  const MEASURE_UNITS = new Set(["℃", "°C", "度", "mm", "毫米"]);
  for (const mt of (text || "").matchAll(new RegExp("(-?\\d+(?:\\.\\d+)?)\\s*(" + UNITS + ")", "g"))) {
    const n = parseFloat(mt[1]);
    const measure = MEASURE_UNITS.has(mt[2]);
    const set = measure ? tempRain : (DOSE_UNITS.has(mt[2]) ? adviceNums : misc);
    // 温度/雨量只认原值与一位小数（四舍五入到整数会让 45.6℃ 靠阈值 45.9℃ 蒙过去，评审 A-R9④）
    const forms = measure ? [String(n), String(Math.round(n * 10) / 10)]
                          : [String(n), String(Math.round(n)), String(Math.round(n * 10) / 10)];
    const inSet = (f) => set.has(f) || (!measure && (mt[2] === "日" || mt[2] === "月") && daySet.has(f));
    if (!forms.some(inSet))
      problems.push(`「${mt[0].trim()}」不是${mt[2] === "℃" || mt[2] === "mm" ? "看板/预报/阈值" : "上下文"}里的数（白名单外）`);
  }
  // 越权**动作**核对（评审 C-R12②：此前只核等级词与数字，模型把 gate 里的熏烟写成"今晚就熏"
  // 时不含数字也不含新等级，零命中）：门控清单里的关键词出现在回答里、且全篇没有一句
  // "先确认/咨询农技员"，就点名为越权。与数字核对同为标注，不拦截。
  const GATED_KEYS = ["熏烟", "覆膜", "地膜", "喷药", "喷施", "保水剂", "叶面肥", "速效肥", "追施", "灌透水", "疏除", "焚烧", "穴贮肥水", "拉水浇根"];
  const gated = new Set();
  (((ctx.gate || {})["需先经当地确认才可视作可执行"]) || []).forEach(
    (x) => GATED_KEYS.forEach((k) => { if (String(x).includes(k)) gated.add(k); }));
  // hedge 必须**同一句内**才算数，且否定式不算（评审 A-R13⑩：全篇判一句"不必咨询农技员"
  // 就把整条越权动作豁免掉，形同装饰）
  const NEG = /不必|不用|无需|不需要|别|不要|并非|无法/;
  const hitKw = new Set();
  String(text || "").split(/[。！？；\n]/).forEach((s) => {
    if (!s) return;
    gated.forEach((k) => {
      if (!s.includes(k) || hitKw.has(k)) return;
      const bare = s.replace(/【[^】]*确认[^】]*】/g, "");   // 原样引用自家"需当地确认"标签不算 hedge（评审 A-R13④）
      if (!(/确认|咨询|农技|主管部门|不适用/.test(bare) && !NEG.test(bare))) {
        hitKw.add(k);
        problems.push(`「${k}」在本园块属未经当地确认的动作，回答却把它当可直接执行的建议给出（同句内没有"先确认／咨询"限定）`);
      }
    });
  });
  // 已知边界：中文数字（"四十一点七度"）、无单位用量（"每株 50 升"里的"50 升"能抓，"两遍"抓不到）
  // 与"数字对但归属错"（把第 5 天预报说成今天）仍拦不住——见 docs/02 §7.2 的诚实标注
  return problems;
}

// 园块灌溉条件：引擎唯一的田间状态输入（评审 C-Q6：判级不看有没有水，就把"浇透根系分布层"
// 排成第一条可执行动作，无水山坡园根本做不到）。默认"未记录"：既不升格也不丢弃，而是标注需先确认。
// 按园块（名称+坐标）分别记录，切园块不会带着上一个园块的灌溉条件（评审 C-Q2）
const condKey = (f) => f + "::" + currentSite.name + "@" + currentSite.lat + "," + currentSite.lon;
function condRec(field) {
  try { return JSON.parse(localStorage.getItem(condKey(field)) || "null"); } catch (e) { return null; }
}
const condOf = (field) => (condRec(field) || {}).v || "unknown";
function setCond(field, v) { localStorage.setItem(condKey(field), JSON.stringify({ v, at: todayStr() })); }
// 材料里写的"撤回方式"必须有一个能点的按钮，而不是一句话（评审 B-Q：无实现路径的承诺不算承诺）：
// 园块条件与确认清单都只存本机，这里给出显式清除入口，返回清掉的条数以便界面回显。
function clearLocalRecords() {
  const kill = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && (k.startsWith("irr_cond::") || k.startsWith("fruit_cond::") || k === "confirmed_actions")) kill.push(k);
  }
  kill.forEach((k) => localStorage.removeItem(k));
  Object.keys(typedBy).forEach((k) => delete typedBy[k]);   // 不重署名就能重新勾选的那条后门
  return kill.length;
}
// 绑定两个"清除本机"按钮。定义放在初始化段之前，烟测能显式调用它并真的点一次——
// 上一轮材料写的"撤回方式"只有按钮没有绑定，点开什么都不发生（评审 A-R8⑧）
// Key 的两条路径：存本机（localStorage，重启浏览器仍在）或"仅本次会话"（只活在这个变量里，
// 刷新即失效）。共用手机/值班机场景下 localStorage 里的明文 Key 任何能打开这台机器浏览器的人
// 都能取用，所以必须给出不写入本机的选项，而不是只写一句"请自行保管"（评审 C-R12⑦）。
let sessionKey = "";
const keyOf = () => sessionKey || localStorage.getItem("deepseek_key") || "";
function wireLocalControls() {
  const st = () => $("key-status");
  const ck = $("clear-key");
  if (ck) ck.onclick = () => {
    localStorage.removeItem("deepseek_key");
    sessionKey = "";   // 清除必须连"仅本次会话"那把一起清，否则按钮只删了一半（评审 C-R12⑦）
    const inp = $("api-key");
    if (inp) inp.value = "";
    if (st()) st().textContent = "已清除（本机与本次会话都不再保存 Key）";
  };
  const once = $("use-key-once");
  if (once) once.onclick = () => {
    const inp = $("api-key");
    sessionKey = (inp && inp.value || "").trim();
    if (st()) st().textContent = sessionKey
      ? "仅本次会话使用，未写入本机（刷新页面即失效）"
      : "输入框里没有可用的 Key";
  };
  const cl = $("clear-local");
  if (cl) cl.onclick = () => {
    const n = clearLocalRecords();
    if (st()) st().textContent = `已清除 ${n} 条本机记录（园块条件／确认清单／本次会话的确认人输入）；`
      + (localStorage.getItem("deepseek_key") ? "API Key 仍在，如需清除请点左侧按钮。" : "API Key 本来就没存。");
    renderIrr();
    if (window.__today) renderAdvice(window.__today);
  };
}
const irrCond = () => condOf("irr_cond");
function setIrrCond(v) { setCond("irr_cond", v); }
const IRR_LABEL = { yes: "有灌溉", no: "无灌溉", unknown: "未记录" };
// 挂果记录（评审 C-Q7）：晚熟杂柑 12–1 月仍挂果，而本表按蜜柑 10 月集中采收建模，
// "树上有没有果"直接决定冻害当晚要不要抢收/覆盖——它必须是输入，不能是一句"若仍挂果"的注释。
const FRUIT_STALE_DAYS = 90;   // 一个物候周期内有效：12 月记的"仍挂果"到 6 月不能继续门控抢收
function fruitCond() {
  const r = condRec("fruit_cond");
  const v = (r || {}).v || "unknown";
  if (v === "unknown" || !r || !r.at) return v;
  const age = Math.round((parseDay(todayStr()) - parseDay(r.at)) / 86400000);
  return age > FRUIT_STALE_DAYS ? "unknown" : v;
}
function setFruitCond(v) { setCond("fruit_cond", v); }
const FRUIT_LABEL = { yes: "树上仍挂果", no: "已采收完", unknown: "未记录" };

// fail-closed（评审 A-Q7/C-Q5）：含外部性的动作默认**不进入可执行建议**，只出现在"待当地确认清单"。
// 确认必须留下"谁确认的＋日期"，否则一个自己能勾上的复选框等于自我证明；记录只存本机
// （无后端、不同步），所以它是防误发的提醒，不是合规凭证——可转移的凭证是导出报告上的签字栏。
function confirmedActions() {
  try { return JSON.parse(localStorage.getItem("confirmed_actions") || "[]"); } catch (e) { return []; }
}
const isConfirmed = (t) => confirmedActions().some((r) => r && r.t === t);
const confirmMeta = (t) => confirmedActions().find((r) => r && r.t === t);
const typedBy = {};   // 本次会话里输入的"确认人／依据"，随清除动作一起抹掉
function setConfirmed(text, on, who) {
  const name = String(who == null ? (typedBy[text] || "") : who).trim();
  const recs = confirmedActions().filter((r) => r && r.t !== text);
  if (on && name) recs.push({ t: text, by: name, at: todayStr() });
  localStorage.setItem("confirmed_actions", JSON.stringify(recs));
  return recs;
}
const adviceItem = (a) => {
  if (!a.confirm) return `<li>${esc(a.t)}</li>`;
  const m = confirmMeta(a.t);
  return `<li>【已经当地确认：${esc(m ? m.by + "／" + m.at : "未记录")}】${esc(a.t)}</li>`;
};
const confirmControl = (t) => `<label class="confirm-box"><input type="checkbox" data-confirm-text="${esc(t)}">已获当地确认</label>` +
  `<input type="text" data-confirm-by="${esc(t)}" placeholder="确认人／单位＋依据（必填）" size="26">`;
function onAdviceChange(ev) {
  const el = ev && ev.target;
  if (!el || !el.dataset || el.dataset.confirmText == null) return;
  const text = el.dataset.confirmText;
  if (el.checked && !String(typedBy[text] || "").trim()) {
    el.checked = false;   // 没写确认人就升不了格：不接受无署名的自我确认
    return;
  }
  setConfirmed(text, !!el.checked, el.checked ? undefined : "");
  if (window.__today) renderAdvice(window.__today);
}
function onAdviceInput(ev) {
  const el = ev && ev.target;
  if (!el || !el.dataset || el.dataset.confirmBy == null) return;
  typedBy[el.dataset.confirmBy] = el.value;
}

// 三条出口（看板、导出报告、规则问答/LLM 上下文）共用同一个取库函数：
// 此前只有面板做了灌溉过滤，无 Key 时的规则问答照样把"立即灌溉补墒"推给无水园块（评审 A-Q1）
// 三条出口共用的文本行：可执行的直说，待确认的带标记，被园块条件排除的显式标"不适用"
// 同一等级连日不降档时，页面不能天天把同一条"24–48 小时内完成"当新指令重发（评审 A-R12②：
// 留出段 2025 年有连续 36 天的花期"重"级，第 20 天的正确动作是核查上一条做没做、看墒情变化，
// 不是再紧急一次）。等级必须改变行动，否则抬高一级只是换颜色（docs/02 §4 的自定标准）。
function levelStreak(riskId, level) {
  if (!level) return 0;
  const d0 = (window.__today || {}).date || "";
  const rows = (window.__recent || []).filter((x) => x && x.date && (!d0 || x.date <= d0))
    .sort((a, b) => a.date.localeCompare(b.date));
  let n = 0, prev = null;
  for (let i = rows.length - 1; i >= 0; i--) {
    const lv = rows[i].risks && rows[i].risks[riskId] ? rows[i].risks[riskId].level : null;
    if (lv !== level) break;
    const cur = +parseDay(rows[i].date);   // parseDay 返回 Date，必须取时间戳再比差值
    if (prev !== null && cur !== prev - 86400000) break;   // 断日即止
    prev = cur;
    n += 1;
  }
  return n;
}
const streakTail = (riskId, level) => {
  const n = levelStreak(riskId, level);
  if (n < 2) return "";
  return `　本等级已连续 ${n} 天：第 2 天起不重复下达同一条紧急指令——先核查上一条是否已执行、`
    + "记录墒情与树势变化，已执行就转入维持动作（保墒、覆盖、续水）；只有仍未执行才继续按原时效处理。";
};

function adviceLines(riskId, phenoId, level) {
  const sel = adviceFor(riskId, phenoId, level);
  return sel.go.map((x) => x.t)
    .concat(sel.wait.map((x) => waitTagOf(x) + x.t))
    .concat(sel.na.map((x) => `【本园块记录为${x.naWhy}，不适用】${x.t}`))
    .concat(sel.verify ? [sel.verify] : []);
}

// 冻害的执行时效要讲"降温前 3–5 天灌透水"，可页面此前不告诉用户降温落在哪一天，
// 农户无法判断自己是在窗口内还是已经太晚（评审 C-Q7）。这里把天数算出来：
// 直接取引擎自己对未来预报日判出的 freeze 等级，不另设温度阈值。
// 只用 8 天内的读数：capFarForecast 把 8 天以外的等级一律压成"关注"并写明"不构成动作依据"，
// 钟不能一边说那批数不可用于行动、一边拿它定灌水日（评审 C-R8②）。
const FREEZE_LEAD_MIN = 3, FREEZE_LEAD_MAX = 5;
function shiftDay(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(y, m - 1, d + n);
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
}
function nextCooling(todayDate) {
  const rows = (window.__recent || []).filter(x => x.forecast && x.date > todayDate);
  if (!rows.length) return { reason: "noForecast" };
  const leadOf = (x) => Math.round((parseDay(x.date) - parseDay(todayDate)) / 86400000);
  const hit = rows.filter(x => leadOf(x) < FAR_LEAD_DAYS && x.risks && x.risks.freeze)
    .map(x => ({ date: x.date, level: (x.risks.freeze || {}).level || "" }))
    .sort((a, b) => a.date.localeCompare(b.date));
  // 钟指向的那一天自己是什么等级，就按那天的等级说话（硬编码"中"会让"今天根本没出级"
  // 拿到比真实"轻"级读数更强的指令，评审 A-R12①）
  if (hit.length) return { reason: "hit", date: hit[0].date, level: hit[0].level };
  const far = rows.filter(x => x.risks && x.risks.freeze).map(x => x.date).sort();
  return { reason: far.length ? "onlyFar" : "none", farDate: far.length ? far[0] : null };
}
function freezeClock(level, todayDate) {
  const d0 = todayDate || (window.__today || {}).date || todayStr();
  const r = nextCooling(d0);
  if (r.reason === "noForecast")
    return "冻害时效钟：预报序列取不到（接口失败或没有未来数据），现在算不出降温日——请按当地霜期与官方预警安排防护，"
         + "别把「取不到读数」当成「这几天没有降温」。";
  if (r.reason === "onlyFar")
    return `冻害时效钟：${FAR_LEAD_DAYS} 天内的可靠预报里没有降温日；第 ${FAR_LEAD_DAYS + 1} 天以后（示意 ${r.farDate}）的读数已被封顶为"关注"，`
         + "不作灌水依据——临近时重新打开本页复核。";
  if (r.reason === "none")
    return `冻害时效钟：本格点当前预报的 ${FAR_LEAD_DAYS} 天内没有触发冻害的日（随预报更新，寒潮临近前请复查）。`;
  const lead = Math.round((parseDay(r.date) - parseDay(d0)) / 86400000);
  const noIrr = irrCond() === "no";   // 无水园块拿不到灌水指令，只能拿物理防护指令（评审 A-R12①）
  if ((r.level || level) === "轻")
    return `冻害时效钟：下次降温在 ${r.date}（还有 ${lead} 天）。当前只有"轻"级（≤-3℃），以观察墒情与树势为主，`
         + "不据此安排灌透水；要防护就用覆膜/涂白这类不动用水源的动作。";
  if (lead > FREEZE_LEAD_MAX)
    return `冻害时效钟：下次降温在 ${r.date}（还有 ${lead} 天）。`
         + (noIrr ? "本园块记录为无灌溉：冻前灌水这条不适用，改用覆盖、培土、涂白。"
                  : `灌透水安排在 ${shiftDay(r.date, -FREEZE_LEAD_MAX)}～${shiftDay(r.date, -FREEZE_LEAD_MIN)} 之间，不要提得太早。`);
  if (lead >= FREEZE_LEAD_MIN)
    return `冻害时效钟：下次降温在 ${r.date}（还有 ${lead} 天），`
         + (noIrr ? "本园块记录为无灌溉：不排灌水，改以覆膜/培土为主。"
                  : "已到 3–5 天窗口——要灌透水就今明两天做；无水源或劳力不足的园块改以覆膜/培土为主。");
  return `冻害时效钟：距降温只有 ${lead} 天（${r.date}），已过灌水窗口：不要临冻灌透（根区结冰风险），改用覆盖、涂白、培土等物理防护。`;
}
// 前置钟（今天还没出冻害级、但 8 天内要冻）在休眠期与采收期才有意义；
// 四条出口共用这一串，导出件与问答不许比看板少一句（评审 A-R12①）。
function preClockText(today) {
  if (!today || !["dormant", "ripe"].includes(today.pheno.id)) return "";
  return freezeClock(null, today.date);
}

// 记录为"仍挂果"但当前不是休眠期时，现行冻害阈值口径并不适用（判级按蜜柑采收后的休眠树建模，
// 带果树抗冻更差）。挂果与否不参与判级（没有本地挂果联校数据），但必须显式告诉用户口径差异（评审 C-R8⑤）。
function fruitNotice(today) {
  if (!today || fruitCond() !== "yes") return "";
  const near = (window.__recent || []).some(x => x.date >= today.date && x.tmin != null && x.tmin <= -3);
  if (!near && !(today.tmin != null && today.tmin <= -3)) return "";
  return "⚠️ 本园块记录为仍挂果，而现行冻害等级按蜜柑采收后的休眠树建模：带果树的抗冻能力更低，−3℃ 级即可伤果。"
       + "挂果状态下的温度—受害关系未做本地联校，因此在等级口径之外，建议按比当前等级更严一档安排覆盖或抢收，"
       + "并把这条差异反馈给农技员核对。";
}

function adviceFor(riskId, phenoId, level) {
  const lib = ADVICE[riskId];
  const items = ((lib.items[phenoId] || lib.items.default) || []).map(adviceOf);
  const naWhy = (x) => (x.irr && irrCond() === "no" ? "无灌溉" : (x.fruit && fruitCond() === "no" ? "已采收完" : null));
  const blocked = (x) => naWhy(x) !== null;
  // 挂果情况"未记录"时，抢收/覆膜保果这类不可逆动作不进可执行段：默认乐观假设等于替用户决定（评审 C-R8⑥）
  const fruitAsk = (x) => x.fruit && fruitCond() === "unknown";
  const timing = timingOf(riskId, level || "中") + (level ? streakTail(riskId, level) : "");
  return {
    go: items.filter((x) => (!x.confirm || isConfirmed(x.t)) && !blocked(x) && !fruitAsk(x)),
    wait: items.filter((x) => !blocked(x) && ((x.confirm && !isConfirmed(x.t)) || fruitAsk(x)))
                .map((x) => ({ ...x,
                    // 一条动作可能同时"有外部性需当地确认"和"取决于挂果记录"：两个标签都得在场，
                    // 只留一个会把 fail-closed 的那半句藏掉（评审 C-R8⑥ 的残余）
                    waitTags: [x.confirm && !isConfirmed(x.t) ? "confirm" : null,
                               fruitAsk(x) ? "fruit" : null].filter(Boolean),
                    waitWhy: fruitAsk(x) && !x.confirm ? "fruit" : "confirm" })),
    na: items.filter(blocked).map((x) => ({ ...x, naWhy: naWhy(x) })),
    timing: riskId === "freeze" && level ? `${timing}　${freezeClock(level)}` : timing,
    // docs/02 承诺"未对表前'重'级动作一律要求现场核实"，这句话此前只活在文档里，
    // 页面/导出/问答/LLM 四处 grep 不到（评审 C-R12④）——按项目自己的纪律，它得是可执行的串而不是措辞。
    verify: level === "重" ? "本格点（约 9km）与本地田块尚未对表：\"重\"级动作执行前先现场核实——看树势、捏土验墒、比对当地官方预警，确认后再投入。" : "",
    src: lib.src,
  };
}

function renderAdvice(today) {
  const PHENO_TIP = {
    dormant: "做好防冻预案检查", bud: "关注倒春寒预报", bloom: "保花为主，留意低温与干旱",
    young: "稳果水肥管理", swell: "膨大期水肥是全年关键", ripe: "安排采收与防寒预案",
  };
  const actives = Object.entries(today.risks).filter(([k, v]) => v && ["重", "中", "轻"].includes(v.level));
  let html = "";
  if (!actives.length) {
    // 农户最需要钟的时点恰恰是"今天还没冻、3–5 天后要冻"：只写"当前无中重度风险"会让时效钟在最该出现时缺席（评审 A-R11①）
    const preClock = preClockText(today)
      ? `<div class="adv-item"><p class="timing">${esc(preClockText(today))}</p>
        <p class="src">这是按当前格点预报给的前置提醒，不是冻害等级——今天仍未达越冬冻害门槛。</p></div>` : "";
    html = `<div class="adv-item"><h4>当前无中重度风险</h4>
      <ul><li>当前物候：${today.pheno.name}——${PHENO_TIP[today.pheno.id]}</li></ul>
      <div class="src">规则来源：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料</div></div>${preClock}`;
  } else {
    html = actives.map(([k, v]) => {
      const lib = ADVICE[k];
      const sel = adviceFor(k, today.pheno.id, v.level);
      const items = (lib.items[today.pheno.id] || lib.items.default).map(adviceOf);
      const irr = irrCond();
      const go = sel.go.map(adviceItem).join("");
      const wait = sel.wait.map((x) => {
        const tag = esc(waitTagOf(x));
        const ctl = x.confirm ? confirmControl(x.t)
          : '<span class="src">请在①区把"本园块挂果情况"选为"树上仍挂果"或"已采收完"，本条会自动改变归属。</span>';
        return `<li>${tag}${esc(x.t)}${ctl}</li>`;
      }).join("");
      const nogo = sel.na.map((x) => `<li>不适用（本园块记录为<b>${x.naWhy}</b>）：${esc(x.t)}</li>`).join("");
      const irrNote = irr === "unknown" && items.some(x => x.irr)
        ? `<p class="src">本页未记录本园块是否有灌溉条件——上面带"需灌溉条件"标注的动作请先自行确认能否执行。</p>` : "";
      const fruitNote = fruitCond() === "unknown" && items.some(x => x.fruit)
        ? `<p class="src">未记录本园块当前是否仍挂果：上面涉及抢收/覆膜保果的动作是按"仍挂果"给出的，若已采收完，请在园块条件里改选"已采收完"，该条会自动标为不适用。</p>` : "";
      return `<div class="adv-item"><h4>【${RISK_NAME[k]}·${v.level}】${esc(v.why)}</h4>
        <p class="timing">${esc(sel.timing)}</p>
        ${sel.verify ? `<p class="src">${esc(sel.verify)}</p>` : ""}
        <ul>${go || "<li>本风险下暂无可自动执行的动作（需当地确认或需灌溉条件，见下）</li>"}</ul>
        ${nogo ? `<h5>按本园块记录不适用的动作</h5><ul class="pending">${nogo}</ul>` : ""}
        ${irrNote}${fruitNote}
        ${wait ? `<h5>待当地确认清单（未确认前不作为可执行建议）</h5><ul class="pending">${wait}</ul>` : ""}
        <div class="src">规则来源：${esc(lib.src)}${SRC_NOTE}</div></div>`;
    }).join("");
    html += `<div class="src">"待当地确认"针对库区农事焚烧、地膜残留、灌溉退水与投入品使用等外部性动作；本工具不替你判断合规性，确认后由农技员或农户在本机登记即可升格为可执行建议。</div>`;
  }
  $("advice").innerHTML = html + (fruitNotice(today)
    ? `<div class="adv-item"><p class="timing">${esc(fruitNotice(today))}</p></div>` : "");
}

// 远期预报不确定性封顶：ECMWF 第 8 天起逐日温度误差 ±2-3℃（阈值间隔才 2℃），
// FAR_LEAD_DAYS 天外的等级一律压为"关注"并改写依据文本（评审意见；A-Q2 要求这条产品级
// 安全装置必须被测试钉住——它此前只在 load() 里内联，任何测试都抽不到它）。
const FAR_LEAD_DAYS = 8;
function capFarForecast(all, todayStr) {
  for (const d of all) {
    if (!d.forecast) continue;
    const lead = (parseDay(d.date) - parseDay(todayStr)) / 86400000;
    if (lead < FAR_LEAD_DAYS) continue;
    for (const k of Object.keys(d.risks)) {
      if (!d.risks[k]) continue;
      d.risks[k] = {
        level: "关注",
        why: `${RISK_NAME[k]}模式值：最高 ${d.tmax ?? "—"}℃ / 最低 ${d.tmin ?? "—"}℃（预报时效 ${Math.round(lead)} 天，误差可达 ±2-3℃，仅作过程示意，不构成动作依据）`
      };
    }
  }
  return all;
}

// ---------- 历史回放 ----------
async function runReplay(kind) {
  const box = $("replay-result");
  box.innerHTML = `<p class="loading">正在获取历史数据（${currentSite.name}）…</p>`;
  const { lat, lon } = currentSite;
  const ranges = {
    "2022-heat":   ["2022-06-01", "2022-09-30"],
    "2025-autumn": ["2025-08-21", "2025-10-31"],
    "2021-winter": ["2021-11-25", "2022-01-20"],
  };
  const [s, e] = ranges[kind];
  try {
    const days = await fetchArchive(lat, lon, s, e);
    const assessed = assess(days);
    let stats = "";
    if (kind === "2022-heat") {
      const heatDays = assessed.filter(d => d.tmax >= 35).length;
      const severe = assessed.filter(d => d.risks.heat && d.risks.heat.level === "重").length;
      const peak = assessed.reduce((a, b) => (b.tmax > a.tmax ? b : a));
      const firstSevere = assessed.find(d => d.risks.heat && d.risks.heat.level === "重");
      const lead = firstSevere ? Math.round((parseDay(peak.date) - parseDay(firstSevere.date)) / 86400000) : null;
      stats = `<span class="replay-stat">≥35℃ 高温日 <b>${heatDays}</b> 天</span>
        <span class="replay-stat">热害"重"级日 <b>${severe}</b> 天</span>
        <span class="replay-stat">极端最高温 <b>${peak.tmax}℃</b>（${peak.date}）</span>` +
        (lead != null ? `<span class="replay-stat">首个"重"级提示比峰值提前 <b>${lead}</b> 天</span>` : "");
    } else if (kind === "2025-autumn") {
      const wetDays = assessed.filter(d => (d.pr || 0) >= 10).length;
      const total = assessed.reduce((s2, d) => s2 + (d.pr || 0), 0);
      stats = `<span class="replay-stat">区间累计降水 <b>${total.toFixed(0)}</b> mm</span>
        <span class="replay-stat">≥10mm 降水日 <b>${wetDays}</b> 天</span>
        <span class="replay-stat">秋汛连阴雨影响采收与树体</span>
        <p class="hint">⚠️ 说明：引擎首发版四类风险不含雨涝/连阴雨指标，本场景展示的是降水统计与事件回顾，不是引擎风险等级。连阴雨指标（连续 ≥3 天日雨 ≥10mm 等）已列入迭代路线。</p>`;
    } else {
      const coldest = assessed.reduce((a, b) => (b.tmin < a.tmin ? b : a));
      const freezeDays = assessed.filter(d => d.risks.freeze).length;
      const heavy = assessed.filter(d => d.risks.freeze && ["重", "中"].includes(d.risks.freeze.level)).length;
      stats = `<span class="replay-stat">极端最低温 <b>${coldest.tmin}℃</b>（${coldest.date}）</span>
        <span class="replay-stat">冻害风险日 <b>${freezeDays}</b> 天（中重 <b>${heavy}</b> 天）</span>`;
    }
    const worst = assessed.map(d => ({ d, w: worstRisk(d.risks) })).filter(x => x.w);
    // 优先展示"重"级事件（按日期），无重级时展示末尾事件
    const topList = worst.filter(x => x.w.level === "重").length >= 3
      ? worst.filter(x => x.w.level === "重").slice(0, 5)
      : worst.slice(-5);
    const top = topList.map(x => `<li>${x.d.date}｜${RISK_NAME[x.w.id]}·${x.w.level}：${esc(x.w.why)}</li>`).join("");
    box.innerHTML = `<div>${stats}</div><p class="hint">若当时使用本工具，主要提示为：</p><ul style="padding-left:20px">${top}</ul>
      <p class="hint">回测说明：风险等级由当日与前 30 天数据按规则引擎复算，供检验提示时效，非事后拟合。"重"级由单日阈值还是连续日数升级触发，见 tools/backtest.py 输出的逐项归因。</p>`;
  } catch (err) {
    box.innerHTML = `<p class="answer err">回放失败：${esc(err.message)}</p>`;
  }
}

// ---------- 问答 ----------
function buildContext() {
  const t = window.__today;
  const all = window.__recent || [];
  const d7 = (x) => ({ date: x.date, tmax: x.tmax, tmin: x.tmin, pr: x.pr });
  return JSON.stringify({
    site: currentSite.name,
    today: { date: t.date, pheno: t.pheno.name, tmax: t.tmax, tmin: t.tmin,
      risks: Object.fromEntries(Object.entries(t.risks).map(([k, v]) => [k, v ? { level: v.level, why: v.why } : null])) },
    recent7: all.filter(x => !x.forecast).slice(-7).map(d7),
    // 未来 7 天预报与引擎等级一并给出（评审 A-Q4：原上下文只有历史数据，
    // 果农问"接下来几天怎么办"时模型只能凭空生成）
    forecast7: all.filter(x => x.forecast && x.date >= t.date).slice(0, 7)
      .map(x => ({ ...d7(x), risks: Object.fromEntries(Object.entries(x.risks).map(([k, v]) => [k, v ? { level: v.level, why: v.why } : null])) })),
    // 可执行动作全文与执行时效一并进上下文：模型不再自己编"灌多少、灌几次"（评审 C-Q10）
    actions: Object.entries(t.risks)
      .filter(([k, v]) => v && ["重", "中", "轻"].includes(v.level))
      .map(([k, v]) => {
        const sel = adviceFor(k, t.pheno.id, v.level);
        return { risk: RISK_NAME[k], level: v.level, timing: sel.timing,
          // 重级的"先现场核实"限定词必须一起进模型上下文，否则三处到人、第四处（模型）恰恰到不了
          // （评审 A-R13⑥：此前 actions 只有 risk/level/timing/executable/pendingConfirm/notApplicable）
          verify: sel.verify || "",
          executable: sel.go.map((x) => x.t),
          pendingConfirm: sel.wait.map((x) => waitTagOf(x) + x.t),
          notApplicable: sel.na.map((x) => `${x.t}（本园块记录为${x.naWhy}，不适用）`) };
      }),
    blockNotice: fruitNotice(t) || null,
    rule: "风险等级、数值与农事动作一律引用本上下文（actions 里没写的用量、次数、药剂不要自己编，"
        + "不确定就说不确定并让用户问农技员）；不得自行判定新等级、不得预测新数值。",
    // 建议库的分级门控必须一起给模型，否则模型会绕过它直接教农户熏烟/覆膜（评审 C-Q5③）
    gate: {
      需先经当地确认才可视作可执行: pendingConfirmList(),
      说明: "上述动作在库区涉及焚烧管控、残膜或灌溉退水等外部性，未经当地确认前不得作为可执行建议输出；可提示用户先咨询农技员或主管部门。",
    },
  });
}

// 当日触发风险对应的、尚未确认的外部性动作清单（引擎与建议库同源）
function pendingConfirmList() {
  const t = window.__today;
  if (!t) return [];
  const out = [];
  for (const [k, v] of Object.entries(t.risks)) {
    if (!v || !["重", "中", "轻"].includes(v.level)) continue;
    const lib = ADVICE[k];
    if (!lib) continue;
    for (const it of (lib.items[t.pheno.id] || lib.items.default)) {
      const a = adviceOf(it);
      if (a.confirm && !isConfirmed(a.t)) out.push(a.t);
    }
  }
  return out;
}

function ruleAnswer(q) {
  const t = window.__today;
  const a = [];
  if (/灌|浇|水|旱/.test(q)) {
    const d = t.risks.drought, h = t.risks.heat;
    a.push(d ? `干旱风险为【${d.level}】：${d.why}。` : idleLabel("drought", t), h ? `高温风险为【${h.level}】。` : "");
    // 旱情没判级就不许按"中"级取库（评审 C-R8⑩：同页一处写"今天判断不了旱情"，
    // 另一处却把未知当已知，直接输出"立即灌溉补墒、浇透根系分布层"）
    a.push(d ? adviceLines("drought", t.pheno.id, d.level)
             : "（旱情当前未判级，不据此给灌水动作——先解决上一条里说的原因：数据不完整、缺蒸散数据，或该期口径本身不出级。）");
  } else if (/冻|寒|低温|冷/.test(q)) {
    // 按实际触发的风险取库（评审 A-Q10：此前低温分支一律取 freeze 库，
    // 花期/幼果低温触发时既取错建议又与同页建议面板给出两套动作）
    const c = t.risks.cold, f = t.risks.freeze;
    if (c) {
      a.push(`低温风险【${c.level}】：${c.why}`);
      a.push(adviceLines("cold", t.pheno.id, c.level));
    } else if (f) {
      a.push(`冻害风险【${f.level}】：${f.why}`);
      a.push(adviceLines("freeze", t.pheno.id, f.level));
      a.push(adviceFor("freeze", t.pheno.id, f.level).timing);   // 时效与"距降温 N 天"的钟也进问答出口
      if (fruitNotice(t)) a.push(fruitNotice(t));
    } else {
      a.push("当前低温/冻害风险未触发。" + (preClockText(t)
        ? " " + preClockText(t) + "（这是前置提醒，不是冻害等级）" : ""));
    }
  } else {
    a.push(`当前处于${t.pheno.name}（${t.pheno.note}）。`);
    const actives = Object.entries(t.risks).filter(([k, v]) => v && v.level !== "无");
    a.push(actives.length ? actives.map(([k, v]) => `${RISK_SHORT[k]}【${v.level}】`).join("；") : "各风险均未触发。");
    a.push("可追问：要不要灌水？花期遇低温怎么办？越冬防冻要做什么？");
  }
  a.push(`——以上由农技规则库生成；数据 ${t.date}，来源 Open-Meteo。`);
  return a.flat().join("\n");
}

async function askLLM(q) {
  const key = keyOf() || null;
  if (!key) return null;
  const sys = `你是丹江口库区柑橘园气候决策助手"丹橘知旱"。只用给定数据与湖北省公开农技规则回答果农问题，给出可执行动作；必须引用数据日期；风险等级与数值只能原样引用上下文，不得自行判定等级或预测新数值；数据不足以判断时明确说明并建议咨询当地农技部门。上下文 gate 中列出的动作尚未获当地确认，不得作为可执行建议输出，只能提示先咨询农技员或主管部门。数据上下文：${buildContext()}`;
  const res = await fetch("https://api.deepseek.com/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: "deepseek-chat", messages: [{ role: "system", content: sys }, { role: "user", content: q }], max_tokens: 500 }),
  });
  if (!res.ok) throw new Error("DeepSeek API HTTP " + res.status);
  const j = await res.json();
  return j.choices[0].message.content;
}

async function onAsk() {
  const q = $("q").value.trim();
  if (!q) return;
  const box = $("a");
  box.innerHTML = `<p class="loading">思考中…</p>`;
  $("ask").disabled = true;
  try {
    let ans = null, mode = "", failNote = "";
    try {
      ans = await askLLM(q);
      if (ans) {
        mode = "（DeepSeek 大模型生成，已结合规则库上下文）";
        // LLM 约束不靠提示词兜底：先做事后一致性核对，再追加统一声明（评审 C-Q10/Q7）
        const issues = llmCheck(ans);
        if (issues.length) ans += "\n【自动核对不过】" + issues.join("；") + "。";
        ans += "\n——风险等级请以页面看板为准，本回答仅为解释；不构成灾害预警。";
      }
    } catch (e) {
      ans = null;
      failNote = e.message.includes("Failed to fetch") || e.message.includes("NetworkError")
        ? "（网络或浏览器跨域策略拦截，大模型层未接通——该失败模式已记录，规模化需服务端代理）"
        : `（大模型层错误：${e.message}）`;
    }
    if (!ans) {
      ans = ruleAnswer(q);
      mode = keyOf()
        ? `（内置规则引擎生成；大模型层未接通${failNote}）`
        : "（内置规则引擎生成；如需个性化解释可在下方设置中接入 DeepSeek Key）";
    }
    box.innerHTML = `<b>问：${esc(q)}</b>\n${esc(ans)}\n<span class="cite">${esc(mode)} 风险等级不构成灾害预警。</span>`;
  } catch (e) {
    box.innerHTML = `<p class="err">出错了：${esc(e.message)}</p>`;
  }
  $("ask").disabled = false;
}

// ---------- 导出 ----------
function downloadFile(name, content, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// 报告正文单独成函数：导出动作只剩一次下载调用，正文本身可被烟测直接断言（评审 C-Q7：
// 此前导出出口从没被执行过，一个未定义变量的引用（items）能一直躺在里面不报错）
function reportHtml(t) {
  const risks = Object.keys(RISK_NAME).map(id => {
    const r = t.risks[id];
    return `<tr><td>${RISK_NAME[id]}</td><td>${r ? r.level : "无"}</td><td>${r ? esc(r.why) : esc(idleLabel(id, t))}</td></tr>`;
  }).join("");
  const actives = Object.entries(t.risks).filter(([k, v]) => v && ["重", "中", "轻"].includes(v.level));
  const adviceHtml = actives.length
    ? actives.map(([k, v]) => {
        const lib = ADVICE[k];
        const sel = adviceFor(k, t.pheno.id, v.level);
        const items = (lib.items[t.pheno.id] || lib.items.default || []).map(adviceOf);
        const go = sel.go.map(adviceItem).join("");
        const wait = sel.wait.map((x) => `<li>${esc(waitTagOf(x))}${esc(x.t)}<br>` +
          (x.confirm ? '<span class="src">确认人签字：__________　日期：__________</span>'
                     : '<span class="src">（本条归属取决于"挂果情况"：请先在页面①区选择"树上仍挂果"或"已采收完"。）</span>') +
          "</li>").join("");
        const nogo = sel.na.map((x) => `<li>不适用（本园块记录为${x.naWhy}）：${esc(x.t)}</li>`).join("");
        return `<h3>${RISK_NAME[k]}（${v.level}）</h3><p class="src">${esc(sel.timing)}</p><ul>${go || "<li>本风险下暂无可自动执行的动作（需当地确认或需灌溉条件，见下）</li>"}</ul>` +
          (nogo ? `<h4>按本园块记录不适用的动作</h4><ul>${nogo}</ul>` : "") +
          (irrCond() === "unknown" && items.some(x => x.irr) ? '<p class="src">本园块未记录是否有灌溉条件：上表中标注"需灌溉条件"的动作请先确认能否执行。</p>' : "") +
          (fruitCond() === "unknown" && items.some(x => x.fruit) ? '<p class="src">本园块未记录是否仍挂果：涉及抢收/覆膜保果的条目按"仍挂果"给出，已采收完则不适用。</p>' : "") +
          (wait ? `<h4>待当地确认清单（未确认前不作为可执行建议）</h4><ul>${wait}</ul>` : "") +
          `<p class="src">规则来源：${esc(lib.src)}${SRC_NOTE}</p>` +
          `<p class="src">${esc(sel.verify)}</p>` +
          `<p class="meta">判级口径：${esc(ruleStamp())}｜园块灌溉条件记录为：${esc(IRR_LABEL[irrCond()] || "未记录")}｜挂果情况记录为：${esc(FRUIT_LABEL[fruitCond()] || "未记录")}</p>`;
      }).join("")
    : `<p>当前无中重度风险。当前物候：${t.pheno.name}（${t.pheno.note}）。</p>`
      + (preClockText(t) ? `<p class="src">${esc(preClockText(t))}（前置提醒，不是冻害等级）</p>` : "");
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>丹橘知旱建议报告 ${t.date}</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:800px;margin:24px auto;color:#1f2d27}h1{color:#0e5233}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px 8px;font-size:14px}.src{color:#6b7a72;font-size:12px}.meta{color:#6b7a72;font-size:13px}</style>
</head><body>
<h1>丹橘知旱 · 柑橘园气候决策建议报告</h1>
<p class="meta">园块：${esc(currentSite.name)}（${esc(currentSite.lat)}, ${esc(currentSite.lon)}）｜数据日期：${t.date}${t.forecast ? "（预报）" : ""}｜生成时间：${new Date().toLocaleString("zh-CN")}</p>
<p class="src">本建议由开源工具生成、未经本地核验；转述给其他农户时请一并说明这一点。</p>
<p class="meta">判级口径：${esc(ruleStamp())}</p>
<p>当前物候：<b>${t.pheno.name}</b>——${t.pheno.note}。当日气象：最高 ${t.tmax}℃ / 最低 ${t.tmin}℃ / 降水 ${t.pr}mm。</p>
<h2>一、四类风险等级</h2>
<table><tr><th>风险</th><th>等级</th><th>触发依据</th></tr>${risks}</table>
<h2>二、建议农事动作</h2>
${adviceHtml}${fruitNotice(t) ? `<p class="src">${esc(fruitNotice(t))}</p>` : ""}
<h2>三、数据与规则来源</h2>
<p>气象数据：Open-Meteo（ERA5-Land 再分析 + ECMWF 预报）；规则：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料。</p>
<p class="meta">本报告的判级口径：${esc(ruleStamp())}。</p>
<p class="src">免责声明：本报告由确定性规则自动生成，用于辅助安排农事，不构成灾害预警，不替代官方气象预警与专业农技指导。</p>
<p class="src">丹橘知旱 · AI4Climate 参赛项目 · 团队 沧浪舟 · ${t.date}</p>
</body></html>`;
  return html;
}

function exportReport() {
  const t = window.__today;
  if (!t) return;
  downloadFile(`丹橘知旱-建议报告-${t.date}.html`, reportHtml(t), "text/html;charset=utf-8");
}

function exportCsv() {
  const all = window.__recent || [];
  if (!all.length) return;
  const rows = [["日期", "物候期", "最高温C", "最低温C", "降水mm", "高温热害", "干旱", "花期低温", "越冬冻害", "干旱说明", "数据类型"]];
  for (const d of all) {
    const lv = (r) => (r ? r.level : "无");
    rows.push([d.date, d.pheno.name, d.tmax, d.tmin, d.pr, lv(d.risks.heat), lv(d.risks.drought),
               lv(d.risks.cold), lv(d.risks.freeze),
               d.risks.drought ? "" : idleLabel("drought", d), d.forecast ? "预报" : "再分析"]);
  }
  const csv = "\uFEFF" + rows.map(r => r.join(",")).join("\n");
  downloadFile(`丹橘知旱-风险日历-${all[0].date}_${all[all.length - 1].date}.csv`, csv, "text/csv;charset=utf-8");
}

// ---------- 停服公告（docs/03 §3 退场机制的执行件）----------
// 本项目无账号、不落库，除试点户外没有任何用户名单，因此"公告"只能走这条通道：
// 停服决策时把日期填入 SUNSET_DATE 并重新部署，任何打开页面的人都会看到剩余天数。
const SUNSET_DATE = "";
const todayStr = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
function renderSunset(dateStr = SUNSET_DATE) {
  const el = $("sunset");
  if (!el || !dateStr) return;
  const days = Math.ceil((parseDay(dateStr) - parseDay(todayStr())) / 86400000);
  el.textContent = days > 0
    ? `停服公告：本工具计划于 ${dateStr} 停止在线服务（还剩 ${days} 天）。此后代码、规则库与物候表校准流程以开源移交包形式存档，可自助部署。`
    : `本工具已于 ${dateStr} 停止在线更新，当前为归档版本；规则库与源码仍开源可用。`;
  el.style.display = "block";
}

// ---------- 主流程 ----------
async function load() {
  wireLocalControls(); wireDelegates();   // 绑定也经 load() 再走一遍：初始化段被烟测切走，只有这里能被覆盖（评审 A-R10㉒）
  $("phenology-now").innerHTML = `<span class="loading">正在获取 ${currentSite.name} 数据…</span>`;
  const { lat, lon } = currentSite;
  const dayMs = 86400000;
  // 本地（UTC+8）日期串，替代 toISOString 避免 UTC 边界错日
function localDayStr(offsetDays = 0) {
  const d = new Date(Date.now() + offsetDays * dayMs + 8 * 3600000);
  return d.toISOString().slice(0, 10);
}
  const start = localDayStr(-100);
  const archEnd = localDayStr(-6);
  // 任一侧数据失败都单独降级、并把原因显示出来（评审 A-Q8：原实现 archive 一挂就整盘空白）
  let past = [], forecast = [], archErr = null, fcErr = null;
  try { past = await fetchArchive(lat, lon, start, archEnd); } catch (e) { archErr = e.message; }
  try { forecast = await fetchForecast(lat, lon); } catch (e) { fcErr = e.message; }
  if (!past.length && !forecast.length) {
    $("phenology-now").innerHTML =
      `<span class="loading">数据加载失败：历史序列「${esc(archErr)}」／预报序列「${esc(fcErr)}」——可切换园块或稍后重试</span>`;
    return;
  }
  const degraded = [
    archErr && !past.length ? `历史数据不可用（${archErr}），现仅显示预报序列，干旱暂不判级` : null,
    fcErr && !forecast.length ? `预报数据不可用（${fcErr}），今日与未来提示暂缺，仅显示历史再分析` : null,
  ].filter(Boolean).join("；");
  const all = capFarForecast(assess(mergeSeries(past, forecast)), localDayStr(0));
  // "今天"取预报中当日；若预报缺失则取序列最后一天
  const todayStr = localDayStr(0);
  const today = all.find(x => x.date === todayStr) || all[all.length - 1];
  window.__today = today;
  window.__recent = all;
  renderPhenology(today, degraded);
  renderRisks(today);
  renderTimeline(all);
  renderAdvice(today);
  renderIrr();   // 切换园块后两个条件下拉要跟着显示该园块自己的记录（评审 C-Q7）
}

// 条件下拉的渲染：定义放在初始化段之前，烟测切走初始化段时 load() 仍能抽到它
function renderCond(selId, tipId, field, labels, explain) {
  const el = document.getElementById(selId);
  if (!el) return;
  el.value = condOf(field);
  const tip = document.getElementById(tipId);
  const rec = condRec(field);
  if (!tip) return;
  const raw = (rec || {}).v || "unknown";
  const shown = condOf(field);
  const stale = field === "fruit_cond" && raw !== "unknown" && shown === "unknown";
  tip.textContent = "本园块当前记录：" + labels[shown] +
    (rec && rec.at ? "（记录于 " + rec.at + (stale ? "，已超过一个物候周期，按未记录处理" : "") + "）" : "") + explain;
}
function renderIrr() {
  renderCond("irr", "irr-tip", "irr_cond", IRR_LABEL,
    "——无灌溉园块不会收到「浇透根系分布层」这类做不到的动作（评审 C-Q6）");
  renderCond("fruit", "fruit-tip", "fruit_cond", FRUIT_LABEL,
    "——记录为「已采收完」时，抢收与覆膜保果条目自动标为不适用；未记录时按「仍挂果」给出并提示确认（评审 C-Q7）");
}

// 事件委托单独成函数：烟测要能真的把事件派发进来，而不是直接调用处理函数
// （否则删掉这行 addEventListener，真机的复选框全废而四道闸门照绿，评审 A-R9⑦）
function wireDelegates() {
  if (typeof document === "undefined" || !document.addEventListener) return;
  document.addEventListener("change", (ev) => {
    if (ev && ev.target && (ev.target.id === "irr" || ev.target.id === "fruit")) {
      setCond(ev.target.id === "irr" ? "irr_cond" : "fruit_cond", ev.target.value);
      renderIrr();
      if (window.__today) renderAdvice(window.__today);
    }
  });
  document.addEventListener("change", onAdviceChange);   // 确认开关的委托监听
  document.addEventListener("input", onAdviceInput);     // 确认人／依据输入
}

// ---------- 初始化 ----------
renderSunset();
renderIrr();
wireLocalControls();
wireDelegates();
(function renderRuleVer() {
  const el = document.getElementById("ver");
  if (el) el.innerHTML = "判级口径：" + esc(ruleStamp()) + "｜详见 README 与 docs/02";
})();
renderSites();
document.querySelectorAll("[data-replay]").forEach(b => b.onclick = () => runReplay(b.dataset.replay));
$("ask").onclick = onAsk;
$("export-report").onclick = exportReport;
$("export-csv").onclick = exportCsv;
$("save-key").onclick = () => {
  const k = $("api-key").value.trim();
  if (k) { localStorage.setItem("deepseek_key", k); $("key-status").textContent = "已保存（仅本机）"; }
};
if (localStorage.getItem("deepseek_key")) $("key-status").textContent = "已保存 Key（仅本机）";
load();
