#!/usr/bin/env python3
"""生成参赛 PPT：丹橘知旱——丹江口库区柑橘园气候决策助手"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

GREEN = RGBColor(0x0E, 0x52, 0x33)
GREEN2 = RGBColor(0x1A, 0x7A, 0x4A)
INK = RGBColor(0x1F, 0x2D, 0x27)
MUTED = RGBColor(0x6B, 0x7A, 0x72)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def add_slide(bg=WHITE):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    r.fill.solid(); r.fill.fore_color.rgb = bg; r.line.fill.background()
    r.shadow.inherit = False
    return s


def tb(slide, x, y, w, h, text, size=18, color=INK, bold=False, align=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame; tf.word_wrap = True
    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        for run in p.runs:
            run.font.size = Pt(size); run.font.color.rgb = color; run.font.bold = bold
        if align:
            p.alignment = align
    return box


def header(slide, num, title):
    tb(slide, 0.6, 0.35, 1.2, 0.8, num, size=40, color=GREEN2, bold=True)
    tb(slide, 1.5, 0.52, 11, 0.7, title, size=28, color=GREEN, bold=True)
    bar = slide.shapes.add_shape(1, Inches(0.65), Inches(1.28), Inches(2.2), Inches(0.06))
    bar.fill.solid(); bar.fill.fore_color.rgb = GREEN2; bar.line.fill.background(); bar.shadow.inherit = False


# S1 封面
s = add_slide(GREEN)
tb(s, 1, 2.1, 11.3, 1.2, "丹橘知旱", size=60, color=WHITE, bold=True)
tb(s, 1, 3.4, 11.3, 0.7, "丹江口库区柑橘园气候决策助手", size=28, color=WHITE)
tb(s, 1, 4.25, 11.3, 0.5, "One Place · One Climate Problem · One AI Solution", size=16, color=RGBColor(0xCF, 0xE6, 0xD8))
tb(s, 1, 6.3, 11.3, 0.9, "AI4Climate 全球青年气候解决方案挑战赛 · 团队 沧浪舟\n开放选题 / 气候适应与韧性", size=14, color=RGBColor(0xCF, 0xE6, 0xD8))

# S2 真实问题
s = add_slide()
header(s, "01", "真实气候问题：极端旱涝下的库区柑橘")
tb(s, 0.7, 1.7, 12, 1.2,
   "丹江口水库——南水北调中线核心水源区；库周 30 万亩柑橘、约 10 万果农。\n气候变化已让「靠天吃饭」的损失常态化：", size=19)
for i, (y, t) in enumerate([
    ("2.95", "●  2022 年长江流域 1961 年来最强高温干旱：十堰竹山 44.6℃ 刷新湖北纪录；气象局首次派飞机在丹江口流域人工增雨"),
    ("3.75", "●  参考园块（凉水河镇）当年夏季 ≥35℃ 达 30 天、极端最高 40.9℃（ERA5-Land 复算）"),
    ("4.55", "●  产区研究：2008—2023 年 38 次气象灾害，干旱致花期落花率增加 32%—45%；越冬冻害频发"),
    ("5.35", "●  2025 年汉江 64 年来最密集秋汛、7 次编号洪水——旱涝急转成为新常态"),
]):
    tb(s, 0.7, float(y), 12, 0.7, t, size=16)
tb(s, 0.7, 6.35, 12, 0.7, "痛点：果农凭经验应对，缺少「我这块园 × 当前物候 × 未来几天」的决策提示，灾后补救远贵于灾前防范。",
   size=16, color=GREEN, bold=True)

# S3 方案
s = add_slide()
header(s, "02", "方案：从公开气象数据到可执行农事建议")
steps = [
    ("公开气象数据", "Open-Meteo\nERA5-Land 再分析\n+ ECMWF 预报\n（免密钥·浏览器直连）"),
    ("物候期识别", "萌芽/开花/幼果\n膨大/成熟/越冬\n六期规则表"),
    ("四类风险引擎", "高温热害 · 干旱\n花期低温 · 越冬冻害\n分级 + 触发依据"),
    ("农事建议", "风险 × 物候期\n动作清单\n标注规则来源"),
]
for i, (t, d) in enumerate(steps):
    x = 0.7 + i * 3.15
    card = s.shapes.add_shape(1, Inches(x), Inches(2.0), Inches(2.85), Inches(2.6))
    card.fill.solid(); card.fill.fore_color.rgb = RGBColor(0xEF, 0xF6, 0xF1); card.line.color.rgb = GREEN2
    card.shadow.inherit = False
    tf = card.text_frame; tf.word_wrap = True
    tf.text = t
    for run in tf.paragraphs[0].runs:
        run.font.size = Pt(19); run.font.bold = True; run.font.color.rgb = GREEN
    p = tf.add_paragraph(); p.text = d
    for run in p.runs:
        run.font.size = Pt(13); run.font.color.rgb = INK
    if i < 3:
        ar = s.shapes.add_shape(13, Inches(x + 2.88), Inches(3.05), Inches(0.25), Inches(0.45))
        ar.fill.solid(); ar.fill.fore_color.rgb = GREEN2; ar.line.fill.background(); ar.shadow.inherit = False
tb(s, 0.7, 5.1, 12, 0.6, "在线体验：https://pawnnwap.github.io/danju-zhihan/      代码开源：https://github.com/Pawnnwap/danju-zhihan", size=15, color=GREEN2, bold=True)
tb(s, 0.7, 5.8, 12, 1.2,
   "手机浏览器打开即用：点选园块（或输入自定坐标）→ 当前物候与四类风险看板 → 90 天风险日历 → 农事建议 → 历史回放 → 问答。", size=15)

# S4 AI 任务
s = add_slide()
header(s, "03", "AI 承担的具体任务：规则算得准，大模型讲得懂")
tb(s, 0.7, 1.75, 5.8, 0.5, "① 确定性风险引擎（核心）", size=20, color=GREEN, bold=True)
tb(s, 0.7, 2.3, 5.8, 3.4,
   "●  阈值 + 连续日数 + 物候期组合判断，逐日输出\n     等级与触发依据\n●  全部阈值显式可审计、可复算、可回测\n●  干旱为降水型 + 蒸散型双口径取高者\n     （ET0 参考蒸散，按物候期十年分位定标）\n●  与 Python 实现逐日对拍：442 天 + 33 组边界\n     用例 + 阈值三方绑定；15 个注入变异全被拦下", size=15)
tb(s, 6.9, 1.75, 5.8, 0.5, "② DeepSeek 大模型解释层（可选接入）", size=20, color=GREEN, bold=True)
tb(s, 6.9, 2.3, 5.8, 3.4,
   "●  以风险数据 + 农技规则库为上下文，生成个性化\n     建议与自然语言追问\n●  回答强制引用数据日期，抑制幻觉\n●  LLM 只做转译与解释，不做数值预测", size=16)
tb(s, 0.7, 5.9, 12, 0.9,
   "为什么不是「为了 AI 而 AI」：没有大模型时产品依然完整可用；大模型解决的是「同一份数据对不同用户的不同表述 + 追问」这一具体负担。",
   size=15, color=MUTED)

# S5 验证
s = add_slide()
header(s, "04", "验证：历史回测已通过，90 天试点已设计")
card = s.shapes.add_shape(1, Inches(0.7), Inches(1.7), Inches(12), Inches(2.6))
card.fill.solid(); card.fill.fore_color.rgb = RGBColor(0xEF, 0xF6, 0xF1); card.line.color.rgb = GREEN2
card.shadow.inherit = False
tf = card.text_frame; tf.word_wrap = True
tf.text = "历史回测（2022 年热害 · 凉水河镇 · 真实数据）"
for run in tf.paragraphs[0].runs:
    run.font.size = Pt(20); run.font.bold = True; run.font.color.rgb = GREEN
for line in ["≥35℃ 高温日 30 天    热害『重』级 13 天    极端最高 40.9℃（08-07）",
             "首个『重』级提示 2022-08-04（39.2℃）——比峰值提前 3 天；时效归因于单日 ≥39℃ 阈值，不记在连续升级头上",
             "干旱双口径同期『重』8 天／『中』54 天，首个提示 06-01（喂数带 35 天 warm-up，与产品一致）",
             "蒸散口径的增益点：2022-08-24~27 def30 123–138mm 判『重』，纯降水口径同期最高只判『中』",
             "可离线复算：输入序列已冻结入库；对拍 + 边界用例 + 变异自证三道闸门全绿"]:
    p = tf.add_paragraph(); p.text = "●  " + line
    for run in p.runs:
        run.font.size = Pt(15); run.font.color.rgb = INK
tb(s, 0.7, 4.5, 12, 2.2,
   "90 天试点计划：\n●  对象：库区 1—2 家合作社 / 乡镇农技员（30—100 亩），现场两次 + 微信回访\n●  指标：越冬准备就绪率 ≥60%（越冬季主指标）｜采纳率 ≥50%（中重级不足 5 次则只报案例）\n        提示时效 ≥1 天｜满意度 ≥3.8｜续用意愿 ≥80%；n<10 一律只报分子分母，不报百分比\n●  前置：与合作社/农技中心签一页纸《试用与责任约定》，外部性动作先确认才推送", size=16)

# S6 市场与安全
s = add_slide()
header(s, "05", "可持续 · 开放 · 安全边界")
tb(s, 0.7, 1.8, 5.8, 0.5, "可持续（12 个月，诚实选边）", size=20, color=GREEN, bold=True)
tb(s, 0.7, 2.35, 5.8, 2.6,
   "●  12 个月内收入为 0——已选定赛道不追求收入\n●  定位：低成本公益基础设施 + 开源作品\n●  机制：赛事奖金／政府专项／基金会\n     （当前均未申请；个人身份通常无申报资格）\n●  免费＝现有全部功能 + 无付费墙 + 可自部署", size=15)
tb(s, 6.9, 1.8, 5.8, 0.5, "扩展与单点风险", size=20, color=GREEN, bold=True)
tb(s, 6.9, 2.35, 5.8, 2.6,
   "●  同气候带柑橘产区＝弱复制（仍需当地校准）\n●  跨作物（茶园）＝强校准 5–15 人日／产区\n●  12 个月内只做丹江口一地\n●  bus factor＝1：暂无人承诺接手，故不自称\n     “可依赖的公共基础设施”", size=15)
tb(s, 0.7, 5.3, 12, 1.3,
   "安全边界：风险等级用于辅助安排农事，不构成灾害预警，不替代官方气象预警与专业农技指导；再分析数据有不确定性，重要决策结合当地气象部门信息。\n伦理：不采集个人身份信息；试点凭证（截图/照片）打码后本地留存，对外只给聚合统计，参与者可随时退出并要求删除。", size=14, color=RGBColor(0xC0, 0x39, 0x2B))

# S7 团队
s = add_slide(GREEN)
tb(s, 1, 2.4, 11.3, 1, "沧浪舟", size=44, color=WHITE, bold=True)
tb(s, 1, 3.6, 11.3, 0.6, "个人参赛 · 2026 年 10 月", size=18, color=RGBColor(0xCF, 0xE6, 0xD8))
tb(s, 1, 4.6, 11.3, 1.2,
   "把一个真实问题带来，让 AI 真正去解决它。\n一个地方。一个气候问题。一个 AI 解决方案。", size=20, color=WHITE)
tb(s, 1, 6.3, 11.3, 0.5, "ai4climate.world ｜ pawnnwap.github.io/danju-zhihan ｜ deepseek.club/topic/5043", size=13, color=RGBColor(0xCF, 0xE6, 0xD8))

prs.save("丹橘知旱-参赛PPT.pptx")
print("saved 丹橘知旱-参赛PPT.pptx")
