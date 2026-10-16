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


def test_title_tolerates_chinese_numeral_prefix():
    """中文序号前缀。实测 836263 / 873576 的 8 个真标题全是这种写法。

    回归背景：``_clean`` 原来只剥阿拉伯数字，于是
    ``（一） 合并资产负债表`` 一条都匹配不上。偏偏同一份文件里
    审计报告正文有一份不带序号的报表名称清单，那些反而匹配上了 ——
    口径标记全落在审计报告目录上，母公司报表被当成合并口径。
    """
    cases = [
        ("（一） 合并资产负债表", ScopeKind.CONSOLIDATED),
        ("（二） 母公司资产负债表", ScopeKind.PARENT),
        ("（三） 合并利润表", ScopeKind.CONSOLIDATED),
        ("（四） 母公司利润表", ScopeKind.PARENT),
        ("（五） 合并现金流量表", ScopeKind.CONSOLIDATED),
        ("（六） 母公司现金流量表", ScopeKind.PARENT),
        ("（七） 合并股东权益变动表", ScopeKind.CONSOLIDATED),
        ("（八） 母公司股东权益变动表", ScopeKind.PARENT),
        ("一、合并资产负债表", ScopeKind.CONSOLIDATED),
        ("十、母公司利润表", ScopeKind.PARENT),
    ]
    for text, expected in cases:
        assert match_title(text) is expected, f"{text!r} 口径判定错误"


def test_bare_titles_are_consolidated():
    """无前缀裸标题按合并口径处理。实测 688610 埃科光电全是这种写法。

    回归背景：零口径标记意味着 **audit 根本不做口径过滤**，
    附注里的数会被当报表科目抽出来。实测 688610 p220
    递延所得税调节表的 -2,203,631.30 被当成所得税费用，
    现金流量表补充资料被当成现金流量净额 —— 全是脏数据。

    「裸的就算合并」不是想当然，依据是实测数值比对：
        摘要区 p12  总资产 159,124.72 万元 = 1,591,247,200.00 元
        裸标题区 p132 资产总计             1,591,247,176.07 元
    差 23.93 元，正是万元四舍五入的精度差。
    """
    for text in (
        "资产负债表",
        "利润表",
        "现金流量表",
        "所有者权益变动表",
        "股东权益变动表",
    ):
        assert match_title(text) is ScopeKind.CONSOLIDATED, f"{text!r} 口径判定错误"


def test_prefixed_titles_still_win_over_bare():
    """带前缀的写法不能被裸标题规则抢走。"""
    for text, expected in (
        ("合并资产负债表", ScopeKind.CONSOLIDATED),
        ("母公司资产负债表", ScopeKind.PARENT),
        ("母公司利润表", ScopeKind.PARENT),
        ("母公司现金流量表", ScopeKind.PARENT),
    ):
        assert match_title(text) is expected, f"{text!r} 口径判定错误"


def test_bare_title_rule_does_not_capture_notes_lines():
    """附注里带「资产负债表」的行不能被裸标题规则误判成标题。

    实测 688610 p134「十三、资产负债表日后事项」—— 序号被剥掉后
    剩下 8 个字，不 fullmatch 任何表名，所以安全。
    """
    for s in (
        "十三、资产负债表日后事项",
        "十四、资产负债表日后事项",
        "（1）资产负债表日外币货币性项目的折算方法",
    ):
        assert match_title(s) is None, f"{s!r} 被误判为三表标题"


def test_number_strip_does_not_eat_real_content():
    """序号必须有右括号/顿号收尾，不能把正常内容的前缀吃掉。"""
    # 这些开头是数字但不是序号，剥完就不是标题了 —— 但也不能被剥成别的标题
    for s in ("2023年年度报告", "12月31日的合并资产负债表"):
        assert match_title(s) is None, f"{s!r} 不应被识别为标题"


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


def test_row_level_scope_splits_one_table_across_two_scopes():
    """回归测试：一页里被并成一张表的两个口径，必须能按行区分。

    实测 603117 ST万林 p88：表格 bbox 是 74→759 横跨整页，
    首行「(二)稀释每股收益」属**合并利润表尾部**，
    而「母公司利润表」标题在 y=159.2，其下才是母公司报表。
    表格识别把两段并成了一张表，表格级判定只能二选一地错 ——
    实测母公司所得税费用 -2,878,668.74 被当成合并口径抽出，
    净利润恒等式报出差 10,002,568.40 的假 FAIL。
    """
    smap = _smap_with_marks(
        [ScopeMark(page=88, y0=159.2, scope=ScopeKind.PARENT,
                   title="母公司利润表")]
    )
    smap.page_scope = {88: ScopeKind.CONSOLIDATED}

    whole_table = (74, 74, 506, 759)
    assert smap.scope_for(88, whole_table) is ScopeKind.CONSOLIDATED

    # 标题之上的行（合并利润表尾部）-> 合并
    assert smap.scope_for_row(88, (74, 74, 506, 100)) is ScopeKind.CONSOLIDATED
    # 标题之下的行（母公司）-> 母公司
    assert smap.scope_for_row(88, (74, 200, 506, 300)) is ScopeKind.PARENT


def test_row_level_scope_uses_row_top_inclusively():
    """行顶与标题同高时算在标题之后 —— 标题行本身属于它宣告的那张表。"""
    smap = _smap_with_marks(
        [ScopeMark(page=88, y0=159.2, scope=ScopeKind.PARENT,
                   title="母公司利润表")]
    )
    smap.page_scope = {88: ScopeKind.CONSOLIDATED}
    assert smap.scope_for_row(88, (74, 159.2, 506, 170)) is ScopeKind.PARENT
    assert smap.scope_for_row(88, (74, 159.1, 506, 170)) is ScopeKind.CONSOLIDATED


def test_row_level_scope_falls_back_to_page_scope():
    smap = _smap_with_marks([])
    smap.page_scope = {7: ScopeKind.NOTES}
    assert smap.scope_for_row(7, (10, 10, 100, 50)) is ScopeKind.NOTES
    assert smap.scope_for_row(7, None) is ScopeKind.NOTES


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
