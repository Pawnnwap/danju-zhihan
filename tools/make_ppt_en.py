#!/usr/bin/env python3
"""生成英文版参赛 PPT：Danju Zhihan — Climate Decision Assistant for Citrus Growers.

英文比中文长 30–60%，排版适配规则：
· 标题降号（28→24pt）并允许两行；正文降 1–2pt
· 卡片/文本框普遍加宽（5.8→6.0in+）或加高
· 英文行宽更宽：长句必须显式断行，不能照搬中文的换行位置
· 截图不变（UI 本身是中文的，如实标注 "Chinese UI, rules auditable"）
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

GREEN = RGBColor(0x0E, 0x52, 0x33)
GREEN2 = RGBColor(0x1A, 0x7A, 0x4A)
INK = RGBColor(0x1F, 0x2D, 0x27)
MUTED = RGBColor(0x6B, 0x7A, 0x72)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xC0, 0x39, 0x2B)
LIGHT = RGBColor(0xEF, 0xF6, 0xF1)
MINT = RGBColor(0xCF, 0xE6, 0xD8)

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


def header(slide, num, title, size=24):
    # 英文标题降号 28→24 并允许更宽（0.6→12.7in），仍与序号框保持缝隙
    tb(slide, 0.6, 0.35, 0.95, 0.8, num, size=40, color=GREEN2, bold=True)
    tb(slide, 1.65, 0.52, 11.05, 0.7, title, size=size, color=GREEN, bold=True)
    bar = slide.shapes.add_shape(1, Inches(0.65), Inches(1.28), Inches(2.2), Inches(0.06))
    bar.fill.solid(); bar.fill.fore_color.rgb = GREEN2; bar.line.fill.background(); bar.shadow.inherit = False


# S1 Cover
s = add_slide(GREEN)
_fruit = s.shapes.add_shape(9, Inches(1.0), Inches(1.34), Inches(0.66), Inches(0.6))
_fruit.fill.solid(); _fruit.fill.fore_color.rgb = RGBColor(0xF0, 0x8A, 0x20)
_fruit.line.fill.background(); _fruit.shadow.inherit = False
_leaf = s.shapes.add_shape(9, Inches(1.27), Inches(1.15), Inches(0.19), Inches(0.19))
_leaf.fill.solid(); _leaf.fill.fore_color.rgb = RGBColor(0x26, 0x8A, 0x5A)
_leaf.line.fill.background(); _leaf.shadow.inherit = False
_rule = s.shapes.add_shape(1, Inches(1.0), Inches(4.82), Inches(1.5), Inches(0.055))
_rule.fill.solid(); _rule.fill.fore_color.rgb = RGBColor(0xF0, 0x8A, 0x20)
_rule.line.fill.background(); _rule.shadow.inherit = False

tb(s, 1, 2.0, 11.3, 1.0, "Danju Zhihan", size=52, color=WHITE, bold=True)
tb(s, 1, 3.05, 11.3, 0.6, "Tangerine Knows the Drought — a climate decision assistant for citrus growers",
   size=21, color=WHITE)
tb(s, 1, 3.75, 11.3, 0.5, "Danjiangkou Reservoir Area · South-North Water Transfer Middle Route", size=15, color=MINT)
tb(s, 1, 4.45, 11.3, 0.5, "One Place · One Climate Problem · One AI Solution", size=15, color=MINT)
tb(s, 1, 6.3, 11.3, 0.9,
   "AI4Climate Global Youth Climate Solutions Challenge · Team Canglangzhou\nOpen track / Climate adaptation & resilience",
   size=14, color=MINT)

# S2 Real problem
s = add_slide()
header(s, "01", "The Real Problem: Citrus Under Extreme Drought-Flood Swings")
tb(s, 0.7, 1.7, 12, 1.0,
   "Danjiangkou Reservoir — the core water source of the South-North Water Transfer (Middle Route);\n"
   "300,000 mu of citrus and ~100,000 growers along its banks. Weather-driven losses are now routine:", size=17)
for y, t, h in [
    (2.85, "●  2022: the Yangtze basin's worst heat-drought since 1961 — 44.6℃ in Zhushan, Shiyan set a Hubei record;\n"
           "     weather authorities flew cloud-seeding aircraft over the Danjiangkou basin for the first time", 0.95),
    (3.85, "●  Reference orchard (Liangshuihe town): 30 days at ≥35℃ that summer, extreme high 40.9℃ (ERA5-Land recomputed)", 0.6),
    (4.5, "●  Literature (study area is Wuxue, Hubei — not Danjiangkou): 38 weather disasters in 2008–2023;\n"
          "     flowering water deficit raised flower drop by 32–45% (DOI 10.12238/as.v8i8.3240)\n"
          "     — used only as problem motivation, never as evidence of this tool's effect", 1.1),
    (5.7, "●  2025: the Hanjiang river's densest autumn flood season in 64 years, 7 numbered floods — drought-flood swings are the new normal", 0.6),
]:
    tb(s, 0.7, y, 12, h, t, size=15)
tb(s, 0.7, 6.55, 12, 0.7,
   "Pain point: growers improvise from experience, with no decision hint of \"my orchard × current phenophase × next few days\" — "
   "remediation after the fact costs far more than preparation before it.", size=15, color=GREEN, bold=True)

# S3 Solution
s = add_slide()
header(s, "02", "The Solution: From Open Weather Data to Actionable Advice")
steps = [
    ("Open weather data", "Open-Meteo\nERA5-Land reanalysis\n+ ECMWF forecast\n(no key, browser-direct)"),
    ("Phenophase ID", "budbreak / bloom /\nfruit set / swell /\nharvest / dormancy"),
    ("Four-risk engine", "heat · drought\nlow-temp bloom · winter\nlevels + trigger evidence"),
    ("Farm advice", "risk × phenophase\naction list\nrules sourced"),
]
for i, (t, d) in enumerate(steps):
    x = 0.7 + i * 3.15
    card = s.shapes.add_shape(1, Inches(x), Inches(2.0), Inches(2.85), Inches(2.6))
    card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = GREEN2
    card.shadow.inherit = False
    tf = card.text_frame; tf.word_wrap = True
    tf.text = t
    for run in tf.paragraphs[0].runs:
        run.font.size = Pt(16); run.font.bold = True; run.font.color.rgb = GREEN
    p = tf.add_paragraph(); p.text = d
    for run in p.runs:
        run.font.size = Pt(12); run.font.color.rgb = INK
    if i < 3:
        ar = s.shapes.add_shape(1, Inches(x + 2.90), Inches(3.28), Inches(0.22), Inches(0.05))
        ar.fill.solid(); ar.fill.fore_color.rgb = GREEN2; ar.line.fill.background(); ar.shadow.inherit = False
tb(s, 0.7, 5.1, 12, 0.6, "Live demo: https://pawnnwap.github.io/danju-zhihan/      Open source: https://github.com/Pawnnwap/danju-zhihan",
   size=14, color=GREEN2, bold=True)
tb(s, 0.7, 5.8, 12, 1.2,
   "Works in any phone browser: pick an orchard block (or enter custom coordinates) → phenophase & four-risk dashboard → "
   "90-day risk calendar → farm advice → historical replay → Q&A.", size=14)

# S3.5 Product UI (real screenshots)
s = add_slide()
header(s, "02", "Product UI: Real Screenshots (same build as the live demo)")
shots = [
    ("img/dashboard.png", "① Dashboard: current phenophase + four risk levels & trigger evidence"),
    ("img/replay.png", "② Historical replay: day-by-day of the 2022 summer heat event"),
    ("img/calendar.png", "③ 90-day risk calendar (CSV export, BOM, opens in Excel)"),
    ("img/qa.png", "④ Q&A: built-in rule-based answers when no LLM key is set"),
    ("img/advice-export.png", "⑤ Advice & exportable report (printable for technicians)"),
]
for i, (path, cap) in enumerate(shots[:3]):
    x = 0.7 + i * 4.16
    pic = s.shapes.add_picture(path, Inches(x), Inches(1.8), Inches(3.62), Inches(2.04))
    pic.line.color.rgb = GREEN2; pic.line.width = Pt(1)
    tb(s, x, 3.9, 3.62, 0.6, cap, size=11, color=MUTED)
for i, (path, cap) in enumerate(shots[3:]):
    x = 0.7 + i * 4.16
    pic = s.shapes.add_picture(path, Inches(x), Inches(4.55), Inches(3.62), Inches(2.04))
    pic.line.color.rgb = GREEN2; pic.line.width = Pt(1)
    tb(s, x, 6.65, 3.62, 0.6, cap, size=11, color=MUTED)
card = s.shapes.add_shape(1, Inches(9.02), Inches(4.55), Inches(3.62), Inches(2.04))
card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = GREEN2
card.shadow.inherit = False
ctf = card.text_frame; ctf.word_wrap = True
ctf.text = "Runs in any phone browser"
for run in ctf.paragraphs[0].runs:
    run.font.size = Pt(15); run.font.bold = True; run.font.color.rgb = GREEN
for line in ["No install, no account, no backend",
             "Orchard conditions stay on-device",
             "175-second demo video, 1080p, VO + subtitles",
             "UI shown is Chinese; rules are open & auditable"]:
    p = ctf.add_paragraph(); p.text = "·  " + line
    for run in p.runs:
        run.font.size = Pt(11); run.font.color.rgb = INK

# S4 AI task
s = add_slide()
header(s, "03", "AI's Specific Task: Rules Compute, LLM Explains")
tb(s, 0.7, 1.75, 6.0, 0.5, "① Deterministic risk engine (core)", size=19, color=GREEN, bold=True)
tb(s, 0.7, 2.3, 6.0, 3.5,
   "●  Thresholds + consecutive days + phenophase, daily\n"
   "     level & trigger evidence\n"
   "●  All thresholds explicit — auditable, reproducible, testable\n"
   "●  Drought: dual criteria take the higher — rainfall type +\n"
   "     ET0 type (calibrated on 2016–2020 by phenophase;\n"
   "     ET0 alone caps at mid, severe needs rainfall corroboration)\n"
   "●  Day-by-day parity vs the Python implementation: 442 days +\n"
   "     37 boundary cases + threshold binding + verbatim text match;\n"
   "     44 injected mutations all caught by the gates", size=13)
tb(s, 7.0, 1.75, 5.7, 0.5, "② DeepSeek LLM explanation layer (optional)", size=19, color=GREEN, bold=True)
tb(s, 7.0, 2.3, 5.7, 3.5,
   "●  Risk data + rule library as context → personalized\n"
   "     advice & natural-language follow-ups\n"
   "●  Answers must cite data dates — hallucination suppressed\n"
   "●  LLM only translates & explains; it never predicts numbers\n"
   "●  No key? Falls back to built-in rule Q&A —\n"
   "     the product stays complete without the LLM", size=13)
tb(s, 0.7, 6.0, 12, 0.9,
   "Not \"AI for AI's sake\": the product works fully without the LLM; the model solves one concrete burden — "
   "the same data needs different wording for different users, plus follow-up questions.", size=14, color=MUTED)

# S5 Verification
s = add_slide()
header(s, "04", "Verification: Backtest Passed; 90-Day Pilot Designed")
card = s.shapes.add_shape(1, Inches(0.7), Inches(1.7), Inches(12), Inches(2.7))
card.fill.solid(); card.fill.fore_color.rgb = LIGHT; card.line.color.rgb = GREEN2
card.shadow.inherit = False
tf = card.text_frame; tf.word_wrap = True
tf.text = "Historical backtest (2022 heat event · Liangshuihe town · real data)"
for run in tf.paragraphs[0].runs:
    run.font.size = Pt(18); run.font.bold = True; run.font.color.rgb = GREEN
for line in ["30 days at ≥35℃    13 days at heat-severe    extreme high 40.9℃ (Aug 7)",
             "First severe-level alert Aug 4 (39.2℃) — 3 days ahead of the peak; timing attributed to the single-day ≥39℃ threshold, not the streak upgrade",
             "Drought dual-criteria same period: 1 severe / 55 mid (severe needs rainfall corroboration); first alert Jun 1 (35-day warm-up, same as the product)",
             "Where the ET0 criterion adds value (recomputable day-by-day): four days in Aug 2022 exceeded the swelling-phase p99 (123–138mm); Aug 26 scored severe (rain30=29.9 lifted rainfall to mid, corroboration holds), other three recorded mid",
             "Offline-recomputable: input series frozen in repo; parity + boundary + mutation + doc-consistency gates all green"]:
    p = tf.add_paragraph(); p.text = "●  " + line
    for run in p.runs:
        run.font.size = Pt(12); run.font.color.rgb = INK
tb(s, 0.7, 4.6, 12, 2.3,
   "90-day pilot plan:\n"
   "●  Who: 1–2 citrus cooperatives / township technicians (30–100 mu), two on-site visits + weekly check-ins "
   "(cash cap ¥3,400; drops to ¥2,200 if compensation cannot be legitimately paid)\n"
   "●  Metrics (n=2 households, 13 weeks, counts only): weekly-active ≥19/26 household-weeks | winter-readiness ≥6/8\n"
   "        adoption ≥5/10 (fewer than 5 mid/severe alerts → report cases only) | satisfaction 2/2 households avg ≥4 | continued use 2/2\n"
   "●  Failure (pre-registered): weekly-active ≤14/26, or readiness <3/8, or any household never opens → core target missed\n"
   "●  Precondition: one-page pilot & responsibility agreement with the cooperative (internal arrangement); unconfirmed external actions are never pushed", size=13)

# S6 Sustainability & safety
s = add_slide()
header(s, "05", "Sustainability · Openness · Safety Boundaries")
tb(s, 0.7, 1.8, 6.0, 0.5, "Sustainability (next 12 months, honest choice)", size=19, color=GREEN, bold=True)
tb(s, 0.7, 2.35, 6.0, 2.7,
   "●  A public-good product: permanently free, no paywall,\n"
   "     self-hostable, no commercialization ever (including long term)\n"
   "●  Positioning: open, recomputable work + handover kit\n"
   "     (we do not claim \"public infrastructure\")\n"
   "●  Funding: no external funds beyond one-off prize money;\n"
   "     public-good stance is permanently consistent with the\n"
   "     Open-Meteo non-commercial license\n"
   "●  Cost model: zero-server static site; pilot cash capped at ¥3,400,\n"
   "     labor 85–95 h — overspending triggers & exit triggers pre-written", size=13)
tb(s, 7.0, 1.8, 5.7, 0.5, "Scaling & single-point risks", size=19, color=GREEN, bold=True)
tb(s, 7.0, 2.35, 5.7, 2.7,
   "●  Same-climate citrus areas = weak transfer (still needs\n"
   "     local calibration)\n"
   "●  Cross-crop (e.g. tea) = strong calibration, 5–15 person-days/area\n"
   "●  Only Danjiangkou in the next 12 months\n"
   "●  Bus factor = 1: no successor committed yet, so we do not\n"
   "     call this \" dependable public infrastructure\"", size=13)
tb(s, 0.7, 5.35, 12, 1.3,
   "Safety boundary: risk levels assist farm planning only — not disaster warnings, and no substitute for official meteorological warnings or professional guidance; "
   "reanalysis/forecast data carry uncertainty — combine with local weather authorities for major decisions.\n"
   "Ethics: no personal identity collected; pilot evidence (screenshots/photos) is de-identified and stored locally; only aggregates published; participants may withdraw at any time.", size=13, color=RED)

# S6.5 Uncertainty
s = add_slide()
header(s, "06", "Uncertainty Stated Upfront (Grid Sensitivity Measured)")
tb(s, 0.7, 1.75, 12, 1.0,
   "Same coordinates, four distinct grid cells: in 2022, 54 of 122 days (44.3%) disagreed on the highest of the four risk levels. "
   "A ~9km grid reanalysis is not an orchard measurement — this test ships in the repo, recomputable by anyone.", size=15)
tb(s, 0.7, 2.9, 6.0, 0.5, "Known uncertainties & how we handle them", size=19, color=GREEN, bold=True)
tb(s, 0.7, 3.45, 6.0, 2.7,
   "●  ERA5-Land is a ~9km grid — biases vs on-orchard reality\n"
   "     → per-orchard calibration in the pilot; levels labeled with grid scope\n"
   "●  7-day forecast error grows with lead time\n"
   "     → beyond day 8, levels are capped at \"watch\"\n"
   "●  Drought thresholds not yet checked against local soil moisture\n"
   "     → field soil-moisture cross-check is a mandatory pilot record\n"
   "●  Forecast skill never backtested\n"
   "     → we claim no forecast skill, only risk-window hints", size=13)
tb(s, 7.0, 3.45, 5.7, 2.7,
   "Honestly stated, never claimed:\n"
   "●  The 2022 backtest is one event at one grid point —\n"
   "     not a general accuracy rate\n"
   "●  Drought \"severe\" discrimination is the weakest named claim\n"
   "●  No user interviews yet; no loss-reduction data (pilot answers)\n"
   "●  Every level prints rule version + calibration/holdout years —\n"
   "     screenshots trace to a specific rule version", size=13)
tb(s, 0.7, 6.35, 12, 0.6,
   "Principle: external actions fail closed (never pushed without confirmation); no on-site commands; levels assist farm planning only.", size=13, color=RED)

# S6.6 Communication
s = add_slide()
header(s, "07", "Communication: Turning Expert Levels Into Wall-Ready Advice")
shots2 = [("img/dashboard.png", "① Dashboard: four risks on one screen"),
          ("img/calendar.png", "② 97-day calendar: one color-coded cell per day"),
          ("img/advice-export.png", "③ Report: printable for the cooperative board")]
for i, (path, cap) in enumerate(shots2):
    x = 0.7 + i * 4.16
    pic = s.shapes.add_picture(path, Inches(x), Inches(1.8), Inches(3.62), Inches(2.04))
    pic.line.color.rgb = GREEN2; pic.line.width = Pt(1)
    tb(s, x, 3.9, 3.62, 0.6, cap, size=11, color=MUTED)
tb(s, 0.7, 4.55, 6.0, 0.5, "Communication chain (already live)", size=19, color=GREEN, bold=True)
tb(s, 0.7, 5.1, 6.0, 1.8,
   "●  Complex climate info → four color levels + one-line trigger\n"
   "●  Report/CSV generated locally in the page — no server\n"
   "●  Portrait phone layout, shareable in technician WeChat groups\n"
   "●  Smallholders benefit via nodes: print & post the report", size=13)
tb(s, 7.0, 5.1, 5.7, 1.8,
   "Public verifiability:\n"
   "●  Three historical replay scenes, openable on the spot\n"
   "●  Frozen inputs + open code — anyone recomputes offline\n"
   "●  175-second video + open repo + community post\n"
   "●  Page footer prints the rule string — traceable to rule version", size=13)

# S6.7 Pilot execution
s = add_slide()
header(s, "08", "90-Day Pilot: Weekly Rhythm & Pre-Registered Criteria")
tb(s, 0.7, 1.75, 6.0, 0.5, "Weekly rhythm (by citrus season)", size=19, color=GREEN, bold=True)
tb(s, 0.7, 2.3, 6.0, 3.1,
   "●  Weeks 1–2 (Nov): sign agreement · set blocks · record baseline\n"
   "●  Weeks 3–10: usage · weekly reports · biweekly threshold calibration\n"
   "●  Weeks 11–13 (Jan): revisit · review · final report\n"
   "●  Covers two risk processes: winter freeze (Nov–Dec) and\n"
   "     cold-snap window (Mar–Apr, tracked after the review)\n"
   "●  Every Friday: LLM usage screenshot logged (cap ¥100;\n"
   "     at ¥50 cumulative the kill-switch disables the key)", size=13)
tb(s, 7.0, 1.75, 5.7, 0.5, "Evidence rules (anti-self-confirmation)", size=19, color=GREEN, bold=True)
tb(s, 7.0, 2.3, 5.7, 3.1,
   "●  Adoption needs evidence: whitewash photos / purchase receipts /\n"
   "     irrigation records; timing from two specific dates\n"
   "●  n<10 → counts only, never percentage claims\n"
   "●  Replay-scene evaluation labeled \"intent/rehearsal\",\n"
   "     never merged with real adoption\n"
   "●  6 weeks zero activity or zero confirmation responses\n"
   "     → exit triggers: 90-day sunset banner + archive\n"
   "●  \"Request sent with delivery proof, still unsigned\" is the only\n"
   "     demand-side evidence that counts", size=13)
tb(s, 0.7, 5.75, 12, 0.9,
   "Pilot cash cap ¥3,400 (drops to ¥2,200 if compensation cannot be legitimately paid) + 85–95 project labor hours — "
   "all with pre-written overspending triggers and exit triggers; weekly ledger in docs/04, published at the review.", size=13, color=RED)

# S7 Team
s = add_slide(GREEN)
tb(s, 1, 2.4, 11.3, 1, "Canglangzhou", size=44, color=WHITE, bold=True)
tb(s, 1, 3.6, 11.3, 0.6, "Solo entry · October 2026", size=18, color=MINT)
tb(s, 1, 4.6, 11.3, 1.2,
   "Bring a real problem; let AI actually solve it.\nOne place. One climate problem. One AI solution.", size=20, color=WHITE)
tb(s, 1, 6.3, 11.3, 0.5, "ai4climate.world ｜ pawnnwap.github.io/danju-zhihan ｜ deepseek.club/topic/5043", size=13, color=MINT)

prs.save("Danju-Zhihan-PPT-en.pptx")
print("saved Danju-Zhihan-PPT-en.pptx")
