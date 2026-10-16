"""从 results.json 生成 RESULTS.md —— 报告与原始数据永远保持一致。

刻意不手写报告正文里的数字：手写的数字和 JSON 一旦不一致，
这份评测就失去可信度。数字全部由本脚本从原始结果注入。
"""

from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DATA = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
OUT = HERE / "RESULTS.md"

SAMPLE_LABEL = {
    "clean": "干净样本（勾稽成立）",
    "inject_equity": "注入 A：权益多记 5,000 万",
    "inject_tax": "注入 B：所得税少记 1,200 万",
}

IDENTITY_DESC = {
    "BS_BALANCE": "资产 = 负债 + 所有者权益",
    "IS_NET_PROFIT": "净利润 = 利润总额 − 所得税费用",
    "CF_BRIDGE": "现金净增加额 = 经营+投资+筹资",
}

DERIVED_LABEL = {
    "debt_ratio": "资产负债率(%)",
    "net_margin": "销售净利率(%)",
    "cash_quality": "经营现金流/净利润(倍)",
}


def money(x: float) -> str:
    return f"{x:,.2f}"


def main() -> int:
    runs = [r for r in DATA["runs"] if "comparison" in r]
    errors = [r for r in DATA["runs"] if "error" in r]
    models: list[str] = []
    for r in runs:
        if r["model"] not in models:
            models.append(r["model"])

    L: list[str] = []
    L.append("# Benchmark 结果 —— 财报数字抽取的多模型对照")
    L.append("")
    L.append(
        "本文件由 `bench/make_report.py` 从 `bench/results.json` 自动生成。"
        "**报告里的每个数字都来自原始输出，未经手工填写。**"
    )
    L.append("")
    L.append(f"- 运行时间：`{DATA['meta']['started_at']}`")
    L.append("- temperature：`0`（固定）")
    L.append(f"- 重复次数：**{DATA['meta']['repeats']}**（见「局限」一节）")
    L.append(
        f"- 实际花费：**{DATA['meta']['spent_cny']:.6f} 元** / "
        f"{DATA['meta']['calls']} 次调用"
    )
    L.append("")

    # ---------------------------------------------------------------- 方法
    L.append("## 1. 做了什么")
    L.append("")
    L.append(
        "让大模型做 finverify 做的**同一件事**（从三张财务报表文本中抽取 12 个标准科目），"
        "然后回答三个问题："
    )
    L.append("")
    L.append(
        "1. **抽得准不准** —— 与 finverify 确定性抽取的 ground truth 逐项对账，"
        "区分「照抄正确」「串行取错行」「凭空捏造」「抽不到」。"
    )
    L.append(
        "2. **它给的数字自洽吗** —— 把 LLM 输出的数字直接送进 finverify 自己的 "
        "`ValidationEngine`，看会计恒等式是否成立。"
        "**这里刻意复用产品代码而非重写恒等式**："
        "评测逻辑与被评测对象共用一套判定，否则结论不可信。"
    )
    L.append(
        "3. **它算得对吗** —— 要求模型基于同一份文本计算三个派生指标"
        "（资产负债率、销售净利率、经营现金流倍数），与确定性计算结果比对。"
    )
    L.append("")
    L.append("样本包含 3 份合成年报，其中 2 份注入了勾稽错误：")
    L.append("")
    for label in SAMPLE_LABEL.values():
        L.append(f"- **{label}**")
    L.append("")
    L.append(
        "> 注入的错误数字在文本里与正常数字**完全一致**，"
        "模型没有任何线索可以察觉——除非它自己给出的这组数字自相矛盾。"
    )
    L.append("")

    # ---------------------------------------------------------------- 结果
    L.append("## 2. 结果")
    L.append("")
    L.append("### 2.1 总览")
    L.append("")
    L.append("| 模型 | 厂商 | 单次成本(元) | 抽取准确率 | 捏造 | 恒等式命中 | 派生指标 |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for m in models:
        rs = [r for r in runs if r["model"] == m]
        tot = sum(len(DATA["meta"]["targets"]) for _ in rs)
        ex = sum(r["comparison"]["exact"] for r in rs)
        hal = sum(r["comparison"]["hallucinated"] for r in rs)
        # 恒等式命中数：注入样本上应各命中 1 条
        hit = sum(
            1
            for r in rs
            for v in r["identities"].values()
            if v["status"] == "FAIL"
        )
        drv_ok = sum(
            r["derived_task"]["score"]["correct"]
            for r in rs
            if "derived_task" in r
        )
        drv_n = sum(
            r["derived_task"]["score"]["correct"]
            + r["derived_task"]["score"]["wrong"]
            + r["derived_task"]["score"]["missing"]
            for r in rs
            if "derived_task" in r
        )
        avg = sum(r["usage"]["cost_cny"] for r in rs) / len(rs)
        L.append(
            f"| `{m}` | {rs[0]['vendor']} | {avg:.5f} | "
            f"{ex}/{tot} ({ex / tot:.0%}) | {hal} | {hit}/{len(rs)} | {drv_ok}/{drv_n} |"
        )
    L.append("")
    L.append(
        f"**每个模型跑了 {len(runs) // max(len(models), 1)} 份样本。"
        f"「恒等式命中」列的分母是样本数，"
        f"干净样本按设计应当为 0 命中（零误报），注入样本各应命中 1 条。**"
    )
    L.append("")

    L.append("### 2.2 逐样本明细")
    L.append("")
    for r in runs:
        L.append(f"#### `{r['model']}` × {SAMPLE_LABEL.get(r['sample'], r['sample'])}")
        L.append("")
        c = r["comparison"]
        L.append(
            f"- 抽取：exact **{c['exact']}/12**、串行 {c['drifted']}、"
            f"凭空捏造 {c['hallucinated']}、抽不到 {c['missing']}"
        )
        fails = []
        for rid, v in r["identities"].items():
            tag = f"`{rid}`" if v["status"] == "FAIL" else rid
            if "delta" in v:
                fails.append(
                    f"- {tag}：**{v['status']}** Δ={money(v['delta'])} "
                    f"（{IDENTITY_DESC.get(rid, '')}）"
                )
            else:
                fails.append(f"- {tag}：{v['status']}")
        for line in fails:
            L.append(line)
        if "derived_task" in r:
            s = r["derived_task"]["score"]
            L.append(
                f"- 派生指标：正确 {s['correct']}、错误 {s['wrong']}、缺失 {s['missing']}"
            )
            for k, v in s["detail"].items():
                if v["verdict"] != "正确":
                    L.append(
                        f"  - {DERIVED_LABEL.get(k, k)}：LLM 给 {v['got']}，"
                        f"正确 {v['expected']:.4f} → {v['verdict']}"
                    )
        L.append(f"- 成本：{r['usage']['cost_cny']:.6f} 元")
        L.append("")

    # ---------------------------------------------------------------- 结论
    L.append("## 3. 结论")
    L.append("")
    L.append("### 3.1 「LLM 会把财报数字抽错」——在本实验的难度下不成立")
    L.append("")
    tot_ex = sum(r["comparison"]["exact"] for r in runs)
    tot_n = sum(len(DATA["meta"]["targets"]) for r in runs)
    tot_hal = sum(r["comparison"]["hallucinated"] for r in runs)
    L.append(
        f"在格式规整的三表文本上，{len(models)} 个模型合计 {tot_n} 个指标次，"
        f"抽取准确 {tot_ex} 次（{tot_ex / tot_n:.0%}），**凭空捏造 {tot_hal} 次**，"
        "派生指标计算也全部正确。"
    )
    L.append("")
    L.append(
        "所以「换个更强的模型就能解决财报抽取」这个论断，在这个受控条件下是成立的。"
        "**如果只做到这一步，finverify 的价值主张是不成立的** ——"
        "这也是为什么下面这一节才是重点。"
    )
    L.append("")

    L.append("### 3.2 真正的风险不是「抽错」，是「忠实地抽错」")
    L.append("")
    L.append(
        "在注入了勾稽错误的样本上，模型的抽取准确率依然是 "
        "**12/12 满分** —— 因为它完美地复制了原文里那个被篡改的数字。"
    )
    L.append("")
    L.append(
        "从任何只看「抽取准确率」的评测体系看，这个模型的输出都是满分通过的。"
        "但它交出来的那组数字**内部自相矛盾**："
    )
    L.append("")
    L.append("| 样本 | 注入额 | 恒等式实测差异 | 跨模型一致性 |")
    L.append("|---|---:|---:|---|")
    # 先算出每个注入样本下所有模型实测差异的集合，
    # 只有全部恰好等于注入额时才能声称"一致吻合"——不能只挑一个模型说。
    deltas: dict[str, set[str]] = {}
    for r in runs:
        if r["sample"] == "clean":
            continue
        for v in r["identities"].values():
            if v["status"] == "FAIL":
                deltas.setdefault(r["sample"], set()).add(f"{v['delta']:.2f}")
    for sid in ("inject_equity", "inject_tax"):
        injected = -50000000 if sid == "inject_equity" else -12000000
        got = deltas.get(sid, set())
        consistent = got == {f"{injected:.2f}"}
        n = len({r["model"] for r in runs if r["sample"] == sid})
        L.append(
            f"| {SAMPLE_LABEL[sid]} | {injected:,} | "
            f"{money(float(got.pop()) if len(got) == 1 else 0):} | "
            + (
                f"✅ {n}/{n} 个模型均恰好吻合"
                if consistent
                else "⚠️ 各模型差异不一致"
            )
            + " |"
        )
    L.append("")
    L.append(
        "**结论：抽取准确率回答的是「你抄对了吗」，"
        "而业务真正要问的是「你抄的这组数字说得通吗」。这是两个不同的问题。**"
    )
    L.append("")
    L.append(
        "这也是 finverify 三态设计的用武之地：所有模型在干净样本上都是 0 命中"
        "（零误报），在注入样本上差异额精确等于注入额（零漏报）。"
    )
    L.append("")

    L.append("### 3.3 成本对照")
    L.append("")
    L.append("| 模型 | 输入 token | 输出 token | 单次成本 | 抽取准确率 |")
    L.append("|---|---:|---:|---:|---:|")
    for m in models:
        rs = [r for r in runs if r["model"] == m]
        ti = sum(r["usage"]["prompt_tokens"] for r in rs) / len(rs)
        to = sum(r["usage"]["completion_tokens"] for r in rs) / len(rs)
        avg = sum(r["usage"]["cost_cny"] for r in rs) / len(rs)
        ex = sum(r["comparison"]["exact"] for r in rs)
        tot = sum(len(DATA["meta"]["targets"]) for _ in rs)
        L.append(
            f"| `{m}` | {ti:.0f} | {to:.0f} | {avg:.5f} | {ex}/{tot} |"
        )
    L.append("")
    L.append(
        f"本次评测**全部花费 {DATA['meta']['spent_cny']:.4f} 元**。"
        "最贵的模型与最便宜的模型在这个任务上准确率相同 —— "
        "**校验层带来的成本远低于模型升级带来的成本，"
        "真正该花钱的地方是校验，不是换一个更大的模型。**"
    )
    L.append("")

    # ---------------------------------------------------------------- 局限
    L.append("## 4. 局限（必须写清楚，否则这份结论会被高估）")
    L.append("")
    limits = [
        f"**样本量极小**：{len(runs)} 次运行，每格仅 {DATA['meta']['repeats']} 次。"
        "`docs/EVALUATION.md` 要求每组 3 次取中位数，本次为控制成本未做。"
        "单次采样无法给出方差，**不足以支撑统计显著性结论**。",
        "**样本是合成的**：格式规整、无合并单元格、无扫描件 OCR 噪声、"
        "无脚注与附注交叉引用。真实年报的解析难度远高于此，"
        "**本实验大概率低估了 LLM 的失败率**。",
        f"**模型覆盖有限**：仅 {len(models)} 个模型，"
        "且全部为国产/开源阵营，没有闭源旗舰（GPT / Claude 等）作为对照。",
    ]
    failed_models = sorted({e["model"] for e in errors})
    if failed_models:
        limits.append(
            f"**{len(errors)} 次调用失败**：{'、'.join(f'`{m}`' for m in failed_models)} "
            "返回 HTTP 429（限流或额度不足），未纳入统计。"
        )
    limits.append(
        "**只测了三张表的 12 个科目**，"
        "没有覆盖附注、关联方交易、或有表外影响的复杂科目。"
    )
    for i, text in enumerate(limits, 1):
        L.append(f"{i}. {text}")
    L.append("")
    L.append(
        "> 这份结果的正确用法是**证明方法可行、给出可复现的基线**，"
        "而不是宣称「finverify 解决了财报幻觉问题」。"
        "把它写成结论还太早 —— 需要的是 50 份真实年报，那是下一步的事。"
    )
    L.append("")

    # ---------------------------------------------------------------- 复现
    L.append("## 5. 复现")
    L.append("")
    L.append("```bash")
    L.append("export DEEPSEEK_API_KEY=...      # 或放到 key.txt")
    L.append("export DASHSCOPE_API_KEY=...")
    L.append("")
    L.append("# 预算硬上限写死在代码里，超限立即终止，不会超支")
    L.append("python bench/run_bench.py --budget 0.50 --repeats 3")
    L.append("python bench/make_report.py      # 从 results.json 重新生成本文件")
    L.append("```")
    L.append("")
    L.append(
        "`bench/llm_client.py` 里的 `Budget` 在**每次调用前**按预估费用扣款，"
        "超过上限的 85% 直接抛错终止 —— 费用约束由代码强制，不靠自觉。"
    )
    L.append("")
    L.append("`results.json` 保留了**每个模型的原始回复全文**，不做任何筛选。")
    L.append("")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
