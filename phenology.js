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

// 蒸散亏缺（def30 = 近 30 天 ET0 累计 − 近 30 天降水累计, mm）分物候期阈值。
// 唯一生成处：tools/baseline_stats.py 的 derive_thresholds(CALIB_YEARS)，即用 **2016–2020** 这五年
// 算出的 p75/p90/p99（分位定义＝线性插值，与 numpy.percentile(method="linear") 同，第三方可直接复算）；留出段 2021–2025 不参与定标，因此回测头条是真正的样本外结果（评审 A-Q3）。
// 由 parity_check.py 第 3 层把 JS、Python 与现算分位绑死，防止手抄漂移（评审 A-Q9）。
// 语义：只用来**抬高**干旱等级（降水型与蒸散型取高者），不单独压低——因此不会漏掉少雨型干旱。
const DEF_THRESH = {
  dormant: { 轻: 28.7, 中: 36.9, 重: 45.9 },
  bud:     { 轻: 42.1, 中: 52.7, 重: 62.9 },
  bloom:   { 轻: 54.4, 中: 66.9, 重: 82.0 },
  young:   { 轻: 77.2, 中: 99.7, 重: 126.3 },
  swell:   { 轻: 54.2, 中: 88.7, 重: 120.9 },
  ripe:    { 轻: 38.4, 中: 61.9, 重: 98.4 },
};
// 蒸散口径是"该物候期内的相对分位排名"，不是绝对缺水量的度量（留出检验显示其跨期不稳，
// 开花期 2016–20 定的 p99 在 2021–25 判到 19.4% 天数）。因此它**单独**最高只能说到"中"：
// 要出"重"（用户会据以在 24–48h 内采取行动的等级），必须降水口径自己也给出 ≥"中" 的绝对水量佐证。
// 这条佐证规则取代了此前只给开花期打的补丁式封顶（评审 A-Q1：一个相对分位不该单独决定最高响应强度）。
const DEF_SOLO_CAP = "中";
// 休眠期没有降水口径可言（沉默），且柑橘冬季需水近零、ET0 是草地参考蒸散会高估树体蒸腾，
// 所以该期蒸散口径只到"轻"级提示，不得单独驱动"本周内完成"级别的行动（评审 C-Q7）
const DEF_CAP_DORMANT = "轻";
// 规则版本号：页面页脚与导出报告都印它，用户截图/反馈能追溯到具体规则版本；
// 与 tools/backtest.py 的 RULE_VERSION 同值，由 parity_check.py 第 3 层绑定（改一处即对拍失败）
const RULE_VERSION = "r5-20261013";
// 定标段与留出段：写在页脚/报告里，让"数字从哪来、哪段是样本外"在界面上就说清（评审 C-Q）
const DEF_CALIB_LABEL = "2016–2020";
const HOLDOUT_LABEL = "2021–2025";
const ruleStamp = () => `规则 ${RULE_VERSION}｜干旱双口径取高者，蒸散口径阈值按物候期 ${DEF_CALIB_LABEL} 分位定标`
  + `（${HOLDOUT_LABEL} 为留出段），单独出级封顶“${DEF_SOLO_CAP}”、需降水口径达“${DEF_SOLO_CAP}”才到“重”`;
