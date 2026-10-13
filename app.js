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

// "无等级"有三种含义，对用户必须分开说（评审 A-Q10：把"不判级"写成"没旱情"会误导越冬管理）
function idleLabel(id, today) {
  const dq = today.dq;
  if (id === "drought" && dq) {
    if (dq.windowDays < 30 || dq.windowBad > 0) return "不判级：30 天回看数据不完整";
    if (!dq.etOk) return "不判级：ET0 缺测，蒸散口径本轮退出";
    if (today.pheno.id === "dormant") return "不判级：休眠期降水口径沉默，蒸散口径未达线";
  }
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
const SRC_NOTE = "（本项目按公开技术文件自行整理的规则摘录，与发文单位无关联，未经当地核验）";
const adviceText = (it) => { const a = adviceOf(it); return (a.confirm ? CONFIRM_TAG : "") + a.t; };

// fail-closed（评审 A-Q7）：含外部性的动作默认**不进入可执行建议**，只出现在"待当地确认清单"；
// 现场有农技员/主管部门确认后，把该动作文本写入本机 localStorage 的 confirmed_actions 才升格为可执行。
function confirmedActions() {
  try { return JSON.parse(localStorage.getItem("confirmed_actions") || "[]"); } catch (e) { return []; }
}
const isConfirmed = (t) => confirmedActions().includes(t);
const adviceItem = (a) => `<li>${a.confirm ? "【已经当地确认】" : ""}${esc(a.t)}</li>`;

function renderAdvice(today) {
  const PHENO_TIP = {
    dormant: "做好防冻预案检查", bud: "关注倒春寒预报", bloom: "保花为主，留意低温与干旱",
    young: "稳果水肥管理", swell: "膨大期水肥是全年关键", ripe: "安排采收与防寒预案",
  };
  const actives = Object.entries(today.risks).filter(([k, v]) => v && ["重", "中", "轻"].includes(v.level));
  let html = "";
  if (!actives.length) {
    html = `<div class="adv-item"><h4>当前无中重度风险</h4>
      <ul><li>当前物候：${today.pheno.name}——${PHENO_TIP[today.pheno.id]}</li></ul>
      <div class="src">规则来源：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料</div></div>`;
  } else {
    html = actives.map(([k, v]) => {
      const lib = ADVICE[k];
      const items = (lib.items[today.pheno.id] || lib.items.default).map(adviceOf);
      const go = items.filter(x => !x.confirm || isConfirmed(x.t)).map(adviceItem).join("");
      const wait = items.filter(x => x.confirm && !isConfirmed(x.t)).map((x) => `<li>${esc(x.t)}</li>`).join("");
      return `<div class="adv-item"><h4>【${RISK_NAME[k]}·${v.level}】${esc(v.why)}</h4>
        <ul>${go || "<li>本风险下暂无可自动执行的动作</li>"}</ul>
        ${wait ? `<h5>待当地确认清单（未确认前不作为可执行建议）</h5><ul class="pending">${wait}</ul>` : ""}
        <div class="src">规则来源：${esc(lib.src)}${SRC_NOTE}</div></div>`;
    }).join("");
    html += `<div class="src">"待当地确认"针对库区农事焚烧、地膜残留、灌溉退水与投入品使用等外部性动作；本工具不替你判断合规性，确认后由农技员或农户在本机登记即可升格为可执行建议。</div>`;
  }
  $("advice").innerHTML = html;
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
      risks: Object.fromEntries(Object.entries(t.risks).map(([k, v]) => [k, v ? v.level : null])) },
    recent7: all.filter(x => !x.forecast).slice(-7).map(d7),
    // 未来 7 天预报与引擎等级一并给出（评审 A-Q4：原上下文只有历史数据，
    // 果农问"接下来几天怎么办"时模型只能凭空生成）
    forecast7: all.filter(x => x.forecast && x.date >= t.date).slice(0, 7)
      .map(x => ({ ...d7(x), risks: Object.fromEntries(Object.entries(x.risks).map(([k, v]) => [k, v ? v.level : null])) })),
    rule: "风险等级与数值一律引用本上下文，不得自行判定新等级、不得预测新数值；上下文缺失时直说不知道。",
  });
}

