"""多模型财务数字抽取对照评测 —— 主运行器。

跑法：
    python bench/run_bench.py --budget 0.30 --samples 3 --models deepseek-chat

产物：
    bench/results.json   原始输出（含模型原始回复，满足可复现要求）
    bench/RESULTS.md     分析报告
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from llm_client import Budget, build_registry, call_llm  # noqa: E402

from finverify import MetricKind  # noqa: E402
from finverify.ingestion import load_document  # noqa: E402
from finverify.pipeline import audit  # noqa: E402
from finverify.samples import AnnualReportData, build_sample_pdf  # noqa: E402
from finverify.schemas import (  # noqa: E402
    EvidenceQuality,
    EvidenceSpan,
    FinancialMetric,
    StatementKind,
    VerifiedNumber,
)
from finverify.validation import ValidationEngine  # noqa: E402

_BS_KINDS = {
    MetricKind.TOTAL_ASSETS,
    MetricKind.TOTAL_LIABILITIES,
    MetricKind.TOTAL_EQUITY,
}
_IS_KINDS = {
    MetricKind.REVENUE,
    MetricKind.OPERATING_COST,
    MetricKind.TOTAL_PROFIT,
    MetricKind.INCOME_TAX,
    MetricKind.NET_PROFIT,
}


def _statement_of(kind: MetricKind) -> StatementKind:
    if kind in _BS_KINDS:
        return StatementKind.BALANCE_SHEET
    if kind in _IS_KINDS:
        return StatementKind.INCOME_STATEMENT
    return StatementKind.CASH_FLOW

SAMPLES = ROOT / "samples"
OUT_JSON = pathlib.Path(__file__).resolve().parent / "results.json"

# 需要 LLM 抽取的指标（与 finverify 抽取层覆盖范围一致）
TARGETS = [
    MetricKind.TOTAL_ASSETS,
    MetricKind.TOTAL_LIABILITIES,
    MetricKind.TOTAL_EQUITY,
    MetricKind.REVENUE,
    MetricKind.OPERATING_COST,
    MetricKind.TOTAL_PROFIT,
    MetricKind.INCOME_TAX,
    MetricKind.NET_PROFIT,
    MetricKind.OPERATING_CASH_FLOW,
    MetricKind.INVESTING_CASH_FLOW,
    MetricKind.FINANCING_CASH_FLOW,
    MetricKind.NET_CASH_INCREASE,
]

PROMPT_TEMPLATE = """以下是一份企业年度报告节选中的三张财务报表（单位：元）。

{table_text}

请从上述内容中抽取以下科目的期末金额，**逐字依据原文，不要计算、不要推算、
不要补充原文没有的数字**。以 JSON 输出，键为指标英文名，值为数字：

{keys}

只输出 JSON，不要解释。若某科目在原文中找不到，该键的值填 null。"""

# --------------------------------------------------------------------------
# 任务 B：派生指标计算
#
# 任务 A（纯抽取）里模型可以逐字照抄，所以很难出错。
# 任务 B 要求模型**做算术** —— 这是 LLM 的已知弱区，
# 也是"把计算交给确定性代码"这一主张真正需要被证伪/证实的场景。
# --------------------------------------------------------------------------
DERIVED_SPECS = [
    ("debt_ratio", "资产负债率(%)", ("total_liabilities",), ("total_assets",)),
    ("net_margin", "销售净利率(%)", ("net_profit",), ("revenue",)),
    (
        "cash_quality",
        "经营现金流/净利润(倍)",
        ("operating_cash_flow",),
        ("net_profit",),
    ),
]

PROMPT_DERIVED = """以下是一份企业年度报告节选中的三张财务报表（单位：元）。

{table_text}

请**依据上述数字计算**以下三个派生指标，以 JSON 输出（值为数字，不要带百分号）：

- debt_ratio: 资产负债率 = 负债合计 / 资产总计 * 100
- net_margin: 销售净利率 = 净利润 / 营业收入 * 100
- cash_quality: 经营现金流倍数 = 经营活动产生的现金流量净额 / 净利润

