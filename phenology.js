// 丹橘知旱 · 柑橘物候与农技规则库
// 依据：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料；
// 物候期为丹江口库区（北亚热带季风气候）常规年景的近似区间，年际略有浮动。
// 本文件为确定性规则，供风险引擎调用；阈值可在试点中校准。
// 与 tools/backtest.py 的 Python 实现逐日等价，由 tools/parity_check.py（含边界合成用例）强制校验。

const SITES = [
  { name: "凉水河镇", lat: 32.60, lon: 111.42, note: "库区柑橘核心乡镇（约8.7万亩）" },
  { name: "习家店镇", lat: 32.68, lon: 111.57, note: "库区北岸主产乡镇" },
  { name: "石鼓镇",   lat: 32.55, lon: 111.66, note: "丹江口水库东岸" },
  { name: "均县镇",   lat: 32.47, lon: 111.40, note: "库区滨湖乡镇" },
  { name: "六里坪镇", lat: 32.45, lon: 111.28, note: "近武当山麓" },
  { name: "浪河镇",   lat: 32.38, lon: 111.20, note: "库区南部（蓝藻预警平台所在地）" },
  { name: "丹江口城区", lat: 32.54, lon: 111.51, note: "市域参考点" },
];

// 物候期（月.日区间，含首尾）。丹江口温州蜜柑为主栽品种。
// 注意：区间必须无缝覆盖全年，否则兜底分支会静默改变物候判定（评审 A-Q10：闰日 2/29 曾落入兜底）。
const PHENOLOGY = [
  { id: "dormant",  name: "越冬休眠期", from: [11, 25], to: [2, 29],  note: "抗寒防冻关键期" },
  { id: "bud",      name: "萌芽期",     from: [3, 1],  to: [3, 31],  note: "对倒春寒敏感" },
  { id: "bud",      name: "现蕾期",     from: [4, 1],  to: [4, 9],   note: "花蕾对低温极敏感（萌芽期阈值适用）" },
  { id: "bloom",    name: "开花期",     from: [4, 10], to: [5, 10],  note: "对低温最敏感，落花风险高" },
  { id: "young",    name: "幼果期",     from: [5, 11], to: [6, 20],  note: "生理落果期，忌高温干旱" },
  { id: "swell",    name: "果实膨大期", from: [6, 21], to: [9, 20],  note: "需水高峰，高温热害与干旱主害期" },
  { id: "ripe",     name: "成熟采收期", from: [9, 21], to: [11, 24], note: "丹江口蜜桔10月集中采收" },
];

// 日期 → 物候期（支持跨年区间；dormant 覆盖 2/29，润年无洞）
function phenologyOf(date) {
  const m = date.getMonth() + 1, d = date.getDate();
  for (const p of PHENOLOGY) {
    const [fm, fd] = p.from, [tm, td] = p.to;
    const inRange = (fm <= tm) ? (m > fm || (m === fm && d >= fd)) && (m < tm || (m === tm && d <= td))
                               : (m > fm || (m === fm && d >= fd)) || (m < tm || (m === tm && d <= td));
    if (inRange) return p;
  }
  return PHENOLOGY[0]; // 理论上不再触达（区间已全年无缝）；保留兜底以显式失败而非抛错
}