function ruleAnswer(q) {
  const t = window.__today;
  const a = [];
  if (/灌|浇|水|旱/.test(q)) {
    const d = t.risks.drought, h = t.risks.heat;
    a.push(d ? `干旱风险为【${d.level}】：${d.why}。` : "当前干旱风险未触发。", h ? `高温风险为【${h.level}】。` : "");
    a.push((ADVICE.drought.items[t.pheno.id] || ADVICE.drought.items.default).map(adviceText));
  } else if (/冻|寒|低温|冷/.test(q)) {
    // 按实际触发的风险取库（评审 A-Q10：此前低温分支一律取 freeze 库，
    // 花期/幼果低温触发时既取错建议又与同页建议面板给出两套动作）
    const c = t.risks.cold, f = t.risks.freeze;
    if (c) {
      a.push(`低温风险【${c.level}】：${c.why}`);
      a.push((ADVICE.cold.items[t.pheno.id] || ADVICE.cold.items.default).map(adviceText));
    } else if (f) {
      a.push(`冻害风险【${f.level}】：${f.why}`);
      a.push((ADVICE.freeze.items[t.pheno.id] || ADVICE.freeze.items.default).map(adviceText));
    } else {
      a.push("当前低温/冻害风险未触发。");
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
  const key = localStorage.getItem("deepseek_key");
  if (!key) return null;
  const sys = `你是丹江口库区柑橘园气候决策助手"丹橘知旱"。只用给定数据与湖北省公开农技规则回答果农问题，给出可执行动作；必须引用数据日期；风险等级与数值只能原样引用上下文，不得自行判定等级或预测新数值；数据不足以判断时明确说明并建议咨询当地农技部门。数据上下文：${buildContext()}`;
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
        // LLM 约束不靠提示词兜底：追加统一声明，等级以看板为准（评审 Q7）
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
      mode = localStorage.getItem("deepseek_key")
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

function exportReport() {
  const t = window.__today;
  if (!t) return;
  const risks = Object.keys(RISK_NAME).map(id => {
    const r = t.risks[id];
    return `<tr><td>${RISK_NAME[id]}</td><td>${r ? r.level : "无"}</td><td>${r ? r.why : "—"}</td></tr>`;
  }).join("");
  const actives = Object.entries(t.risks).filter(([k, v]) => v && ["重", "中", "轻"].includes(v.level));
  const adviceHtml = actives.length
    ? actives.map(([k, v]) => {
        const lib = ADVICE[k];
        const items = (lib.items[t.pheno.id] || lib.items.default).map(adviceOf);
        const go = items.filter(x => !x.confirm || isConfirmed(x.t)).map(adviceItem).join("");
        const wait = items.filter(x => x.confirm && !isConfirmed(x.t)).map((x) => `<li>${esc(x.t)}</li>`).join("");
        return `<h3>${RISK_NAME[k]}（${v.level}）</h3><ul>${go || "<li>本风险下暂无可自动执行的动作</li>"}</ul>` +
          (wait ? `<h4>待当地确认清单（未确认前不作为可执行建议）</h4><ul>${wait}</ul>` : "") +
          `<p class="src">规则来源：${esc(lib.src)}${SRC_NOTE}</p>`;
      }).join("")
    : `<p>当前无中重度风险。当前物候：${t.pheno.name}（${t.pheno.note}）。</p>`;
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>丹橘知旱建议报告 ${t.date}</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:800px;margin:24px auto;color:#1f2d27}h1{color:#0e5233}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px 8px;font-size:14px}.src{color:#6b7a72;font-size:12px}.meta{color:#6b7a72;font-size:13px}</style>
</head><body>
<h1>丹橘知旱 · 柑橘园气候决策建议报告</h1>
<p class="meta">园块：${esc(currentSite.name)}（${esc(currentSite.lat)}, ${esc(currentSite.lon)}）｜数据日期：${t.date}${t.forecast ? "（预报）" : ""}｜生成时间：${new Date().toLocaleString("zh-CN")}</p>
<p class="src">本建议由开源工具生成、未经本地核验；转述给其他农户时请一并说明这一点。</p>
<p>当前物候：<b>${t.pheno.name}</b>——${t.pheno.note}。当日气象：最高 ${t.tmax}℃ / 最低 ${t.tmin}℃ / 降水 ${t.pr}mm。</p>
<h2>一、四类风险等级</h2>
<table><tr><th>风险</th><th>等级</th><th>触发依据</th></tr>${risks}</table>
<h2>二、建议农事动作</h2>
${adviceHtml}
<h2>三、数据与规则来源</h2>
<p>气象数据：Open-Meteo（ERA5-Land 再分析 + ECMWF 预报）；规则：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料。</p>
<p class="src">免责声明：本报告由确定性规则自动生成，用于辅助安排农事，不构成灾害预警，不替代官方气象预警与专业农技指导。</p>
<p class="src">丹橘知旱 · AI4Climate 参赛项目 · 团队 沧浪舟 · ${t.date}</p>
</body></html>`;
  downloadFile(`丹橘知旱-建议报告-${t.date}.html`, html, "text/html;charset=utf-8");
}

function exportCsv() {
  const all = window.__recent || [];
  if (!all.length) return;
  const rows = [["日期", "物候期", "最高温C", "最低温C", "降水mm", "高温热害", "干旱", "花期低温", "越冬冻害", "数据类型"]];
  for (const d of all) {
    const lv = (r) => (r ? r.level : "无");
    rows.push([d.date, d.pheno.name, d.tmax, d.tmin, d.pr, lv(d.risks.heat), lv(d.risks.drought), lv(d.risks.cold), lv(d.risks.freeze), d.forecast ? "预报" : "再分析"]);
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
  const all = assess(mergeSeries(past, forecast));
  // 远期预报不确定性封顶：ECMWF 第 8 天起逐日温度误差 ±2-3℃（阈值间隔才 2℃），
  // 8 天外的中/重级一律压为"关注"，避免"掷骰子"式色块（评审意见）
  const todayStr0 = localDayStr(0);
  for (const d of all) {
    if (!d.forecast) continue;
    const lead = (parseDay(d.date) - parseDay(todayStr0)) / 86400000;
    if (lead >= 8) {
      for (const k of Object.keys(d.risks)) {
        if (d.risks[k]) {
          // 统一改写依据文本：不再保留与被封顶等级矛盾的触发描述（评审 Q6）
          d.risks[k] = {
            level: "关注",
            why: `${RISK_NAME[k]}模式值：最高 ${d.tmax ?? "—"}℃ / 最低 ${d.tmin ?? "—"}℃（预报时效 ${Math.round(lead)} 天，误差可达 ±2-3℃，仅作过程示意，不构成动作依据）`
          };
        }
      }
    }
  }
  // "今天"取预报中当日；若预报缺失则取序列最后一天
  const todayStr = localDayStr(0);
  const today = all.find(x => x.date === todayStr) || all[all.length - 1];
  window.__today = today;
  window.__recent = all;
  renderPhenology(today, degraded);
  renderRisks(today);
  renderTimeline(all);
  renderAdvice(today);
}

// ---------- 初始化 ----------
renderSunset();
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
