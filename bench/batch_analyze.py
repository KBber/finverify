"""批量失败率分析 —— 量化真实年报上到底卡在哪一环。

对每份年报回答三个问题：

1. **口径识别成功了吗** —— 找没找到合并/母公司三表标题
2. **指标抽出来了吗** —— 11 个标准科目的命中率
3. **表格切列破坏了多少科目名** —— 这是当前的主要阻塞点

关键判断（决定修复方向）：
> 标签在**表格单元格**里被切碎了，那它在**页面文本流**里完整吗？
> 若文本流完整 -> 可以用文本流重建标签，修复成本低；
> 若文本流也是碎的 -> 说明是 PDF 本身的排版问题，得换解析策略。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify import MetricKind  # noqa: E402
from finverify.ingestion import all_text_lines, load_document  # noqa: E402
from finverify.pipeline import audit  # noqa: E402
from finverify.scope import ScopeKind, detect_scopes_cached  # noqa: E402

DATA = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                    else ROOT.parent / "data" / "real")

EXPECTED = [
    MetricKind.TOTAL_ASSETS,
    MetricKind.TOTAL_LIABILITIES,
    MetricKind.TOTAL_EQUITY,
    MetricKind.REVENUE,
    MetricKind.TOTAL_PROFIT,
    MetricKind.INCOME_TAX,
    MetricKind.NET_PROFIT,
    MetricKind.OPERATING_CASH_FLOW,
    MetricKind.INVESTING_CASH_FLOW,
    MetricKind.FINANCING_CASH_FLOW,
    MetricKind.NET_CASH_INCREASE,
]

# 科目名 -> 常见写法正则（用于在文本流里找完整标签）
LABEL_PATTERNS = {
    "total_assets": r"资产总计|资产合计",
    "total_liabilities": r"负债合计|负债总计",
    "total_equity": r"(所有者|股东)权益.{0,8}(合|总)计|所有者权益\(或股东权益\)合计",
    "revenue": r"营业总?收入",
    "total_profit": r"利润总额",
    "income_tax": r"所得税费用",
    "net_profit": r"净利润",
    "operating_cash_flow": r"经营活动产生的现金流量净额",
    "investing_cash_flow": r"投资活动产生的现金流量净额",
    "financing_cash_flow": r"筹资活动产生的现金流量净额",
    "net_cash_increase": r"现金及现金等价物净增加额",
}


def analyse(pdf: pathlib.Path) -> dict:
    rec: dict = {"file": pdf.name}
    t0 = time.perf_counter()
    doc = load_document(pdf)
    rec["pages"] = doc.n_pages
    rec["tables"] = len(doc.tables)
    rec["parse_s"] = round(time.perf_counter() - t0, 1)

    smap = detect_scopes_cached(pdf)
    rec["scopes_found"] = len(smap.marks)
    rec["has_consolidated"] = any(
        m.scope is ScopeKind.CONSOLIDATED for m in smap.marks
    )
    rec["scope_summary"] = smap.summary()

    rep = audit(pdf, period="2023")
    rec["audit_scope"] = rep.scope.value
    rec["audit_status"] = rep.status.value
    got = {m.kind.value for m in rep.metrics}
    rec["metrics_found"] = len(got & {k.value for k in EXPECTED})
    rec["metrics_missing"] = sorted(
        {k.value for k in EXPECTED} - got
    )
    rec["identity_fail"] = sum(
        1 for r in rep.rule_results if r.status.value == "FAIL"
    )
    rec["identity_review"] = sum(
        1 for r in rep.rule_results if r.status.value == "REVIEW_REQUIRED"
    )

    # ---- 表格单元格里的标签：完整还是被打碎 ----
    cell_hits: set[str] = set()
    for pt in doc.tables:
        if smap.scope_for(pt.page, pt.bbox) not in (
            ScopeKind.CONSOLIDATED, ScopeKind.PARENT
        ):
            continue
        for r in pt.rows:
            joined = "".join(v for v in r.values if v)
            for key, pat in LABEL_PATTERNS.items():
                if re.search(pat, joined):
                    cell_hits.add(key)

    # ---- 页面文本流里的标签：是否完整 ----
    flow_hits: set[str] = set()
    flow_frag: set[str] = set()
    if rec["has_consolidated"]:
        for pno, _b, text in all_text_lines(pdf, doc.n_pages):
            if smap.scope_of(pno) is not ScopeKind.CONSOLIDATED:
                continue
            t = text.replace(" ", "")
            for key, pat in LABEL_PATTERNS.items():
                if re.fullmatch(pat, t):
                    flow_hits.add(key)
                elif re.search(pat, t):
                    flow_frag.add(key)

    rec["cell_label_found"] = len(cell_hits)
    rec["flow_label_intact"] = len(flow_hits)
    rec["flow_label_fragmented"] = len(flow_frag - flow_hits)
    rec["recoverable_by_flow"] = len(flow_hits - cell_hits)
    return rec


def main() -> int:
    pdfs = sorted(DATA.glob("*.pdf"))
    results: list[dict] = []
    for i, p in enumerate(pdfs, 1):
        try:
            rec = analyse(p)
        except Exception as exc:  # noqa: BLE001
            rec = {"file": p.name, "error": f"{type(exc).__name__}: {exc}"}
        results.append(rec)
        if "error" in rec:
            print(f"[{i}/{len(pdfs)}] {p.name}  ERROR {rec['error']}")
        else:
            print(
                f"[{i}/{len(pdfs)}] {p.name:<34} {rec['pages']:>4}页 "
                f"口径{rec['metrics_found']}/11 恒等FAIL={rec['identity_fail']} "
                f"单元格标签{rec['cell_label_found']}/11 "
                f"文本流完整{rec['flow_label_intact']}/11"
            )

    ok = [r for r in results if "error" not in r]
    summary = {
        "n_reports": len(results),
        "n_ok": len(ok),
        "avg_pages": round(sum(r["pages"] for r in ok) / max(len(ok), 1), 1),
        "avg_parse_s": round(
            sum(r["parse_s"] for r in ok) / max(len(ok), 1), 1
        ),
        "scope_detected": sum(1 for r in ok if r["has_consolidated"]),
        "avg_metrics": round(
            sum(r["metrics_found"] for r in ok) / max(len(ok), 1), 2
        ),
        "avg_cell_label": round(
            sum(r["cell_label_found"] for r in ok) / max(len(ok), 1), 2
        ),
        "avg_flow_intact": round(
            sum(r["flow_label_intact"] for r in ok) / max(len(ok), 1), 2
        ),
        "total_recoverable_by_flow": sum(r["recoverable_by_flow"] for r in ok),
    }

    out = pathlib.Path(__file__).resolve().parent
    (out / "batch_results.json").write_text(
        json.dumps({"summary": summary, "reports": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    L = ["批量失败率汇总", ""]
    L.append(f"  样本数        : {summary['n_ok']}/{summary['n_reports']}")
    L.append(f"  平均页数      : {summary['avg_pages']}")
    L.append(f"  平均解析耗时  : {summary['avg_parse_s']} s")
    L.append(f"  口径识别成功  : {summary['scope_detected']}/{summary['n_ok']}")
    L.append(f"  平均指标命中  : {summary['avg_metrics']}/11")
    L.append(f"  单元格标签完整: {summary['avg_cell_label']}/11")
    L.append(f"  文本流标签完整: {summary['avg_flow_intact']}/11")
    L.append(f"  **可用文本流补回的标签总数: {summary['total_recoverable_by_flow']}**")
    L.append("")
    L.append("结论：若「文本流标签完整」显著高于「单元格标签完整」，")
    L.append("说明表格切列是主要信息损失来源，且**可以用文本流重建标签**，")
    L.append("修复成本远低于更换解析策略。")
    (out / "batch_summary.txt").write_text("\n".join(L), encoding="utf-8")
    print()
    print("\n".join(L))
    print(f"\nwritten -> {out / 'batch_summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
