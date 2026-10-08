// 丹橘知旱 · 主逻辑
// 数据：Open-Meteo Archive（ERA5-Land 再分析）+ Forecast（ECMWF）——浏览器直连，免密钥
// 引擎：phenology.js 中的物候与规则库（确定性计算）；LLM 仅做解释层（可选）

const $ = (id) => document.getElementById(id);
const LEVEL_COLOR = { "重": "var(--red)", "中": "var(--orange)", "轻": "var(--yellow)", "关注": "var(--blue)", "无": "var(--gray)" };
const RISK_NAME = { heat: "高温热害", drought: "干旱", cold: "花期/幼果低温", freeze: "越冬冻害" };
const RISK_SHORT = { heat: "高温", drought: "干旱", cold: "低温", freeze: "冻害" };

let currentSite = SITES[0];

// ---------- 数据获取 ----------
// Archive API 有约 2~5 天滞后；Forecast API 覆盖今天起 16 天。两者拼接保证"今天"有值。
async function fetchArchive(lat, lon, startDate, endDate) {
  const url = `https://archive-api.open-meteo.com/v1/archive?latitude=${lat}&longitude=${lon}` +
    `&start_date=${startDate}&end_date=${endDate}` +
    `&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=Asia%2FShanghai`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("历史数据获取失败 HTTP " + res.status);
  const j = await res.json();
  const d = j.daily;
  return d.time.map((t, i) => ({
    date: t,
    tmax: d.temperature_2m_max[i],
    tmin: d.temperature_2m_min[i],
    pr: d.precipitation_sum[i],
  })).filter(x => x.tmax != null && x.tmin != null && x.pr != null); // 丢弃滞后的空尾日
}

