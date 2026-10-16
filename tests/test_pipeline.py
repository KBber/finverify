"""端到端流水线测试：合成 PDF -> 解析 -> 抽取 -> 勾稽校验。

这些测试是 finverify 的核心承诺：
**注入的勾稽错误必须被确定性检出，且不产生误报。**

运行：``python tests/test_pipeline.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finverify import MetricKind, VerificationStatus  # noqa: E402
from finverify.extraction import (  # noqa: E402
    _is_value_cell,
    classify_label,
    extract_metrics,
    parse_number,
)
from finverify.ingestion import load_document, normalize_text  # noqa: E402
from finverify.pipeline import audit  # noqa: E402
from finverify.samples import AnnualReportData, build_sample_pdf  # noqa: E402

_TMP = Path(tempfile.gettempdir()) / "finverify_tests"
_TMP.mkdir(parents=True, exist_ok=True)


# ======================================================================
# 1. 数值解析
# ======================================================================
def test_parse_number_thousands_separator():
    assert parse_number("9,450,000,000.00") == (9_450_000_000.00, "元")


def test_parse_number_negative_kept_negative():
    """回归测试：早期版本把 -880,000,000 解析成了 +880,000,000。"""
    val, unit = parse_number("-880,000,000.00")
    assert val == -880_000_000.00, f"负号丢失，得到 {val}"
    assert unit == "元"


def test_parse_number_parenthesis_negative():
    """会计惯例：(1,234) = -1234"""
    val, _ = parse_number("(1,234.00)")
    assert val == -1234.00


def test_parse_number_chinese_unit_scaling():
    # 3,600.00 万元 = 3,600 × 10^4 = 36,000,000 元
    val, unit = parse_number("营业收入 3,600.00 万元")
    assert val == 3.6e7 and unit == "元"


def test_parse_number_yuan_suffix_not_shadowed_by_wan():
    """回归测试："万元" 含 "元"，必须按长度降序匹配，否则缩放静默失效。"""
    assert parse_number("1,000 万元")[0] == 1e7
    assert parse_number("1,000 亿元")[0] == 1e11
    assert parse_number("1,000 元")[0] == 1e3


def test_parse_number_rejects_non_numeric():
    assert parse_number("合并资产负债表（简表）") is None
    assert parse_number("") is None


def test_note_reference_cell_is_not_a_value_cell():
    """回归测试：附注编号列不能被当成金额。

    实测 688610 埃科光电 p133 利润表：
        [0] 一、营业收入   [1] 七、61   [3] 235,575,368.09
    「七、61」是附注七第 61 项的引用编号。``parse_number`` 是 search
    语义，会抠出 61 —— 于是营业收入读成 61.00、所得税费用读成 76.00，
    而真正的本期数就在第 3 列。
    """
    assert parse_number("七、61")[0] == 61.0, "本用例的前提是 parse_number 会误抠"
    for ref in ("七、61", "七、76", "五、4", "（七）61"):
        assert _is_value_cell(ref) is False, f"{ref!r} 被当成数值单元格"
    # 真正的数值列必须照常通过
    for v in ("235,575,368.09", "-910,591.05", "(1,234.00)", "61.00%", "3,600万元"):
        assert _is_value_cell(v) is True, f"{v!r} 被误拒"


# ======================================================================
# 2. 科目分类
# ======================================================================
def test_classify_label_strips_order_prefix():
    """'一、营业收入' 与 '营业收入' 必须映射到同一指标。"""
    hit = classify_label("一、营业收入")
    assert hit is not None and hit[0] is MetricKind.REVENUE


def test_classify_label_liab_plus_equity_is_assets():
    """回归测试：'负债和所有者权益总计' 数值上等于资产总计。

    早期版本误映射为 TOTAL_EQUITY，会让资产恒等式失去意义。
    """
    hit = classify_label("负债和所有者权益总计")
    assert hit is not None and hit[0] is MetricKind.TOTAL_ASSETS


def test_classify_label_revenue_beats_cost():
    """'营业收入' 不能被 '营业成本' 的模式抢走，反之亦然。"""
    a = classify_label("一、营业收入")
    b = classify_label("减：营业成本")
    assert a[0] is MetricKind.REVENUE
    assert b[0] is MetricKind.OPERATING_COST


def test_net_profit_subitems_are_not_net_profit():
    """回归测试：净利润的两个分项不能被当成净利润本身。

    实测 002825 纳尔股份 p82 有一行
    ``(二)终止经营净利润(净亏损以"-"号填列) 0.00``。
    匹配用的是 search，``净利润`` 直接命中「终止经营净利润」，
    net_profit 被读成 0.00 —— 随后
    「净利润 = 利润总额 − 所得税费用」恒等式假性 FAIL。

    判为「不认识」是对的：漏抽只会得到 REVIEW_REQUIRED（证据不足），
    错值会让人误以为年报有问题。
    """
    for label in (
        "（二）终止经营净利润(净亏损以“-”号填列)",
        "持续经营净利润(净亏损以“-”号填列)",
        "终止经营净利润",
    ):
        assert classify_label(label) is None, f"{label!r} 被误判为净利润"


def test_real_net_profit_labels_still_match():
    """排除分项不能误伤真正的净利润行。"""
    for label in (
        "四、净利润（净亏损以“-”号填列)",
        "五、净利润",
        "归属于母公司股东的净利润",
        "净利润",
    ):
        hit = classify_label(label)
        assert hit is not None and hit[0] is MetricKind.NET_PROFIT, f"{label!r} 漏匹配"


# ======================================================================
# 3. 端到端：正样本
# ======================================================================
def test_clean_sample_passes_all_identities():
    pdf = build_sample_pdf(_TMP / "clean.pdf", AnnualReportData())
    report = audit(pdf, period="2023")

    assert report.status is VerificationStatus.PASS, report.to_text()
    assert len(report.rule_results) == 3
    for r in report.rule_results:
        assert r.status is VerificationStatus.PASS, r


def test_clean_sample_extracts_all_core_metrics():
    pdf = build_sample_pdf(_TMP / "clean2.pdf", AnnualReportData())
    metrics, _ = extract_metrics(load_document(pdf), "2023")
    kinds = {m.kind for m in metrics}
    for required in (
        MetricKind.TOTAL_ASSETS,
        MetricKind.TOTAL_LIABILITIES,
        MetricKind.TOTAL_EQUITY,
        MetricKind.TOTAL_PROFIT,
        MetricKind.INCOME_TAX,
        MetricKind.NET_PROFIT,
        MetricKind.OPERATING_CASH_FLOW,
        MetricKind.INVESTING_CASH_FLOW,
        MetricKind.FINANCING_CASH_FLOW,
        MetricKind.NET_CASH_INCREASE,
    ):
        assert required in kinds, f"未抽取 {required.value}"


# ======================================================================
# 4. 端到端：幻觉检出（这是核心承诺）
# ======================================================================
def test_injected_equity_error_is_caught():
    """所有者权益多记 5,000 万 -> BS_BALANCE 必须 FAIL。"""
    pdf = build_sample_pdf(
        _TMP / "bad_equity.pdf",
        AnnualReportData(
            injected_error=(MetricKind.TOTAL_EQUITY, 4_965_000_000.00)
        ),
    )
    report = audit(pdf, period="2023")

    assert report.status is VerificationStatus.FAIL
    bs = [r for r in report.rule_results if r.rule_id == "BS_BALANCE"][0]
    assert bs.status is VerificationStatus.FAIL, bs
    assert abs(bs.delta - (-50_000_000.0)) < 1.0


def test_injected_tax_error_is_caught():
    """所得税少记 1,200 万 -> IS_NET_PROFIT 必须 FAIL。"""
    pdf = build_sample_pdf(
        _TMP / "bad_tax.pdf",
        AnnualReportData(injected_error=(MetricKind.INCOME_TAX, 79_800_000.00)),
    )
    report = audit(pdf, period="2023")

    assert report.status is VerificationStatus.FAIL
    np_ = [r for r in report.rule_results if r.rule_id == "IS_NET_PROFIT"][0]
    assert np_.status is VerificationStatus.FAIL, np_
    assert abs(np_.delta - (-12_000_000.0)) < 1.0


def test_injection_does_not_break_other_identities():
    """注入错误只应打掉对应的那条恒等式，不应引发连环误报。"""
    pdf = build_sample_pdf(
        _TMP / "bad2.pdf",
        AnnualReportData(
            injected_error=(MetricKind.TOTAL_EQUITY, 4_965_000_000.00)
        ),
    )
    report = audit(pdf, period="2023")
    others = [r for r in report.rule_results if r.rule_id != "BS_BALANCE"]
    for r in others:
        assert r.status is VerificationStatus.PASS, (
            f"{r.rule_id} 出现连带误报: {r.detail}"
        )


# ======================================================================
# 5. 溯源坐标
# ======================================================================
def test_every_metric_carries_real_coordinates():
    pdf = build_sample_pdf(_TMP / "prov.pdf", AnnualReportData())
    metrics, _ = extract_metrics(load_document(pdf), "2023")

    assert metrics
    for m in metrics:
        ev = m.number.evidence
        assert ev.page >= 1, "页码必须从 1 开始"
        x0, y0, x1, y1 = ev.bbox
        assert x1 > x0 and y1 > y0, f"bbox 非法: {ev.bbox}"
        assert ev.cell is not None and ev.cell[0] >= 0
        assert ev.quality.name == "TABLE_CELL"


def test_report_serialises_without_breaking():
    pdf = build_sample_pdf(_TMP / "json.pdf", AnnualReportData())
    report = audit(pdf, period="2023")
    import json

    payload = json.loads(report.to_json())
    assert payload["status"] == "PASS"
    assert len(payload["metrics"]) == len(report.metrics)
    assert payload["extraction_trace"]["rows_matched"] > 0
    md = report.to_markdown()
    assert "勾稽校验" in md and "BS_BALANCE" in md


# ======================================================================
# 6. 文本规范化回归
#    这些用例对应两个真实事故：换一台机器跑，指标静默消失；
#    负数被读成正数。对财务工具都属于不可接受的错误。
# ======================================================================

_MINUS_VARIANTS = [
    ("ASCII 连字符", "-"),
    ("非断行连字符", "\u2011"),
    ("减号", "\u2212"),
    ("全角连字符", "\uff0d"),
    ("en dash", "\u2013"),
    ("em dash", "\u2014"),
]


def test_every_minus_variant_parses_as_negative():
    """任何一种连字符都必须解析为负数。

    回归背景：Noto 字体抽出的负号是 U+2011，
    旧实现只认 ASCII '-'，于是 -8.8 亿被读成 +8.8 亿 ——
    现金流量表恒等式会"通过"，错误反而被掩盖。
    """
    for name, dash in _MINUS_VARIANTS:
        parsed = parse_number(f"{dash}880,000,000.00")
        assert parsed is not None, f"{name} 未解析出数值"
        value, _unit = parsed
        assert value == -880_000_000.0, f"{name} {dash!r} 符号丢失: {value}"


def test_fullwidth_parenthesised_negative():
    """全角括号负数 —— 中文财报的常见写法。"""
    for text in ("(880,000,000.00)", "（880,000,000.00）", "（1,234.00）"):
        parsed = parse_number(text)
        assert parsed is not None, f"{text!r} 未解析出数值"
        assert parsed[0] < 0, f"{text!r} 括号负数失效: {parsed[0]}"


def test_cjk_compatibility_ideograph_is_normalised():
    """兼容码位必须折叠成规范码位，否则科目名正则会静默失配。

    回归背景：Noto Sans CJK 把"利"字的字形反向映射到 U+F9DD
    （显示完全相同，码位却不同），于是 `三、利润总额` 实际是
    `三、\\uF9DD润总额`，正则 `利润总额` 匹配不上 ——
    整行指标连同勾稽校验一起消失，
    而系统报告的是 REVIEW_REQUIRED，看起来"很合理"。
    """
    compat = "\u4e09\u3001\uf9dd\u6da6\u603b\u989d"  # 三、[利]润总额
    canonical = "三、利润总额"

    assert compat != canonical, "本用例的前提是两者码位不同"
    assert normalize_text(compat) == canonical
    assert classify_label(compat) == classify_label(canonical)
    assert classify_label(compat) is not None, "兼容码位未折叠，科目名匹配失败"


def test_fullwidth_digits_are_normalised():
    """全角数字 —— 部分 PDF 与旧系统导出常见。"""
    assert parse_number("９４５，０００，０００．００") == (945_000_000.0, "元")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