const RANK_OF = (lv) => (lv === "重" ? 3 : lv === "中" ? 2 : lv === "轻" ? 1 : 0);

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
      if (ctx.pheno.id === "young" || ctx.pheno.id === "swell") { why += `；当前${ctx.pheno.name}，日灼与落果风险高（本地产量响应未联校）`; }
      return { level: lv, why };
    }
  },
  {
    id: "drought", name: "干旱",
    // 双口径取高者：①降水型（连续无有效降水日 + 30 天日历窗口累计降水）；
    // ②蒸散型（def30 = 近 30 天 ET0 累计 − 近 30 天累计降水，按物候期自身 2016–2020 定标段分位判级）。
    // 蒸散口径只用于**抬高**等级，不与降水口径互相抵消——现口径认得少雨型干旱，
    // 认不得高热蒸散型干旱（评审 C-Q2：2022-08-24~28 该格点 def30 达 127~138mm，引擎当时只报"轻/中"）。
    eval(day, ctx) {
      // 需完整 30 天日历回看窗口，且窗口内无缺测日：缺测会被当作 0mm 使 rain30 偏低、等级虚高，
      // 故窗口不满或有缺测日一律不判级（评审 A-Q5/A-Q4，显式缺测语义）
      if (ctx.dayIndex < 29 || ctx.windowDays < 30 || ctx.windowBad > 0) return null;
      const dry = ctx.dryStreak, rain30 = ctx.rain30;
      // 阈值比较一律用原始浮点值；一位小数取整只用于展示文案（评审 A-Q2）
      const r30 = Math.round(rain30 * 10) / 10;
      // 降水口径在休眠期不判级、蒸散口径仍可出级；各级常驻率的唯一生成处是
      // tools/baseline_stats.py（第 2、3 节）与 docs/02，注释不复制数字以免漂移（评审 C-Q2）。
      let rainLv = null;
      if (ctx.pheno.id !== "dormant") {
        if (dry >= 25 || rain30 <= 15) rainLv = "重";
        else if (dry >= 15 || rain30 <= 30) rainLv = "中";
        // "轻"必须真的连着多天没下过雨：仅凭 30 天累计偏低就报轻，在季风区的萌芽/成熟期是常驻噪声
        // （十年基线：萌芽·现蕾期 rain30≤45 命中 258/400 天，若单独构成等级则该期 64% 天数带级）
        else if (dry >= 8 && rain30 <= 45) rainLv = "轻";
      }
      let defLv = null, defLvRaw = null, defLvTrue = null, def30 = null, th = null, defCapped = false;
      if (ctx.et30 != null) {
        const defRaw = ctx.et30 - rain30;   // 与 Python 侧一致：阈值比较用原始值，取整只进文案
        def30 = Math.round(defRaw * 10) / 10;
        th = DEF_THRESH[ctx.pheno.id];
        if (th) {
          if (defRaw >= th["重"]) defLvRaw = "重";
          else if (defRaw >= th["中"]) defLvRaw = "中";
          else if (defRaw >= th["轻"]) defLvRaw = "轻";
          defLv = defLvRaw;
          defLvTrue = defLvRaw;
          // 佐证规则（评审 A-Q1）：降水口径未到"中"时，蒸散口径的"重"降格为"中"
          // 上限取决于有没有绝对水量佐证：降水口径达「中」→ 允许蒸散口径说「重」；没有佐证 → 压到 DEF_SOLO_CAP
          // 休眠期连佐证口径都不存在，且冬季需水近零、ET0 是草地参考蒸散（系统性高估树体蒸腾）：
          // 只有越过该期自身 p90 才给"轻"级提示。若沿用"超 p75 即轻"，留出段轻级会常驻到 67 天/年（评审 C-Q7）
          if (ctx.pheno.id === "dormant") {
            defLvTrue = defLvRaw;                 // 封顶前的真实分位档，文案要印它而不是封顶后的档
            defLv = defRaw >= th["中"] ? DEF_CAP_DORMANT : null;
            defLvRaw = defLv;
            defCapped = false;
          } else {
            const capLv = RANK_OF(rainLv) >= RANK_OF(DEF_SOLO_CAP) ? "重" : DEF_SOLO_CAP;
            if (RANK_OF(defLv) > RANK_OF(capLv)) { defLv = capLv; defCapped = true; }
          }
        }
      }
      const lv = RANK_OF(rainLv) >= RANK_OF(defLv) ? rainLv : defLv;
      if (!lv) return null;
      const rainTxt = `连续 ${dry} 天无有效降水，30 天累计 ${r30.toFixed(1)} mm`;
      // 蒸散文案只在蒸散口径真的定过级时才拼（否则 th 为空会抛错，评审侧一跑就崩）
      // 休眠期是特例：门槛取的是该期自身的 p90（表里的"中"位），只用来给"轻"级提示——
      // 若照字面印"轻级线 28.7"，屏幕上就会与"未达 p90 线"的沉默文案互相矛盾（评审 A-R11⑧）
      let defTxt = null;
      if (defLv) {
        defTxt = ctx.pheno.id === "dormant"
          ? `30 天蒸散亏缺 ${def30.toFixed(1)} mm，超休眠期自身 p90 门槛 ${th["中"].toFixed(1)} mm`
            + `（分位达"${defLvTrue}"级线 ${th[defLvTrue].toFixed(1)} mm；冬季只出"轻"级提示）`
          : `30 天蒸散亏缺 ${def30.toFixed(1)} mm，超该物候期定标分位${defLvRaw}级线 ${th[defLvRaw].toFixed(1)} mm`;
      }
      if (defCapped) defTxt += `（分位达"${defLvRaw}"，但降水口径未达"${DEF_SOLO_CAP}"作佐证，按"${DEF_SOLO_CAP}"处理）`;
      let why;
      if (defTxt && rainLv === lv && defLv === lv) why = `${rainTxt}；${defTxt}`;
      else if (defTxt && defLv === lv && rainLv !== lv) why = defTxt;
      else why = rainTxt;
      if (ctx.pheno.id === "swell") why += "；膨大期缺水主要影响单果重与果径（本地产量响应未联校）";
      if (ctx.pheno.id === "bloom") why += "；花期缺水与落花相关（本地未做联校）";
      if (ctx.pheno.id === "dormant") why += "；越冬墒情偏低——要不要冻前灌水按冻害卡的时效钟定：距降温 ≥3 天才灌透，不足 3 天不要临冻灌透，灌前先捏土验墒";
      return { level: lv, why };
    }
  },
  {
    id: "cold", name: "花期/幼果低温（倒春寒）",
    eval(day, ctx) {
      const t = day.tmin;
      if (t == null) return null;
      if (ctx.pheno.id === "bloom") {
        if (t <= 2) return { level: "重", why: `花期最低温 ${t}℃ ≤2℃，落花风险高（本地未联校）` };
        if (t <= 5) return { level: "中", why: `花期最低温 ${t}℃ ≤5℃，落花风险上升（本地未联校）` };
        if (t <= 8) return { level: "关注", why: `花期最低温 ${t}℃，接近低温敏感区` };
      } else if (ctx.pheno.id === "bud") {
        // 萌芽期（3月）与现蕾期（4/1-4/9）共用 bud 分支；与开花期 ≤2/5/8 之间逐级递进，跨期边界跳档 ≤1
        if (t <= 0) return { level: "重", why: `${ctx.pheno.name}最低温 ${t}℃ ≤0℃，受冻风险高` };
        if (t <= 2) return { level: "中", why: `${ctx.pheno.name}最低温 ${t}℃ ≤2℃` };
        if (t <= 4) return { level: "轻", why: `${ctx.pheno.name}最低温 ${t}℃ ≤4℃` };
        if (t <= 7) return { level: "关注", why: `${ctx.pheno.name}最低温 ${t}℃ ≤7℃，接近敏感区（现蕾期花蕾对低温极敏感）` };
      } else if (ctx.pheno.id === "young") {
        // 幼果期分级补齐（评审 A-Q7：原仅 ≤5℃→轻，与开花期 ≤2℃→重在 5/10→5/11 边界会跳两档）
        if (t <= 0) return { level: "重", why: `幼果期最低温 ${t}℃ ≤0℃，霜冻落果风险高（本地未联校）` };
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

// 等级必须改变行动，否则"抬高等级"只是换颜色（评审 C-Q1）：不同等级对应不同的执行时效与留痕要求。
const LEVEL_TIMING = {
  "重": "执行时效：24–48 小时内完成，并在导出报告中留痕（照片/农资单据）；同时通知对接农技员。",
  "中": "执行时效：本周内完成；期末复盘时逐条核对是否执行。",
  "轻": "执行时效：本周内择机处理即可，以观察与复查为主，不需要为此停工。",
};

// 冻害的执行时效不能用通用的"24–48 小时"（评审 C-Q3）：灌水蓄热要在降温前数日才抬得起地温，
// 距降温不足 3 天时灌透反而制造根区结冰风险。此处按风险覆写，页面与导出报告同源。
const RISK_TIMING = {
  freeze: {
    "重": "执行时效：涂白、培土壅蔸、覆盖等物理防护**立即**做；冻前灌透水必须在**降温前 3–5 天**完成——"
        + "距降温不足 3 天时不要临冻灌透（根区结冰风险），改以覆盖为主并请农技员确认。留痕：照片 + 处理日期。",
    "中": "执行时效：降温前完成覆盖与培土；灌水仅在距降温 ≥3 天且墒情偏浅时考虑。",
    "轻": "执行时效：本周内择机处理，以观察墒情与树势为主。",
  },
};
const timingOf = (riskId, level) => (RISK_TIMING[riskId] && RISK_TIMING[riskId][level]) || LEVEL_TIMING[level] || "";

// 农技建议规则库（风险 × 物候期）。来源标注见 src。
// 每项为字符串，或 {t: 文本, confirm: true}——confirm 表示该动作在丹江口库区（南水北调中线水源地）
// 存在环境外部性（燃烧、残膜、退水、投入品），必须先经当地农技/主管部门确认才谈得上执行。
// 这是数据层标记 + 渲染层强制前缀（见 app.js fmtAdvice），不是文档里的一句承诺。
const ADVICE = {
  heat: {
    // 只取该文献的**灾中应对**部分；"灾后恢复"（旱后修剪时序、次生病虫、日灼果处理）本轮未入库，见 docs/03 §6
    src: "湖北省农业农村厅《湖北柑橘抗高温干旱及灾后恢复技术措施》灾中应对部分 + 公开农技资料（灾后恢复章节未入库，待逐字核对后补）",
    items: {
      young: ["日射强的树冠外围挂遮阳网，减轻日灼落果", "傍晚少量多次补水，避免正午灌溉", "树盘覆盖秸秆/杂草 10cm 保湿降温"],
      swell: ["及时灌溉：有条件园块 3—5 天一次少量多次", "树盘覆盖保墒；行间生草降温", { t: "避免高温期喷药，防药害（如须喷药，药剂选择与用量先经当地确认）", confirm: true }],
      bloom: [{ t: "高温期喷清水或叶面肥（傍晚）保花", confirm: true }, "树盘覆盖，降低根际温度"],
      default: ["关注树体水分，避免长时间萎蔫", "高温时段减少田间作业"]
    }
  },
  drought: {
    src: "同上《抗高温干旱技术措施》；蓄水保墒常规做法",
    items: {
      swell: [{ t: "有灌溉：立即灌溉补墒，滴灌/沟灌浇透根系分布层", irr: true },
        { t: "无水源时以保树为先：酌情疏除部分幼果、暂停追肥，减少挂果负担（源-库取舍由农户按树势定，工具不替你决定）", confirm: true },
        { t: "无水源时拉水浇根（重点保树冠内膛果；运水成本与劳力自定，不等审批）", irr: false },
        { t: "穴贮肥水（根系集中区打孔覆膜保墒）", confirm: true },
        "树盘覆盖秸秆保墒，减少蒸发",
        { t: "叶面喷施抗旱保水剂（按说明书浓度，水源区优先选环保型）", confirm: true }],
      young: [{ t: "小水勤灌稳果", irr: true }, "覆盖保墒，间作绿肥翻压改土"],
      bloom: [{ t: "轻度补水防落花，避免大水漫灌", irr: true }, "记录本周花期最低温与花量，供坐果率核对"],
      default: [{ t: "检查灌溉设施，做好蓄水", irr: true }, { t: "无灌溉园块：穴贮肥水或拉水重点浇根，覆盖树盘保墒", confirm: true, irr: false }, "覆盖树盘，减少裸露蒸发"]
    }
  },
  cold: {
    src: "湖北省柑橘防冻与花期管理公开技术要点",
    items: {
      bloom: [{ t: "今明两晨记录固定 5 株标记树的最低温与开花量（拍照留档，不逐株——30 亩园逐株做不完），低温过后 48 小时内抽查这 5 株的受害花——先留下可核对的事实" },
        { t: "低温来临前傍晚园内熏烟（烟雾防霜；表定开花期 4/10–5/10 对坡地/高海拔常偏早一旬以上，"
            + "园内尚未开花时本条不适用，先按现蕾期处理并把物候期反馈给农技员核对）", confirm: true },
        { t: "喷施防冻保护剂或 0.3% 磷酸二氢钾增强抗性", confirm: true },
        { t: "低温过后及时检查坐果，补喷保花保果剂", confirm: true }],
      bud: [{ t: "本周记录固定 5 株标记树的花蕾/新梢受冻情况（拍照留档；萌芽期无花蕾则记新梢受害，不逐株——30 亩园逐株做不完），冻后暂缓修剪与喷肥，等恢复生长再定" },
        { t: "现蕾/萌芽期防霜：傍晚熏烟或园内覆盖（树干涂白为冬季措施，花期前不宜）", confirm: true },
        { t: "覆盖防寒布/遮阳网挡夜间辐射霜冻，用后回收不落地", confirm: true },
        { t: "低温过后检查花蕾受害情况，补喷保花保果剂", confirm: true }],
      young: [{ t: "低温过后先查幼果受害（转绿、膨大停滞或皱缩拍照留档），再决定是否补肥" },
        { t: "低温过后追施速效肥恢复树势，用量按当地推荐配方", confirm: true }],
      default: ["关注天气预报，寒潮前做好覆盖防护"]
    }
  },
  freeze: {
    src: "湖北省柑橘防冻技术措施（冻前灌水、涂白、覆盖、熏烟）",
    items: {
      dormant: [{ t: "降温前抢收可采部分，不要等到落霜后再处理（晚熟杂柑 12–1 月采收，本表按蜜柑 10 月集中采收期建模——本条是否适用取决于园块「是否仍挂果」记录）", fruit: true },
        { t: "来不及抢收时对树冠覆膜/覆盖保果（覆盖材料用后回收，不落地）", confirm: true, fruit: true },
        { t: "冻前灌透水（土壤热容量大，减轻冻害）——灌前先捏土验墒：10cm 土层手捏成团不散则缓灌或不灌；避免退水直排入库河", confirm: true },
        "树干涂白 + 培土壅蔸",
        { t: "幼树全树覆盖防寒布，成龄树树冠覆盖（覆盖材料用后回收）", confirm: true },
        { t: "冻后修剪**不要当年立剪**：待春季新梢萌发、枯死界限明确后再剪并做伤口保护（当地冻害处理惯例待逐字核对，未核前只作提示）", confirm: true }],
      ripe: [{ t: "抢收已熟果，减轻冻害损失", fruit: true }, "树冠覆盖防寒（不需水源即可做）",
        { t: "冻前灌水补墒", irr: true, confirm: true }],
      default: ["关注寒潮预警，提前防护"]
    }
  }
};

// 渲染与文本共用：把一条建议规范成 {t, confirm}
// 需水动作按关键词判定，不靠人工逐条标记（评审 C-Q1：上一版只给干旱库标了 irr，
// 热害库的"傍晚少量多次补水""及时灌溉"照样排进无灌溉园块的可执行段第一条）
const NEEDS_WATER = /灌溉|补水|浇透|浇水|灌透水|冻前灌水|小水勤灌|大水漫灌/;
const adviceOf = (it) => (typeof it === "string" ? { t: it, confirm: false, irr: NEEDS_WATER.test(it), fruit: false }
  // irr/fruit 显式写 false 时以显式值为准：关键词表会把"无水源时拉水浇根"这类**专为无水园写的动作**
  // 误判成需灌溉条目，再被无水块过滤器判成"不适用"（评审 C-R8①：唯一的救命动作被自己的过滤删掉）
  : { t: it.t, confirm: !!it.confirm, irr: it.irr === undefined ? NEEDS_WATER.test(it.t) : !!it.irr,
      fruit: !!it.fruit });


// 由日期串 "2026-10-08" 生成 Date（本地时区）
function parseDay(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
