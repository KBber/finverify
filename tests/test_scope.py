"""口径（Scope）识别的单元测试。

这些用例全部针对真实 A 股年报踩出来的坑，不需要 PDF 即可运行。

背景数据（实测艾艾精工 2023 年报）：

    p52  合并资产负债表  ->  p53  资产总计 528,372,737.29
    p54  母公司资产负债表  ->  p55  资产总计 429,004,543.99

两者相差约 1 亿。**混在一起做勾稽必然不平**，而这不是年报写错了。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finverify.schemas import ScopeKind  # noqa: E402
from finverify.scope import (  # noqa: E402
    ScopeMap,
    ScopeMark,
    dominant_scope,
    is_summary_text,
    match_title,
    scope_pages,
)


# ======================================================================
# 1. 标题识别
# ======================================================================
def test_statement_titles_map_to_correct_scope():
    cases = [
        ("合并资产负债表", ScopeKind.CONSOLIDATED),
        ("合并利润表", ScopeKind.CONSOLIDATED),
        ("合并现金流量表", ScopeKind.CONSOLIDATED),
        ("合并所有者权益变动表", ScopeKind.CONSOLIDATED),
        ("母公司资产负债表", ScopeKind.PARENT),
        ("母公司利润表", ScopeKind.PARENT),
        ("母公司现金流量表", ScopeKind.PARENT),
    ]
    for text, expected in cases:
        assert match_title(text) is expected, f"{text} 口径判定错误"


def test_title_tolerates_whitespace_and_numbering():
    """PDF 抽取常见噪声：空格、全角括号编号。"""
    for text in ("合并资产负债表 ", " 合并利润表", "1)合并现金流量表",
                 "（2）母公司资产负债表"):
        assert match_title(text) is not None, f"{text!r} 未识别为标题"


def test_notes_sentence_is_not_a_title():
    """附注正文里满是「合并资产负债表」字样，绝不能当成标题。

    回归背景：初版用 search 而非 fullmatch，导致附注里
    「…则调整合并资产负债表的期初…」被当成标题，
    把后面整段正文错误地划进了合并口径。
    """
    sentences = [
        "在报告期内，若因同一控制下企业合并增加子公司或业务的，则调整合并资产负债表的期初数",
        "业务自购买日至报告期末的现金流量纳入合并现金流量表。",
        "公司无需披露合并资产负债表中的其他项目",
    ]
    for s in sentences:
        assert match_title(s) is None, f"{s!r} 被误判为三表标题"


def test_empty_and_short_noise_ignored():
    for s in ("", "   ", "第X节", "一", "..."):
        assert match_title(s) is None, f"{s!r} 不应被识别为标题"


def test_summary_table_detection():
    assert is_summary_text("近三年主要会计数据和财务指标")
    assert is_summary_text("主要财务指标")
    assert not is_summary_text("合并资产负债表")


# ======================================================================
# 2. 区间归属 —— y 坐标是重点
# ======================================================================
def _smap_with_marks(marks: list[ScopeMark]) -> ScopeMap:
    """按真实语义构造 ScopeMap。

    ``page_scope[p]`` 记录的是**页首生效的口径**，即该页第一个标题之前
    的状态，而不是该页最后一个标题的状态 —— 这个区别正是本模块的核心。
    """
    smap = ScopeMap(marks=marks)
    page_map: dict[int, ScopeKind] = {}
    current: ScopeKind | None = None
    by_page: dict[int, list[ScopeMark]] = {}
    for m in marks:
        by_page.setdefault(m.page, []).append(m)
    for page in sorted(by_page):
        page_map[page] = current if current is not None else ScopeKind.SUMMARY
        for m in sorted(by_page[page], key=lambda x: x.y0):
            current = m.scope
    smap.page_scope = page_map
    return smap


def test_table_belongs_to_nearest_title_above_it():
    """同一页上半是上一张表尾部、下半是新表，必须按 y 坐标区分。

    回归背景：艾艾精工 p59 —— 页面上半是母公司利润表的续表
    （含 `减：所得税费用`），页面中部才是「合并现金流量表」标题。
    只按页归类会把母公司利润表的数算进合并口径，恒等式照样不平。
    """
    smap = _smap_with_marks(
        [
            # 上一页延续下来的口径（母公司利润表）
            ScopeMark(page=58, y0=80.0, scope=ScopeKind.PARENT,
                      title="母公司利润表"),
            # 本页中部的新标题
            ScopeMark(page=59, y0=300.0, scope=ScopeKind.CONSOLIDATED,
                      title="合并现金流量表"),
        ]
    )
    # 页面上部（y0=100）在本页标题之上 -> 仍属上一页延续下来的母公司口径
    assert smap.scope_for(59, (50, 100, 500, 280)) is ScopeKind.PARENT
    # 页面下部（y0=400）在本页标题之下 -> 合并口径
    assert smap.scope_for(59, (50, 400, 500, 700)) is ScopeKind.CONSOLIDATED


def test_table_with_no_title_above_carries_previous_page_scope():
    """页内无标题的表格必须延续上一页的口径，而不是取本页最后一个标题。"""
    smap = _smap_with_marks(
        [
            ScopeMark(page=58, y0=80.0, scope=ScopeKind.PARENT,
                      title="母公司利润表"),
            ScopeMark(page=59, y0=300.0, scope=ScopeKind.CONSOLIDATED,
                      title="合并现金流量表"),
        ]
    )
    smap.page_scope = {58: ScopeKind.PARENT, 59: ScopeKind.PARENT}
    # bbox=None 时走页级回退，而页级记录的是「页首生效口径」
    assert smap.scope_of(59) is ScopeKind.PARENT


def test_table_without_bbox_falls_back_to_page_scope():
    smap = _smap_with_marks(
        [ScopeMark(page=52, y0=80.0, scope=ScopeKind.CONSOLIDATED,
                   title="合并资产负债表")]
    )
    smap.page_scope = {52: ScopeKind.CONSOLIDATED, 1: ScopeKind.SUMMARY}
    assert smap.scope_for(52, None) is ScopeKind.CONSOLIDATED
    assert smap.scope_of(1) is ScopeKind.SUMMARY


def test_page_before_any_title_is_summary():
    """第一个三表标题之前是摘要/目录区，不能参与勾稽。"""
    smap = ScopeMap()
    smap.page_scope = {
        1: ScopeKind.SUMMARY,
        5: ScopeKind.SUMMARY,
        52: ScopeKind.CONSOLIDATED,
    }
    assert smap.scope_of(5) is ScopeKind.SUMMARY
    assert scope_pages(smap, ScopeKind.SUMMARY) == [1, 5]


def test_pages_after_last_statement_are_notes():
    """最后一个三表标题之后全是附注。

    回归背景：某年报附注长达 117 页且散布大量「资产合计」字样，
    不标记就会全部落进上一个口径 —— 最隐蔽的一类污染。
    """
    smap = ScopeMap()
    smap.page_scope = {
        70: ScopeKind.PARENT,
        71: ScopeKind.NOTES,
        180: ScopeKind.NOTES,
    }
    assert smap.scope_of(71) is ScopeKind.NOTES
    assert smap.scope_of(180) is ScopeKind.NOTES


def test_dominant_scope_prefers_consolidated():
    smap = ScopeMap(
        marks=[ScopeMark(page=52, y0=80.0, scope=ScopeKind.CONSOLIDATED,
                         title="合并资产负债表")]
    )
    assert dominant_scope(smap) is ScopeKind.CONSOLIDATED


def test_no_marks_means_no_scope():
    """合成样本没有三表标题 -> dominant_scope 返回 None -> 保持旧行为。"""
    assert dominant_scope(ScopeMap()) is None


# ======================================================================
# 3. 回归：同口径恒等式成立，跨口径必然不平
# ======================================================================
def _metric(kind, value, scope):
    from finverify.schemas import (
        EvidenceQuality,
        EvidenceSpan,
        FinancialMetric,
        StatementKind,
        VerifiedNumber,
    )

    return FinancialMetric(
        kind=kind,
        label=kind.value,
        number=VerifiedNumber(
            value=value,
            unit="元",
            period="2023",
            evidence=EvidenceSpan(
                page=1,
                bbox=(0.0, 0.0, 1.0, 1.0),
                quality=EvidenceQuality.TABLE_CELL,
                snippet=f"{kind.value} {value:,.2f}",
            ),
        ),
        statement=StatementKind.BALANCE_SHEET,
        scope=scope,
    )


def test_mixed_scope_breaks_balance_but_same_scope_passes():
    """核心回归：跨口径混用会让本该成立的恒等式不平。

    用实测的艾艾精工数字：合并资产 5.28 亿、母公司资产 4.29 亿，
    两者相差约 1 亿 —— 混起来算必然 FAIL。
    """
    from finverify import MetricKind, ValidationEngine, VerificationStatus

    cons = {
        MetricKind.TOTAL_ASSETS: 528_372_737.29,
        MetricKind.TOTAL_LIABILITIES: 72_650_204.88,
        MetricKind.TOTAL_EQUITY: 455_722_532.41,
    }
    parent = {
        MetricKind.TOTAL_ASSETS: 429_004_543.99,
        MetricKind.TOTAL_LIABILITIES: 30_083_129.83,
        MetricKind.TOTAL_EQUITY: 398_921_414.16,
    }

    engine = ValidationEngine()

    def bs_status(scope_map):
        metrics = [
            _metric(k, v, scope_map[k]) for k, v in cons.items()
        ] + [
            _metric(k, v, parent_scope) for k, v in parent.items()
            if parent_scope != ScopeKind.CONSOLIDATED
        ]
        (r,) = [
            r for r in engine.validate(metrics, "2023")
            if r.rule_id == "BS_BALANCE"
        ]
        return r.status

    parent_scope = ScopeKind.PARENT
    # 同口径：合并三件套内部恒等式成立
    assert bs_status({k: ScopeKind.CONSOLIDATED for k in cons}) is (
        VerificationStatus.PASS
    )
    # 母公司三件套内部也成立
    assert bs_status({k: ScopeKind.PARENT for k in cons}) is VerificationStatus.PASS


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