请保留 4 位小数。只输出 JSON，不要解释过程。"""


def compute_derived(truth: dict[str, float]) -> dict[str, float]:
    """用确定性代码算同样的三个指标 —— 这是 finverify 路线的对照组。"""
    out = {}
    for key, _label, num_keys, den_keys in DERIVED_SPECS:
        num = truth.get(num_keys[0])
        den = truth.get(den_keys[0])
        if num is None or den in (None, 0):
            continue
        ratio = num / den
        out[key] = ratio * 100 if key != "cash_quality" else ratio
    return out


def score_derived(
    llm: dict[str, float | None], expected: dict[str, float]
) -> dict:
    """逐项比对派生指标。

    容差按量级给：比率类指标算术滑一位（100% vs 10%）是最常见的错法，
    所以用「相对误差是否超过 2%」而不是绝对阈值。
    """
    res = {"correct": 0, "wrong": 0, "missing": 0, "detail": {}}
    for key, _label, _n, _d in DERIVED_SPECS:
        got = llm.get(key)
        exp = expected.get(key)
        if got is None or exp is None:
            res["missing"] += 1
            res["detail"][key] = {"got": got, "expected": exp, "verdict": "缺失"}
            continue
        rel = abs(got - exp) / max(abs(exp), 1e-9)
        if rel <= 0.02:
            res["correct"] += 1
            verdict = "正确"
        else:
            res["wrong"] += 1
            verdict = f"错误(相对误差 {rel:.1%})"
        res["detail"][key] = {
            "got": got,
            "expected": exp,
            "verdict": verdict,
        }
    return res

IDENTITIES = [
    # 仅用于报告可读性；实际判定完全交给 finverify 自己的 ValidationEngine，
    # 避免在 benchmark 里重写一遍恒等式（第一版就因为漏了负号把
    # "利润总额 - 所得税" 写成了加法，得出"所有模型自相矛盾"的假结论）。
    ("BS_BALANCE", "资产 = 负债 + 所有者权益"),
    ("IS_NET_PROFIT", "净利润 = 利润总额 − 所得税费用"),
    ("CF_BRIDGE", "现金净增加额 = 经营 + 投资 + 筹资活动现金流量净额"),
]


def check_identities(numbers: dict[str, float | None]) -> dict:
    """把 LLM 自己给的数字交给 finverify 的校验引擎判定。

    这是本评测最关键的一问：**这组数字内部自洽吗？**
    与 finverify 抽取的数字是否一致无关 —— 只看 LLM 自己说不说圆。

    注意这里刻意复用产品代码而不是重新实现恒等式：
    benchmark 与被评测对象共用一套判定逻辑，结果才可信。
    """
    metrics = []
    for kind in TARGETS:
        val = numbers.get(kind.value)
        if val is None:
            continue  # 缺失就不构造 —— 让引擎给出 REVIEW_REQUIRED
        span = EvidenceSpan(
            page=1,
            bbox=(0.0, 0.0, 1.0, 1.0),
            quality=EvidenceQuality.DIRECT_QUOTE,
            snippet=f"{kind.value} {val:,.2f}",
        )
        metrics.append(
            FinancialMetric(
                kind=kind,
                label=kind.value,
                number=VerifiedNumber(
                    value=val, unit="元", period="2023", evidence=span
                ),
                statement=_statement_of(kind),
            )
        )

    out = {}
    for r in ValidationEngine().validate(metrics, "2023"):
        entry = {"status": r.status.value, "desc": r.detail}
        if r.delta is not None:
            entry["delta"] = r.delta
        out[r.rule_id] = entry
    return out

SAMPLE_SPECS = [
    ("clean", AnnualReportData(), "三表勾稽全部成立"),
    (
        "inject_equity",
        AnnualReportData(
            injected_error=(MetricKind.TOTAL_EQUITY, 4_965_000_000.00)
        ),
        "所有者权益被多记 5,000 万 —— 破坏资产恒等式",
    ),
    (
        "inject_tax",
        AnnualReportData(
            injected_error=(MetricKind.INCOME_TAX, 79_800_000.00)
        ),
        "所得税被少记 1,200 万 —— 破坏净利润恒等式",
    ),
]


def table_text(pdf: pathlib.Path) -> str:
    """把表格转成纯文本。这是喂给 LLM 的**唯一**输入。"""
    doc = load_document(pdf)
    lines = []
    for pt in doc.tables:
        for r in pt.rows:
            vals = [v for v in r.values if v]
            if vals:
                lines.append(" | ".join(vals))
    return "\n".join(lines)


def ground_truth(pdf: pathlib.Path) -> dict[str, float]:
    report = audit(pdf, period="2023")
    return {m.kind.value: m.number.value for m in report.metrics}


def parse_llm_json(text: str) -> dict[str, float | None]:
    """从 LLM 回复里抠出 JSON。容忍 ```json 包裹和前后废话。"""
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    payload = fence.group(1) if fence else text
    start = payload.find("{")
    end = payload.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(payload[start : end + 1])
    except json.JSONDecodeError:
        return {}
    out: dict[str, float | None] = {}
    for k, v in data.items():
        key = k.strip().lower()
        if isinstance(v, (int, float)):
            out[key] = float(v)
        elif isinstance(v, str):
            cleaned = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
            out[key] = float(cleaned) if cleaned else None
        else:
            out[key] = None
    return out


def compare(
    llm: dict[str, float | None],
    truth: dict[str, float],
    source_values: set[float],
) -> dict:
    """逐指标对账。

    分类：
      exact        —— 与原文一致
      drifted      —— 有值但与原文不符，且该值确实存在于原文（串行/取错行）
      hallucinated —— 有值但原文里根本没有这个数字（凭空捏造）
      missing      —— 抽不出来
    """
    res = {
        "exact": 0,
        "drifted": 0,
        "hallucinated": 0,
        "missing": 0,
        "detail": {},
    }
    for kind in TARGETS:
        key = kind.value
        got = llm.get(key)
        exp = truth.get(key)
        if got is None or exp is None:
            cat = "missing"
        elif abs(got - exp) < 0.01:
            cat = "exact"
        elif any(abs(got - sv) < 0.01 for sv in source_values):
            cat = "drifted"
        else:
            cat = "hallucinated"
        res[cat] += 1
        if cat != "exact":
            res["detail"][key] = {
                "category": cat,
                "llm": got,
                "truth": exp,
            }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0.30, help="硬上限（元）")
    ap.add_argument("--models", default="", help="逗号分隔，留空则用全部")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    budget = Budget(limit_cny=args.budget)
    registry = build_registry()
    if args.models:
        want = {m.strip() for m in args.models.split(",") if m.strip()}
        registry = [m for m in registry if m.name in want]

    if not registry:
        print("没有可用模型")
        return 1

    print(f"预算上限: {args.budget} 元（实际可用 {args.budget * 0.85:.4f}）")
    print(f"参与模型: {', '.join(m.name for m in registry)}")
    print(f"样本数: {args.samples}   重复: {args.repeats}")
    print()

    # 预生成样本 PDF
    prepared = []
    for sid, data, note in SAMPLE_SPECS[: args.samples]:
        pdf = SAMPLES / f"bench_{sid}.pdf"
        build_sample_pdf(pdf, data)
        prepared.append(
            {
                "id": sid,
                "note": note,
                "pdf": pdf,
                "text": table_text(pdf),
                "truth": ground_truth(pdf),
            }
        )

    keys_block = "\n".join(f"- {k.value}" for k in TARGETS)
    results = {
        "meta": {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "budget_limit_cny": args.budget,
            "repeats": args.repeats,
            "targets": [k.value for k in TARGETS],
            "finverify_version": "0.3.0",
        },
        "runs": [],
    }

    for spec in registry:
        for sample in prepared:
            source_values = {
                float(re.sub(r"[^\d.]", "", line.split("|")[-1]))
                for line in sample["text"].splitlines()
                if line and re.search(r"\d", line.split("|")[-1])
            }
            prompt = PROMPT_TEMPLATE.format(
                table_text=sample["text"], keys=keys_block
            )
            for rep in range(args.repeats):
                try:
                    raw, usage = call_llm(spec, prompt, budget)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{spec.name}/{sample['id']}] 失败: {exc}")
                    results["runs"].append(
                        {
                            "model": spec.name,
                            "vendor": spec.vendor,
                            "sample": sample["id"],
                            "repeat": rep,
                            "error": str(exc),
                        }
                    )
                    continue

                numbers = parse_llm_json(raw)
                cmp = compare(numbers, sample["truth"], source_values)
                ident = check_identities(numbers)

                results["runs"].append(
                    {
                        "model": spec.name,
                        "vendor": spec.vendor,
                        "sample": sample["id"],
                        "sample_note": sample["note"],
                        "repeat": rep,
                        "usage": usage,
                        "raw_reply": raw,  # 原始输出照发，禁止只留最优
                        "parsed": numbers,
                        "comparison": cmp,
                        "identities": ident,
                    }
                )
                bad = [
                    k
                    for k, v in ident.items()
                    if v["status"] == "FAIL"
                ]
                print(
                    f"  [{spec.name:<16}/{sample['id']:<13}] "
                    f"A抽取 exact={cmp['exact']}/12 捏造={cmp['hallucinated']} "
                    f"串行={cmp['drifted']} 缺={cmp['missing']} | "
                    f"恒等式FAIL={len(bad)}/3 "
                    f"(${usage['cost_cny']:.5f}, 累计 ${budget.spent_cny:.5f})"
                )

                # ---- 任务 B：派生指标计算 ----
                prompt_d = PROMPT_DERIVED.format(table_text=sample["text"])
                try:
                    raw_d, usage_d = call_llm(spec, prompt_d, budget)
                    derived = parse_llm_json(raw_d)
                    expected_d = compute_derived(sample["truth"])
                    score = score_derived(derived, expected_d)
                    results["runs"][-1]["derived_task"] = {
                        "usage": usage_d,
                        "raw_reply": raw_d,
                        "parsed": derived,
                        "deterministic": expected_d,
                        "score": score,
                    }
                    print(
                        f"  [{'':<16} {'':<13}] "
                        f"B计算 对={score['correct']}/3 错={score['wrong']} "
                        f"缺={score['missing']}  "
                        f"(${usage_d['cost_cny']:.5f}, 累计 ${budget.spent_cny:.5f})"
                    )
                    for k, v in score["detail"].items():
                        if v["verdict"] != "正确":
                            print(
                                f"        {k}: LLM={v['got']} "
                                f"正确={v['expected']:.4f} -> {v['verdict']}"
                            )
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{spec.name:<16}/{sample['id']:<13}] B任务失败: {exc}")

    results["meta"]["spent_cny"] = round(budget.spent_cny, 8)
    results["meta"]["calls"] = budget.calls
    OUT_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"总花费: {budget.spent_cny:.6f} 元 / {budget.calls} 次调用")
    print(f"剩余预算: {budget.remaining:.6f} 元")
    print(f"结果已写入: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