// 四类风险阈值与规则（阈值单位：℃，mm）
const RISKS = [
  {
    id: "heat", name: "高温热害",
    // 依据当日最高气温 + 连续日数升级；幼果期与膨大期最敏感
    eval(day, ctx) {
      const t = day.tmax;
      if (t == null) return null;
      let lv = null, why = "";
      if (t >= 39) { lv = "重"; why = `最高温 ${t}℃ ≥39℃`; }
      else if (t >= 37) { lv = "中"; why = `最高温 ${t}℃ ≥37℃`; }
      else if (t >= 35) { lv = "轻"; why = `最高温 ${t}℃ ≥35℃`; }
      else if (t >= 33) { lv = "关注"; why = `最高温 ${t}℃ ≥33℃（连续将升级）`; }
      // 连续高温日升级（与 tools/backtest.py 逐分支等价；:57 原「streak≥5 且 lv=中」分支已被下一条覆盖，属死分支，评审 A-Q3 移除）
      const streak = ctx.heatStreak;
      if (streak >= 5 && t >= 35) { lv = "重"; why = `最高温 ${t}℃，已连续 ${streak} 天 ≥35℃`; }
      else if (streak >= 3 && t >= 35 && lv === "轻") { lv = "中"; why = `最高温 ${t}℃，已连续 ${streak} 天 ≥35℃`; }
      if (!lv) return null;
      if (ctx.pheno.id === "young" || ctx.pheno.id === "swell") { why += `；当前${ctx.pheno.name}，日灼与落果风险高`; }
      return { level: lv, why };
    }
  },
  {
    id: "drought", name: "干旱",
    // 连续无雨日 + 近30天累计降水双指标（窗口按日历天数计算，见 app.js assess）
    eval(day, ctx) {
      // 需完整 30 天日历回看窗口：缺测日会被上游过滤掉，使 rain30 系统性偏低→干旱等级虚高，
      // 因此窗口不满 30 天一律不判级（评审 A-Q5，替代原先"照判+文字标注"的偏置行为）
      if (ctx.dayIndex < 29 || ctx.windowDays < 30) return null;
      // 休眠期不出干旱等级：经 10 年基线核算，本区 12–2 月按现口径若不抑制则 93% 的天数出"轻"、
      // 40% 出"重"（tools/baseline_stats.py），常驻等级≈纯噪声（狼来了效应）；
      // 墒情信号改由越冬冻害建议中的"捏土验墒"可执行动作承载
      if (ctx.pheno.id === "dormant") return null;
      const dry = ctx.dryStreak, rain30 = ctx.rain30;
      // 阈值比较一律用原始浮点值；一位小数取整只用于展示文案（评审 A-Q2：JS 先取整、Python 比原值会分叉一档）
      const r30 = Math.round(rain30 * 10) / 10;
      let lv = null;
      if (dry >= 25 || rain30 <= 15) lv = "重";
      else if (dry >= 15 || rain30 <= 30) lv = "中";
      else if (dry >= 8 || rain30 <= 45) lv = "轻";
      if (!lv) return null;
      let why = `连续 ${dry} 天无有效降水，30 天累计 ${r30} mm`;
      if (ctx.pheno.id === "swell") why += "；膨大期缺水将直接减产";
      if (ctx.pheno.id === "bloom") why += "；花期干旱加剧落花";
      return { level: lv, why };
    }
  },
  {
    id: "cold", name: "花期/幼果低温（倒春寒）",
    eval(day, ctx) {
      const t = day.tmin;
      if (t == null) return null;
      if (ctx.pheno.id === "bloom") {
        if (t <= 2) return { level: "重", why: `花期最低温 ${t}℃ ≤2℃，落花严重` };
        if (t <= 5) return { level: "中", why: `花期最低温 ${t}℃ ≤5℃，落花加剧` };
        if (t <= 8) return { level: "关注", why: `花期最低温 ${t}℃，接近低温敏感区` };
      } else if (ctx.pheno.id === "bud") {
        // 萌芽期（3月）与现蕾期（4/1-4/9）共用 bud 分支；与开花期 ≤2/5/8 之间逐级递进，跨期边界跳档 ≤1
        if (t <= 0) return { level: "重", why: `${ctx.pheno.name}最低温 ${t}℃ ≤0℃，受冻风险高` };
        if (t <= 2) return { level: "中", why: `${ctx.pheno.name}最低温 ${t}℃ ≤2℃` };
        if (t <= 4) return { level: "轻", why: `${ctx.pheno.name}最低温 ${t}℃ ≤4℃` };
        if (t <= 7) return { level: "关注", why: `${ctx.pheno.name}最低温 ${t}℃ ≤7℃，接近敏感区（现蕾期花蕾对低温极敏感）` };
      } else if (ctx.pheno.id === "young") {
        // 幼果期分级补齐（评审 A-Q7：原仅 ≤5℃→轻，与开花期 ≤2℃→重在 5/10→5/11 边界会跳两档）
        if (t <= 0) return { level: "重", why: `幼果期最低温 ${t}℃ ≤0℃，霜冻直接落果` };
        if (t <= 2) return { level: "中", why: `幼果期最低温 ${t}℃ ≤2℃` };
        if (t <= 5) return { level: "轻", why: `幼果期最低温 ${t}℃ ≤5℃` };
      }
      return null; // 膨大/成熟/休眠期不适用低温（倒春寒）风险，改由越冬冻害风险承载
    }
  },
  {
    id: "freeze", name: "越冬冻害",
    eval(day, ctx) {
      const t = day.tmin;
      if (t == null) return null;
      if (ctx.pheno.id !== "dormant" && !(ctx.pheno.id === "ripe" && t <= -5)) return null;
      // 冻害是格点最无力的风险（库区水体调温+微地形主导，离水距离/坡向未建模）：
      // 触发即附背景场声明；门槛保持文献值，漏报代价>误报代价（涂白覆盖本就是普惠动作）
      const micro = "（乡镇尺度背景场：离水近的园块风险通常更低，山坳园块可能更重，请结合园地位置判断）";
      if (t <= -7) return { level: "重", why: `最低温 ${t}℃ ≤-7℃，枝干冻害风险高${micro}` };
      if (t <= -5) return { level: "中", why: `最低温 ${t}℃ ≤-5℃，秋梢与晚熟果易受冻${micro}` };
      if (t <= -3) return { level: "轻", why: `最低温 ${t}℃ ≤-3℃，注意防护${micro}` };
      return null;
    }
  },
];

