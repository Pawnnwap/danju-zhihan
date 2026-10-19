// 对拍辅助：在 Node 中运行前端规则引擎（phenology.js + app.js 的 assess），输出逐日等级。
// 由 tools/parity_check.py 调用；只依赖规则本身，不碰 DOM。
// 依赖全部显式注入（不依赖 eval 中函数声明提升到全局这种隐晦行为）：RISKS / parseDay / phenologyOf 以形参传入 assess。
// 用法：
//   echo '[{date,tmax,tmin,pr},...]' | node tools/parity_js.mjs    # 输出 [{date,pheno,heat,drought,cold,freeze},...]
//   node tools/parity_js.mjs --tables                             # 输出物候期表（供全年覆盖与两表一致性核算）
import fs from 'node:fs';

const read = (rel) => fs.readFileSync(new URL(rel, import.meta.url), 'utf8');

// phenology.js 的顶层 const 只在同一次 eval 作用域内可见，故把源码与取值表达式拼成一次 eval
const api = (0, eval)(read('../phenology.js')
  + '\n;({ RISKS, parseDay, phenologyOf, PHENOLOGY, DEF_THRESH, DEF_SOLO_CAP, DEF_CAP_DORMANT, RULE_VERSION, '
  + 'DEF_CALIB_LABEL, HOLDOUT_LABEL, ruleStamp })');

// 从 app.js 中抽出 assess 函数体（按花括号配平），不复制粘贴规则实现
const app = read('../app.js');
const start = app.indexOf('function assess(');
if (start < 0) throw new Error('app.js 中未找到 function assess —— 改名会使本对拍失效并主动报错');
let depth = 0, end = -1;
for (let j = app.indexOf('{', start); j < app.length; j++) {
  if (app[j] === '{') depth++;
  else if (app[j] === '}' && --depth === 0) { end = j + 1; break; }
}
if (end < 0) throw new Error('assess 函数花括号不配平');
const assess = new Function(
  'RISKS', 'parseDay', 'phenologyOf',
  'return (' + app.slice(start, end) + ')',
)(api.RISKS, api.parseDay, api.phenologyOf);

const lv = (r) => (r ? r.level : null);

if (process.argv[2] === '--tables') {
  process.stdout.write(JSON.stringify({
    phenology: api.PHENOLOGY.map((p) => ({ id: p.id, name: p.name, from: p.from, to: p.to })),
    defThresh: api.DEF_THRESH,
    defSoloCap: api.DEF_SOLO_CAP,
    defCapDormant: api.DEF_CAP_DORMANT,
    ruleVersion: api.RULE_VERSION,
    defCalibLabel: api.DEF_CALIB_LABEL,
    holdoutLabel: api.HOLDOUT_LABEL,
    ruleStamp: api.ruleStamp(),
  }), () => process.exit(0));
} else {
  const chunks = [];
  process.stdin.on('data', (c) => chunks.push(c));
  process.stdin.on('end', () => {
    const days = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const out = assess(days).map((d) => ({
      date: d.date,
      pheno: d.pheno.id,
      heat: lv(d.risks.heat),
      drought: lv(d.risks.drought),
      heatWhy: d.risks.heat ? d.risks.heat.why : null,
      coldWhy: d.risks.cold ? d.risks.cold.why : null,
      freezeWhy: d.risks.freeze ? d.risks.freeze.why : null,
      // 归因文案也进对拍：两套实现各写一套说明，等于把"为什么是这个等级"这件事留了两份真相（评审 A-Q4）
      why: d.risks.drought ? d.risks.drought.why : null,
      cold: lv(d.risks.cold),
      freeze: lv(d.risks.freeze),
    }));
    process.stdout.write(JSON.stringify(out));
  });
}
