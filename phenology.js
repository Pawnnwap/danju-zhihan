// 丹江口库区柑橘物候与农技规则库
// 依据：湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》等公开农技资料；
// 物候期为丹江口库区（北亚热带季风气候）常规年景的近似区间，年际略有浮动。
// 本文件为确定性规则，供风险引擎调用；阈值可在试点中校准。

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
const PHENOLOGY = [
  { id: "dormant",  name: "越冬休眠期", from: [11, 25], to: [2, 28],  note: "抗寒防冻关键期" },
  { id: "bud",      name: "萌芽期",     from: [3, 1],  to: [3, 31],  note: "对倒春寒敏感" },
  { id: "bloom",    name: "开花期",     from: [4, 10], to: [5, 10],  note: "对低温最敏感，落花风险高" },
  { id: "young",    name: "幼果期",     from: [5, 11], to: [6, 20],  note: "生理落果期，忌高温干旱" },
  { id: "swell",    name: "果实膨大期", from: [6, 21], to: [9, 20],  note: "需水高峰，高温热害与干旱主害期" },
  { id: "ripe",     name: "成熟采收期", from: [9, 21], to: [11, 24], note: "丹江口蜜桔10月集中采收" },
];

// 日期 → 物候期（支持跨年区间）
function phenologyOf(date) {
  const m = date.getMonth() + 1, d = date.getDate();
  for (const p of PHENOLOGY) {
    const [fm, fd] = p.from, [tm, td] = p.to;
    const inRange = (fm <= tm) ? (m > fm || (m === fm && d >= fd)) && (m < tm || (m === tm && d <= td))
                               : (m > fm || (m === fm && d >= fd)) || (m < tm || (m === tm && d <= td));
    if (inRange) return p;
  }
  return PHENOLOGY[0]; // 11/25–11/24 兜底
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
      // 连续高温日升级
      const streak = ctx.heatStreak;
      if (streak >= 5 && t >= 35) { lv = "重"; why = `最高温 ${t}℃，已连续 ${streak} 天 ≥35℃`; }
      else if (streak >= 3 && t >= 35 && lv !== "重") { lv = lv === "中" ? "重" : "中"; why = `最高温 ${t}℃，已连续 ${streak} 天 ≥35℃`; }
      if (!lv) return null;
      if (ctx.pheno.id === "young" || ctx.pheno.id === "swell") { why += `；当前${ctx.pheno.name}，日灼与落果风险高`; }
      return { level: lv, why };
    }
  },
  {
    id: "drought", name: "干旱",
    // 连续无雨日 + 近30天降水距平（相对同期常年约值，用固定基准简化：30天累计 <30mm 为轻）
    eval(day, ctx) {
      // 干旱需要完整的 30 天回看窗口，避免序列开头的暖启动虚高
      if (ctx.dayIndex < 29) return null;
      const dry = ctx.dryStreak, rain30 = Math.round(ctx.rain30 * 10) / 10;
      let lv = null, why = "";
      if (dry >= 25 || rain30 <= 15) { lv = "重"; why = `连续 ${dry} 天无有效降水，30 天累计 ${rain30} mm`; }
      else if (dry >= 15 || rain30 <= 30) { lv = "中"; why = `连续 ${dry} 天无有效降水，30 天累计 ${rain30} mm`; }
      else if (dry >= 8 || rain30 <= 45) { lv = "轻"; why = `连续 ${dry} 天无有效降水，30 天累计 ${rain30} mm`; }
      if (!lv) return null;
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
        if (t <= 0) return { level: "重", why: `萌芽期最低温 ${t}℃ ≤0℃，嫩芽受冻` };
        if (t <= 3) return { level: "轻", why: `萌芽期最低温 ${t}℃ ≤3℃` };
      } else if (ctx.pheno.id === "young") {
        if (t <= 5) return { level: "轻", why: `幼果期最低温 ${t}℃ ≤5℃` };
      }
      return null;
    }
  },
  {
    id: "freeze", name: "越冬冻害",
    eval(day, ctx) {
      const t = day.tmin;
      if (t == null) return null;
      if (ctx.pheno.id !== "dormant" && !(ctx.pheno.id === "ripe" && t <= -5)) return null;
      if (t <= -7) return { level: "重", why: `最低温 ${t}℃ ≤-7℃，枝干冻害风险高` };
      if (t <= -5) return { level: "中", why: `最低温 ${t}℃ ≤-5℃，秋梢与晚熟果易受冻` };
      if (t <= -3) return { level: "轻", why: `最低温 ${t}℃ ≤-3℃，注意防护` };
      return null;
    }
  },
];

// 农技建议规则库（风险 × 物候期）。来源标注见 src。
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
      swell: ["立即灌溉补墒：滴灌/沟灌浇透根系分布层", "树盘覆盖秸秆保墒，减少蒸发", "叶面喷施抗旱保水剂（按说明书浓度）"],
      young: ["小水勤灌稳果", "覆盖保墒，间作绿肥翻压改土"],
      bloom: ["轻度补水防落花，避免大水漫灌"],
      default: ["检查灌溉设施，做好蓄水", "覆盖树盘，减少裸露蒸发"]
    }
  },
  cold: {
    src: "湖北省柑橘防冻与花期管理公开技术要点",
    items: {
      bloom: ["低温来临前傍晚园内熏烟（烟雾防霜）", "喷施防冻保护剂或 0.3% 磷酸二氢钾增强抗性", "低温过后及时检查坐果，补喷保花保果剂"],
      bud: ["寒潮前树干涂白、树盘覆盖", "覆盖防寒布/遮阳网挡夜间辐射霜冻"],
      young: ["低温过后追施速效肥恢复树势"],
      default: ["关注天气预报，寒潮前做好覆盖防护"]
    }
  },
  freeze: {
    src: "湖北省柑橘防冻技术措施（冻前灌水、涂白、覆盖、熏烟）",
    items: {
      dormant: ["冻前灌透水（土壤热容量大，减轻冻害）", "树干涂白 + 培土壅蔸", "幼树全树覆盖防寒布，成龄树树冠覆盖", "冻后及时剪除受冻枝，伤口保护"],
      ripe: ["抢收已熟果，减轻冻害损失", "树冠覆盖防寒，冻前灌水"],
      default: ["关注寒潮预警，提前防护"]
    }
  }
};

// 由日期串 "2026-10-08" 生成 Date（本地时区）
function parseDay(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
