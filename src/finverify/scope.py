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
所以定位用 :func:`finverify.ingestion.all_text_lines`，
再按「最近的前一个三表标题」把区间切出来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from finverify.ingestion import all_text_lines
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

# 裸标题（不带「合并」「母公司」前缀）。实测 688610 埃科光电用的是这种写法：
# p131 资产负债表 / p133 利润表 / p134 现金流量表 / p136 所有者权益变动表，
# 一个前缀都没有，导致零口径标记 —— 而零标记意味着**不过滤口径**，
# 附注里的数会被当成报表科目抽出来（实测 p220 递延所得税调节表的
# -2,203,631.30 被当成所得税费用，现金流量表补充资料被当成现金流量净额）。
#
# 为什么可以按**合并**处理，依据是实测数值比对，不是想当然：
#     摘要区 p12 披露   总资产 159,124.72 万元 = 1,591,247,200.00 元
#     裸标题区 p132    资产总计                1,591,247,176.07 元
# 差 23.93 元，正是万元四舍五入的精度差 —— 两者是同一个数。
#
# 语料里 17 份带前缀的年报，合并口径一律标「合并」、母公司口径一律标
# 「母公司」，从未出现裸标题与真实母公司报表并存的情况，
# 所以裸标题落到合并是安全的默认。
_register(
    ScopeKind.CONSOLIDATED,
    "资产负债表",
    "利润表",
    "现金流量表",
    "所有者权益变动表",
    "股东权益变动表",
    "所有者权益（或股东权益）变动表",
)

_TITLE_RE = re.compile("|".join(re.escape(k) for k in _STATEMENT_TITLES))

# 摘要表关键词：出现这些词的表格不可信，不能参与勾稽
_SUMMARY_RE = re.compile(r"主要会计数据|主要财务指标|非经常性损益")


def _clean(text: str) -> str:
    """去掉 PDF 抽取常见的空白与编号噪声。

    必须同时剥掉**中文数字**序号，否则整份年报的口径判定会塌掉。
    实测 836263 中航泰达、873576 天力复合用的是
    ``（一） 合并资产负债表`` 这种写法，8 个真标题**一个都匹配不上**。
    更糟的是同一份文件里审计报告正文里有一份不带序号的报表名称清单，
    那些反而匹配上了 —— 于是口径标记全部落在审计报告目录上，
    母公司报表被当成合并口径，恒等式必然混口径。

    注意右括号/顿号是**必需**的：光匹配数字会把
    ``2023年年度报告`` 的开头吃掉。只有 ``1)`` / ``一、`` 这种
    真正的序号形态才会被剥掉。
    """
    t = text.replace(" ", "").replace(" ", "").strip()
    t = re.sub(
        r"^[（(【]?[0-9一二三四五六七八九十百]+[）).、】]\s*",
        "",
        t,
    )
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

        注意这张表可能横跨两个口径
        ------------------------
        实测 603117 ST万林 p88：表格 bbox 是 74→759 横跨整页，
        首行是「(二)稀释每股收益」属**合并利润表尾部**，
        而「母公司利润表」标题在 y=159.2，其下才是母公司报表。
        表格识别把两段并成了一张表，表格级判定必然二选一地错。

        需要精确到行时请用 :meth:`scope_for_row`。
        """
        if bbox:
            above = [m for m in self.marks if m.page == page and m.y0 < bbox[1]]
            if above:
                return max(above, key=lambda m: m.y0).scope
        return self.page_scope.get(page, ScopeKind.UNKNOWN)

    def scope_for_row(self, page: int, bbox: tuple[float, ...] | None) -> ScopeKind:
        """按**行**判定口径，而不是按整张表。

        这是 :meth:`scope_for` 的行级版本，规则相同但用行的顶边做 y 比较。
        必须做到行级，是因为表格识别经常把一页里的两段内容并成一张表
        （实测 603117 p88 横跨合并与母公司两个口径），
        此时表格级判定只能二选一地错，而错的那一半会直接污染勾稽 ——
        实测母公司所得税费用 -2,878,668.74 被当成合并口径抽出，
        净利润恒等式报出差 10,002,568.40 的假 FAIL。
        """
        if bbox:
            above = [m for m in self.marks if m.page == page and m.y0 <= bbox[1]]
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


def _detect_uncached(pdf_path, max_pages: int = 400) -> ScopeMap:
    """扫描页面文本流，把文档切成若干口径区间。

    结果按 (路径, mtime) 缓存 —— ``audit()`` 与 ``extract_metrics()``
    都会调它，不缓存的话全文文本流会被反复扫描，实测是主要性能瓶颈。

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

    for pno, _bbox, text in all_text_lines(pdf_path, max_pages):
        # 先记下**页首生效的口径**（本页标题之前的状态）。
        # 这一步很关键：表格上方没有标题时必须回退到这里，
        # 而不是回退到"本页最后一个标题"—— 后者会把
        # 「上一张表的续表」错误地划进本页新表的口径。
        page_map.setdefault(
            pno, current if current is not None else ScopeKind.SUMMARY
        )
        scope = match_title(text)
        if scope is not None:
            current = scope
            marks.append(
                ScopeMark(page=pno, y0=_bbox[1], scope=scope, title=_clean(text))
            )

    # 最后一个标题之后 = 附注
    if marks:
        last_page = max(m.page for m in marks)
        for p in range(last_page + 1, max_pages + 1):
            if p in page_map:
                page_map[p] = ScopeKind.NOTES

    smap.marks = marks
    smap.page_scope = page_map
    return smap


@lru_cache(maxsize=32)
def _cached(
    path_str: str, mtime: float, max_pages: int
) -> ScopeMap:
    """按 (路径, mtime, 页数上限) 缓存。

    mtime 参与 key，文件一旦被重新生成就自然失效，
    不需要手动清缓存 —— 否则改了样本却拿到旧口径，是很难查的坑。
    """
    return _detect_uncached(path_str, max_pages)


def detect_scopes_cached(pdf_path, max_pages: int = 400) -> ScopeMap:
    """带缓存的口径识别。

    ``audit()`` 与 ``extract_metrics()`` 都会调口径识别，
    不缓存的话全文文本流会被反复扫描 —— 实测这是评测的主要性能瓶颈。
    """
    p = Path(pdf_path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _detect_uncached(str(p), max_pages)
    return _cached(str(p.resolve()), mtime, max_pages)
