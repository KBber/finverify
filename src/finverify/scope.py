"""口径识别 —— 判断每张表属于合并报表、母公司报表还是摘要表。

为什么必须做
------------
真实年报里同一个科目会在多个地方出现，且**数值不同**：

    艾艾精工  资产总计  合并 528,372,737.29   母公司 429,004,543.99

合并报表含子公司，母公司报表只含本公司，两套数字各自内部自洽。
把它们混在一起做勾稽，必然 FAIL —— 而这与「年报写错了」是两回事。

怎么定位
--------
**不能靠表格单元格里的标题。** 实测三份年报：

    艾艾精工     合并资产负债表 / 母公司资产负债表  -> 表格行里完全抓不到
    ST美谷      母公司利润表 -> 表格切列后变成 '司利润表'
    雷科防务     合并资产负债表 -> 表格里只有 '资产负债表'，没有前缀

表格识别会把标题切列、截断、丢行。**页面文本流是完整顺序的**，
所以定位用 :func:`finverify.ingestion.page_text_lines`，
再按「最近的前一个三表标题」把区间切出来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from finverify.ingestion import page_text_lines
from finverify.schemas import ScopeKind

# --------------------------------------------------------------------------
# 标题识别
# --------------------------------------------------------------------------
# 必须用 fullmatch：附注正文里大量出现「…调整合并资产负债表的期初…」这类
# 句子，search 会把它们误判成标题，把后续整段归到错误的口径里。
_STATEMENT_TITLES: dict[str, ScopeKind] = {}


def _register(scope: ScopeKind, *names: str) -> None:
    for n in names:
        _STATEMENT_TITLES[n] = scope


_register(
    ScopeKind.CONSOLIDATED,
    "合并资产负债表",
    "合并利润表",
    "合并现金流量表",
    "合并所有者权益变动表",
    "合并股东权益变动表",
    "合并所有者权益（或股东权益）变动表",
)
_register(
    ScopeKind.PARENT,
    "母公司资产负债表",
    "母公司利润表",
    "母公司现金流量表",
    "母公司所有者权益变动表",
    "母公司股东权益变动表",
    "母公司所有者权益（或股东权益）变动表",
)

_TITLE_RE = re.compile("|".join(re.escape(k) for k in _STATEMENT_TITLES))

# 摘要表关键词：出现这些词的表格不可信，不能参与勾稽
_SUMMARY_RE = re.compile(r"主要会计数据|主要财务指标|非经常性损益")


def _clean(text: str) -> str:
    """去掉 PDF 抽取常见的空白与编号噪声。"""
    t = text.replace(" ", "").replace(" ", "").strip()
    # 去掉 "1)" "（1）" 之类前缀
    t = re.sub(r"^[（(]?\d+[）).、]\s*", "", t)
    return t


def match_title(text: str) -> ScopeKind | None:
    """判断一行文本是否为三表标题，返回其口径。不是标题则返回 None。

    单独抽出来是为了能直接写单元测试 —— 口径识别最容易出错的地方
    就是把附注正文误判成标题。
    """
    t = _clean(text)
    if not t or len(t) > 24:
        # 三表标题都很短；长句一律不是标题（附注里满��都是）
        return None
    if _TITLE_RE.fullmatch(t):
        return _STATEMENT_TITLES[t]
    # 有些版本写成「合并资产负债表（续）」或带空格的全角括号
    stripped = re.sub(r"[（(]续[）)]?$", "", t)
    if stripped != t and _TITLE_RE.fullmatch(stripped):
        return _STATEMENT_TITLES[stripped]
    return None


def is_summary_text(text: str) -> bool:
    t = _clean(text)
    return bool(_SUMMARY_RE.search(t)) and len(t) < 40


def scope_pages(smap: ScopeMap, scope: ScopeKind) -> list[int]:
    return sorted(p for p, s in smap.page_scope.items() if s is scope)


def dominant_scope(smap: ScopeMap) -> ScopeKind | None:
    """挑一个「主口径」。

    默认优先合并报表：审计意义上「这家公司的账」就是合并口径。
    只有母公司报表的公司才退而使用母公司口径。
    """
    if smap.marks:
        return ScopeKind.CONSOLIDATED
    return None


# --------------------------------------------------------------------------
# 区间切分
# --------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class ScopeMark:
    """一个口径分界标记（三表标题）。"""

    page: int
    y0: float
    scope: ScopeKind
    title: str


@dataclass(slots=True)
class ScopeMap:
    """文档口径图。

    ``page_scope`` 是**页级**回退口径；精确判定请用 :meth:`scope_for`。
    """

    marks: list[ScopeMark] = field(default_factory=list)
    page_scope: dict[int, ScopeKind] = field(default_factory=dict)

    def scope_for(self, page: int, bbox: tuple[float, ...] | None) -> ScopeKind:
        """判定某张表属于哪个口径。

        为什么必须看 y 坐标而不是只看页码
        ------------------------------
        真实年报里，**三表标题在页顶，但同一页的上半部分可能还是上一张表的尾部**。

        实测艾艾精工 p59：页面顶部是「合并现金流量表」标题，
        但该页上方的 `减：所得税费用` 属于**母公司利润表**的续表。
        只按页归类就会把它算进合并口径 —— 恒等式照样不平。

        所以规则是：表格归属于**它上方最近的那个标题**。
        """
        if bbox:
            top = bbox[1]
            above = [m for m in self.marks if m.page == page and m.y0 < top]
            if above:
                return max(above, key=lambda m: m.y0).scope
        return self.page_scope.get(page, ScopeKind.UNKNOWN)

    def scope_of(self, page: int) -> ScopeKind:
        return self.page_scope.get(page, ScopeKind.UNKNOWN)

    def summary(self) -> str:
        if not self.page_scope:
            return "未识别到三表标题"
        counts: dict[ScopeKind, int] = {}
        for s in self.page_scope.values():
            counts[s] = counts.get(s, 0) + 1
        return ", ".join(
            f"{s.value}={n}页" for s, n in sorted(counts.items(), key=lambda kv: kv[0].value)
        )


def detect_scopes(pdf_path, max_pages: int = 400) -> ScopeMap:
    """扫描页面文本流，把文档切成若干口径区间。

    规则：
    - 某页属于该页上**最后一个**三表标题所声明的口径；
    - 第一个三表标题之前的页面归 SUMMARY（摘要/目录区）；
    - **最后一个三表标题之后**的页面归 NOTES —— 实测某年报 117 页附注
      会被误判成"母公司口径"，那是最容易污染数据的地方。
    """
    smap = ScopeMap()
    marks: list[ScopeMark] = []
    current: ScopeKind | None = None
    page_map: dict[int, ScopeKind] = {}

    for pno in range(1, max_pages + 1):
        try:
            lines = page_text_lines(pdf_path, pno)
        except Exception:  # noqa: BLE001 — 单页失败不应中断整体
            continue
        # 先记下**页首生效的口径**（本页标题之前的状态）。
        # 这一步很关键：表格上方没有标题时必须回退到这里，
        # 而不是回退到"本页最后一个标题"—— 后者会把
        # 「上一张表的续表」错误地划进本页新表的口径。
        page_map[pno] = current if current is not None else ScopeKind.SUMMARY
        for _idx, bbox, text in lines:
            scope = match_title(text)
            if scope is not None:
                current = scope
                marks.append(
                    ScopeMark(
                        page=pno, y0=bbox[1], scope=scope, title=_clean(text)
                    )
                )
                break

    # 最后一个标题之后 = 附注
    if marks:
        last_page = max(m.page for m in marks)
        for p in range(last_page + 1, max_pages + 1):
            if p in page_map:
                page_map[p] = ScopeKind.NOTES

    smap.marks = marks
    smap.page_scope = page_map
    return smap
