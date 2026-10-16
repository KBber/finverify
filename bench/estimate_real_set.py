"""50 份真实年报评测集的成本估算。

单价全部来自实测（见 bench/results.json），不是查价目表。
真实年报的 token 规模是**假设**，集中定义在 SCENARIOS 里，方便按实际情况调整。
输出写到同目录 estimate_real_set.txt（UTF-8），避免控制台编码问题。
"""

from __future__ import annotations

import pathlib

_CAPTURED: list[str] = []


def emit(s: str = "") -> None:
    _CAPTURED.append(s)


# ---------------------------------------------------------------- 实测单价
# 来源：bench/results.json，18 次真实调用
MEASURED = [
    # 模型, 输入价(元/百万), 输出价(元/百万), 实测输入tok, 实测输出tok, 实测花费元
    ("deepseek-chat", 2.0, 8.0, 415, 175, 0.00223),
    ("deepseek-reasoner", 1.0, 2.0, 441, 670, 0.00178),
    ("qwen-plus", 0.8, 2.0, 524, 235, 0.00089),
]

# ------------------------------------------------------- 真实年报 token 假设
# 合成样本只有 3 张表约 20 行；真实年报三表往往跨页、带表头单位与脚注。
SCENARIOS = {
    "精简（只喂三表文本）": (2000, 400),
    "全量（喂三表+附注节选）": (15000, 500),
    "视觉（扫描件逐页读图）": (12000, 500),
}

REPORTS = 50
REPEATS = 3
MODELS = len(MEASURED)
INJECT_TYPES = 6  # docs/EVALUATION.md：25 份 × 6 种注入 = 150 个注入幻觉

BAR = "=" * 74

emit(BAR)
emit("一、单价模型自校验（用实测数据反推，确认公式无误）")
emit(BAR)
for name, pin, pout, ti, to, actual in MEASURED:
    calc = ti / 1e6 * pin + to / 1e6 * pout
    ok = "OK" if abs(calc - actual) / actual < 0.02 else "偏差!"
    emit(
        f"  {name:<18} {ti:>5}in + {to:>4}out   "
        f"公式算 {calc:.5f}   实测 {actual:.5f}   {ok}"
    )

emit()
emit(BAR)
emit("二、LLM 调用费用")
emit(BAR)
emit("  注意：注入测试不需要 LLM —— 只有「模型抽取准确率对比」才需要。")
emit(f"  调用量 = {REPORTS} 份 × {MODELS} 模型 × {REPEATS} 重复 = "
     f"{REPORTS * MODELS * REPEATS} 次")
emit()
for label, (tin, tout) in SCENARIOS.items():
    total = 0.0
    detail = []
    for name, pin, pout, *_ in MEASURED:
        per = tin / 1e6 * pin + tout / 1e6 * pout
        detail.append(f"{name} {per:.4f}")
        total += per * REPORTS * REPEATS
    emit(f"  {label}")
    emit(f"    单次成本: {', '.join(detail)}")
    emit(f"    小计: {total:.2f} 元")
    emit()

emit(BAR)
emit("三、注入测试的成本 —— 零")
emit(BAR)
emit(f"  {INJECT_TYPES} 种注入 × 25 份 = {INJECT_TYPES * 25} 个注入样本，")
emit("  全部由 finverify 确定性校验引擎检出，不调用任何 LLM。")
emit("  这一项为 0.00 元 —— 这正是确定性架构相对 LLM-as-judge 的核心优势：")
emit("  评测成本不随样本量线性增长。")
emit()

emit(BAR)
emit("四、真正的瓶颈不是钱")
emit(BAR)
work = [
    ("样本挑选（行业/规模/格式分层抽样）", "2-3", "人工，不可外包"),
    ("批量下载与格式筛选", "0.5", "自动化为主"),
    ("* 解析器适配真实年报", "20-40", "最大技术风险，见结论"),
    ("人工标注基线（10 份 x 双人）", "10-15", "人工，无法用钱解决"),
    ("标注一致性校验（Kappa + 仲裁）", "2-3", "人工"),
    ("评测跑批与结果分析", "2-3", "自动化 + 人工"),
    ("文档撰写与复现脚本", "3-5", "人工"),
]
lo = hi = 0.0
for name, est, kind in work:
    a, _, b = est.partition("-")
    lo += float(a)
    hi += float(b or a)
    emit(f"  {name:<34} {est:>6} h   {kind}")
emit(f"  {'':<34} {'-' * 9}")
emit(f"  {'合计':<34} {f'{lo:.0f}-{hi:.0f} h':>9}")

emit()
emit(BAR)
emit("五、分级方案")
emit(BAR)
emit(
    f"  {'方案':<12}{'年报':>5}{'标注':>5}{'模型':>5}"
    f"{'工时':>11}{'API费':>10}   用途"
)
plans = [
    ("L0 探路", 1, 0, 1, "1-2 h", "<0.1 元", "看解析器能不能读出真实年报"),
    ("L1 最小可信", 5, 2, 2, "8-12 h", "0.5-1 元", "够讲清楚方法论"),
    ("L2 求职可用", 20, 10, 3, "25-35 h", "2-5 元", "分层抽样 + 双人标注"),
    ("L3 原始设计", 50, 10, 3, "40-70 h", "5-15 元", "EVALUATION.md 完整版"),
]
for n, r, a, m, h, c, use in plans:
    emit(f"  {n:<12}{r:>5}{a:>5}{m:>5}{h:>11}{c:>10}   {use}")

emit()
emit(BAR)
emit("六、结论")
emit(BAR)
emit("  API 费用 0.1 - 15 元。比预想的低得多，不是瓶颈。")
emit("  人工时间 40 - 70 小时。这才是瓶颈。")
emit()
emit("  其中解析器适配占 20-40 h，且不确定性最高：")
emit("  合成年报是理想表格；真实年报有合并单元格、跨页续表、")
emit("  表头带「万元」单位、脚注编号、括号负数、科目名带序号。")
emit("  现在无法断言要多久 —— 这是估算里最大的未知数。")
emit()
emit("  建议：先花 1-2 小时做 L0 探路，用 1 份真实年报把解析器的")
emit("  真实失败率测出来。没有这个数据，上面的工时全是猜的。")

_OUT = pathlib.Path(__file__).with_name("estimate_real_set.txt")
_OUT.write_text("\n".join(_CAPTURED), encoding="utf-8")
print(f"written -> {_OUT}")