// 农技建议规则库（风险 × 物候期）。来源标注见 src。
// 水源区（南水北调中线水源地）外部性约束：熏烟/覆膜/大水漫灌类动作附在地校验提示（评审 C-Q4）
const ADVICE = {
  heat: {
    src: "湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》及公开农技资料",
    items: {
      young: ["日射强的树冠外围挂遮阳网，减轻日灼落果", "傍晚少量多次补水，避免正午灌溉", "树盘覆盖秸秆/杂草 10cm 保湿降温"],
      swell: ["及时灌溉：有条件园块 3—5 天一次少量多次", "树盘覆盖保墒；行间生草降温", "避免高温期喷药，防药害"],
      bloom: ["高温期喷清水或叶面肥（傍晚）保花", "树盘覆盖，降低根际温度"],
      default: ["关注树体水分，避免长时间萎蔫", "高温时段减少田间作业"]
    }
  },
  drought: {
    src: "同上《抗高温干旱技术措施》；蓄水保墒常规做法",
    items: {
      swell: ["有灌溉：立即灌溉补墒，滴灌/沟灌浇透根系分布层", "无灌溉：穴贮肥水（根系集中区打孔覆膜，用后回收残膜，库区不留落地膜）或拉水浇根，重点保树冠内膛果", "树盘覆盖秸秆保墒，减少蒸发", "叶面喷施抗旱保水剂（按说明书浓度，水源区优先选环保型）"],
      young: ["小水勤灌稳果", "覆盖保墒，间作绿肥翻压改土"],
      bloom: ["轻度补水防落花，避免大水漫灌"],
      default: ["检查灌溉设施，做好蓄水", "无灌溉园块：穴贮肥水或拉水重点浇根，覆盖树盘保墒", "覆盖树盘，减少裸露蒸发"]
    }
  },
  cold: {
    src: "湖北省柑橘防冻与花期管理公开技术要点",
    items: {
      bloom: ["低温来临前傍晚园内熏烟（烟雾防霜；库区农事焚烧受当地管控约束，属需农技员确认项，未确认不推送）", "喷施防冻保护剂或 0.3% 磷酸二氢钾增强抗性", "低温过后及时检查坐果，补喷保花保果剂"],
      bud: ["现蕾/萌芽期防霜：傍晚熏烟（同上，属需确认项）或园内覆盖（树干涂白为冬季措施，花期前不宜）", "覆盖防寒布/遮阳网挡夜间辐射霜冻", "低温过后检查花蕾受害情况，补喷保花保果剂"],
      young: ["低温过后追施速效肥恢复树势，用量按当地推荐配方，避免退水直入库河"],
      default: ["关注天气预报，寒潮前做好覆盖防护"]
    }
  },
  freeze: {
    src: "湖北省柑橘防冻技术措施（冻前灌水、涂白、覆盖、熏烟）",
    items: {
      dormant: ["冻前灌透水（土壤热容量大，减轻冻害）——灌前先捏土验墒：10cm 土层手捏成团不散则缓灌或不灌；灌后避免退水直排入库河", "树干涂白 + 培土壅蔸", "幼树全树覆盖防寒布，成龄树树冠覆盖（覆盖材料用后回收）", "冻后及时剪除受冻枝，伤口保护"],
      ripe: ["抢收已熟果，减轻冻害损失", "树冠覆盖防寒，冻前灌水"],
      default: ["关注寒潮预警，提前防护"]
    }
  }
};

// 由日期串 "2026-10-08" 生成 Date（本地时区）
function parseDay(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
