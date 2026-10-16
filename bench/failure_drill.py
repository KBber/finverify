"""从批量结果里挖出真正的阻塞点。

批量分析已证明「文本流重建标签」假设是错的（文本流 4.47/11
反而低于单元格 8.37/11）。这里进一步回答：
**到底是哪几个科目抽不到，卡在哪一步。**
"""

from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
data = json.loads((HERE / "batch_results.json").read_text(encoding="utf-8"))
reports = [r for r in data["reports"] if "error" not in r]

L: list[str] = []

# ---- 1. 哪些科目最常缺失 ----
miss = collections.Counter()
found = collections.Counter()
for r in reports:
    for k in r["metrics_missing"]:
        miss[k] += 1
L.append("=== 缺失频率（19 份年报，11 个标准科目）===")
for k, n in miss.most_common():
    L.append(f"  {k:<26} 缺 {n}/{len(reports)} 份 ({n / len(reports):.0%})")
L.append("")

# ---- 2. 恒等式结果分布 ----
L.append("=== 恒等式结果分布 ===")
fail_dist = collections.Counter(r["identity_fail"] for r in reports)
rev_dist = collections.Counter(r["identity_review"] for r in reports)
L.append(f"  FAIL 条数分布: {dict(sorted(fail_dist.items()))}")
L.append(f"  REVIEW 条数分布: {dict(sorted(rev_dist.items()))}")
status = collections.Counter(r["audit_status"] for r in reports)
L.append(f"  审计结论分布: {dict(status)}")
L.append("")

# ---- 3. 单元格标签找得到、但抽取仍失败 —— 说明断在别处 ----
L.append("=== 断点定位 ===")
L.append("  单元格里有标签、但指标没抽出来 = 断在「标签->指标映射」")
L.append("  单元格里没标签 = 断在「表格识别」")
L.append("")
for r in sorted(reports, key=lambda x: x["metrics_found"]):
    gap = r["cell_label_found"] - r["metrics_found"]
    L.append(
        f"  {r['file'][:30]:<32} 页{r['pages']:>4} "
        f"指标{r['metrics_found']:>2}/11 单元格标签{r['cell_label_found']:>2}/11 "
        f"差{gap:>3}  口径{r['audit_scope']:<11} FAIL={r['identity_fail']}"
    )
    if r["metrics_missing"]:
        L.append(f"      缺: {', '.join(r['metrics_missing'])}")
L.append("")

# ---- 4. 口径识别失败的两份 ----
L.append("=== 口径识别失败 ===")
for r in reports:
    if not r["has_consolidated"]:
        L.append(
            f"  {r['file'][:34]:<36} {r['pages']}页  "
            f"标记{r['scopes_found']}  {r['scope_summary']}"
        )
L.append("")

# ---- 5. 页数异常 ----
L.append("=== 页数异常（可能不是完整年报）===")
for r in reports:
    if r["pages"] < 50:
        L.append(f"  {r['file'][:34]:<36} {r['pages']} 页")
L.append("")

avg_m = sum(r["metrics_found"] for r in reports) / len(reports)
avg_c = sum(r["cell_label_found"] for r in reports) / len(reports)
L.append("=== 结论 ===")
L.append(f"  平均指标命中 {avg_m:.2f}/11，单元格标签 {avg_c:.2f}/11，"
         f"差距 {avg_c - avg_m:.2f}")
L.append("  表格识别不是主要瓶颈（8.37/11 已很高）；")
L.append("  真正的缺口在「标签 -> MetricKind 映射」，即中文科目名的写法变体。")

out = HERE / "failure_analysis.txt"
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