async function fetchForecast(lat, lon) {
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
    `&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&forecast_days=16&timezone=Asia%2FShanghai`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("预报获取失败 HTTP " + res.status);
  const j = await res.json();
  const d = j.daily;
  return d.time.map((t, i) => ({
    date: t,
    tmax: d.temperature_2m_max[i],
    tmin: d.temperature_2m_min[i],
    pr: d.precipitation_sum[i],
    forecast: true,
  }));
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
    const pheno = phenologyOf(date);
    heatStreak = (day.tmax != null && day.tmax >= 35) ? heatStreak + 1 : 0;
    dryStreak = (day.pr != null && day.pr < 1) ? dryStreak + 1 : 0;
    const rain30 = days.slice(Math.max(0, i - 29), i + 1).reduce((s, x) => s + (x.pr || 0), 0);
    const ctx = { pheno, heatStreak, dryStreak, rain30, dayIndex: i };
    const risks = {};
    for (const r of RISKS) risks[r.id] = r.eval(day, ctx);
    out.push({ ...day, pheno, risks });
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

function renderPhenology(today) {
  $("phenology-now").innerHTML =
    `当前物候：<b>${today.pheno.name}</b>（${today.pheno.note}）｜园块：<b>${currentSite.name}</b>｜数据日期：<b>${today.date}${today.forecast ? "（预报）" : ""}</b>`;
}

function renderRisks(today) {
  $("risk-now").innerHTML = Object.keys(RISK_NAME).map(id => {
    const r = today.risks[id];
    const lv = r ? r.level : "无";
    return `<div class="risk-item lv-${lv === "无" ? "" : lv}">
      <div class="name">${RISK_NAME[id]}</div>
      <div class="level">${lv}</div>
      <div class="why">${r ? r.why : "今日无该风险触发"}</div>
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
    return `<div class="day ${mark}" style="background:${color}" data-tip="${tip}"></div>`;
  }).join("");
}

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
      const items = lib.items[today.pheno.id] || lib.items.default;
      return `<div class="adv-item"><h4>【${RISK_NAME[k]}·${v.level}】${v.why}</h4>
        <ul>${items.map(x => `<li>${x}</li>`).join("")}</ul>
        <div class="src">规则来源：${lib.src}</div></div>`;
    }).join("");
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
        <span class="replay-stat">秋汛连阴雨影响采收与树体</span>`;
    } else {
      const coldest = assessed.reduce((a, b) => (b.tmin < a.tmin ? b : a));
      const freezeDays = assessed.filter(d => d.risks.freeze).length;
      const heavy = assessed.filter(d => d.risks.freeze && ["重", "中"].includes(d.risks.freeze.level)).length;
      stats = `<span class="replay-stat">极端最低温 <b>${coldest.tmin}℃</b>（${coldest.date}）</span>
        <span class="replay-stat">冻害风险日 <b>${freezeDays}</b> 天（中重 <b>${heavy}</b> 天）</span>`;
    }
    const worst = assessed.map(d => ({ d, w: worstRisk(d.risks) })).filter(x => x.w);
    // 优先展示"重"级事件（按日期），无重级时展示末尾事件
    const LV_RANK = { "重": 0, "中": 1, "轻": 2, "关注": 3 };
    const topList = worst.filter(x => x.w.level === "重").length >= 3
      ? worst.filter(x => x.w.level === "重").slice(0, 5)
      : worst.slice(-5);
    const top = topList.map(x => `<li>${x.d.date}｜${RISK_NAME[x.w.id]}·${x.w.level}：${x.w.why}</li>`).join("");
    box.innerHTML = `<div>${stats}</div><p class="hint">若当时使用本工具，主要提示为：</p><ul style="padding-left:20px">${top}</ul>
      <p class="hint">回测说明：风险等级由当日与前 30 天数据按规则引擎复算，供检验提示时效，非事后拟合。</p>`;
  } catch (err) {
    box.innerHTML = `<p class="answer err">回放失败：${err.message}</p>`;
  }
}

// ---------- 问答 ----------
function buildContext() {
  const t = window.__today;
  return JSON.stringify({
    site: currentSite.name,
    today: { date: t.date, pheno: t.pheno.name, tmax: t.tmax, tmin: t.tmin,
      risks: Object.fromEntries(Object.entries(t.risks).map(([k, v]) => [k, v ? v.level : null])) },
    recent7: (window.__recent || []).slice(-7).map(d => ({ date: d.date, tmax: d.tmax, tmin: d.tmin, pr: d.pr })),
  });
}

function ruleAnswer(q) {
  const t = window.__today;
  const a = [];
  if (/灌|浇|水|旱/.test(q)) {
    const d = t.risks.drought, h = t.risks.heat;
    a.push(d ? `干旱风险为【${d.level}】：${d.why}。` : "当前干旱风险未触发。", h ? `高温风险为【${h.level}】。` : "");
    a.push(ADVICE.drought.items[t.pheno.id] || ADVICE.drought.items.default);
  } else if (/冻|寒|低温|冷/.test(q)) {
    const c = t.risks.cold, f = t.risks.freeze;
    a.push(c ? `低温风险【${c.level}】：${c.why}` : f ? `冻害风险【${f.level}】：${f.why}` : "当前低温/冻害风险未触发。");
    a.push(ADVICE.freeze.items[t.pheno.id] || ADVICE.freeze.items.default);
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
  const sys = `你是丹江口库区柑橘园气候决策助手"丹橘知旱"。只用给定数据与湖北省公开农技规则回答果农问题，给出可执行动作；必须引用数据日期；若数据不足以判断，明确说明并建议咨询当地农技部门。数据上下文：${buildContext()}`;
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
    let ans = null, mode = "";
    try { ans = await askLLM(q); if (ans) mode = "（DeepSeek 大模型生成，已结合规则库上下文）"; } catch (e) { ans = null; }
    if (!ans) { ans = ruleAnswer(q); mode = "（内置规则引擎生成；接入 DeepSeek Key 后可获得个性化解释）"; }
    box.innerHTML = `<b>问：${q}</b>\n${ans}\n<span class="cite">${mode} 风险等级不构成灾害预警。</span>`;
  } catch (e) {
    box.innerHTML = `<p class="err">出错了：${e.message}</p>`;
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
        const items = lib.items[t.pheno.id] || lib.items.default;
        return `<h3>${RISK_NAME[k]}（${v.level}）</h3><ul>${items.map(x => `<li>${x}</li>`).join("")}</ul><p class="src">规则来源：${lib.src}</p>`;
      }).join("")
    : `<p>当前无中重度风险。当前物候：${t.pheno.name}（${t.pheno.note}）。</p>`;
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>丹橘知旱建议报告 ${t.date}</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:800px;margin:24px auto;color:#1f2d27}h1{color:#0e5233}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px 8px;font-size:14px}.src{color:#6b7a72;font-size:12px}.meta{color:#6b7a72;font-size:13px}</style>
</head><body>
<h1>丹橘知旱 · 柑橘园气候决策建议报告</h1>
<p class="meta">园块：${currentSite.name}（${currentSite.lat}, ${currentSite.lon}）｜数据日期：${t.date}${t.forecast ? "（预报）" : ""}｜生成时间：${new Date().toLocaleString("zh-CN")}</p>
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

// ---------- 主流程 ----------
async function load() {
  $("phenology-now").innerHTML = `<span class="loading">正在获取 ${currentSite.name} 数据…</span>`;
  const { lat, lon } = currentSite;
  const dayMs = 86400000;
  const start = new Date(Date.now() - 100 * dayMs).toISOString().slice(0, 10);
  const archEnd = new Date(Date.now() - 6 * dayMs).toISOString().slice(0, 10);
  try {
    const past = await fetchArchive(lat, lon, start, archEnd);
    let forecast = [];
    try { forecast = await fetchForecast(lat, lon); } catch (e) { forecast = []; }
    const all = assess(mergeSeries(past, forecast));
    // "今天"取预报中当日；若预报缺失则取序列最后一天
    const todayStr = new Date().toISOString().slice(0, 10);
    const today = all.find(x => x.date === todayStr) || all[all.length - 1];
    window.__today = today;
    window.__recent = all;
    renderPhenology(today);
    renderRisks(today);
    renderTimeline(all);
    renderAdvice(today);
  } catch (err) {
    $("phenology-now").innerHTML = `<span class="loading">数据加载失败：${err.message}（可尝试切换园块或稍后重试）</span>`;
  }
}

// ---------- 初始化 ----------
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
