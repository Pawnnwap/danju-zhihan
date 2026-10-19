// 从前端规则库里读出建议条目数与其中需当地确认的条数，供材料一致性闸门绑定。
// 与烟测同源（eval phenology.js），避免"材料里手抄 15/35、代码里已经变了"这类漂移（评审 A-Q6/B-Q10）。
// 用法：node tools/advice_counts.mjs   → {"confirm":16,"total":41}
import fs from 'node:fs';
const src = fs.readFileSync(new URL('../phenology.js', import.meta.url), 'utf8');
const { ADVICE, adviceOf } = (0, eval)(src + '\n;({ ADVICE, adviceOf })');
let confirm = 0, total = 0;
for (const lib of Object.values(ADVICE)) {
  for (const items of Object.values(lib.items)) {
    for (const it of items) {
      total += 1;
      if (adviceOf(it).confirm) confirm += 1;
    }
  }
}
process.stdout.write(JSON.stringify({ confirm, total }));
