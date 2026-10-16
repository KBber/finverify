"""文档解析层 —— PDF → 带坐标的表格行。

这一层的唯一职责是**保住坐标**。文本可以丢，但只要还知道
"这个值在第几页、第几行、第几列"，溯源能力就在。

PyMuPDF 的表格识别默认走 ``lines`` 策略，只认有完整框线的表；
中文财报里大量是无框线表，这里统一走 ``text`` 策略。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

BBox = tuple[float, float, float, float]


def normalize_text(text: str) -> str:
    """NFKC 规范化 PDF 抽取出的文本。

    这一步不是洁癖，是必需的。实测踩到的坑：

    - **CJK 兼容汉字**：部分字体（Noto Sans CJK 等）里"润"字的
      字形反向映射到 U+F9DD（兼容码位）而非 U+6DA6（规范码位）。
      于是 `三、利润总额` 实际是 `三、利润\uF9DD总额`，
      正则 `利润总额` 匹配不上，整行指标静默丢失。
    - **全角减号 / 全角括号**：－（U+FF0D）、（）（U+FF08/09），
      NFKC 之后分别变成 `-` 与 `()`，负数与括号负数才认得出。

    NFKC 把所有这些兼容写法折叠到规范形式，
    下游的科目名正则与数值解析只需要处理一种写法。
    """
    return unicodedata.normalize("NFKC", text)


@dataclass(slots=True)
class TableRow:
    """一行表格，携带每个单元格的坐标。"""

    page: int
    row_index: int
    values: list[str]
    cells: list[BBox | None]

    def cell_bbox(self, col: int) -> BBox | None:
        if 0 <= col < len(self.cells):
            return self.cells[col]
        return None

    @property
    def row_bbox(self) -> BBox:
        xs0 = [c[0] for c in self.cells if c]
        ys0 = [c[1] for c in self.cells if c]
        xs1 = [c[2] for c in self.cells if c]
        ys1 = [c[3] for c in self.cells if c]
        if not xs0:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    @property
    def is_blank(self) -> bool:
        return not any(v.strip() for v in self.values)


@dataclass(slots=True)
class PageTable:
    """某一页上的一个表。"""

    page: int
    bbox: BBox
    rows: list[TableRow] = field(default_factory=list)


# --------------------------------------------------------------------------
# 逻辑行重建
# --------------------------------------------------------------------------
# 真实年报是**无框线表**，逻辑上的一行在视觉上常占 2-3 个文本行，
# 而 find_tables 只按视觉切分。实测（永泰运 2023 年报 p103）：
#
#     r22  非空1/3  ['四、利润总额(亏损总额以"-"号', '', '']
#     r23  非空2/3  ['', '215,459,982.04', '417,326,200.27']
#     r24  非空1/3  ['填列)', '', '']
#
# 本该是一行： 四、利润总额(亏损总额以"-"号填列) | 215,459,982.04 | ...
# 标签正则写得再全也匹配不上 —— 数值根本不在标签所在的那一行里。

# 中文会计惯例：单独的破折号表示「零」或「无」。
# 实测踩坑：``四、净利润   -   -`` 是一行完整的零值数据，
# 但若不把破折号当数字，这行会被误判成「只有标签没有数值」的片段，
# 从而去合并下一行 —— 把 net_profit 变成 0.00，属于最典型的假阳性。
_ZERO_LIKE = r"[-\u2010\u2011\u2012\u2013\u2014\u2212\uff0d\u2015\u00ad]"

_NUMERIC_RE = re.compile(
    rf"^[\s（(]*(?:{_ZERO_LIKE}|[-+]?[\d,]+(?:\.\d+)?)"
    rf"[\s）)]*%?$"
)


def _cell_is_numeric(value: str) -> bool:
    """判断一个单元格是否是数值（含会计惯例的「破折号表示零」）。"""
    v = value.strip()
    if not v:
        return False
    return bool(_NUMERIC_RE.match(v))


def _row_shape(row: TableRow) -> tuple[bool, bool]:
    """返回 (是否有文本, 是否有数值)。"""
    has_text = any(v.strip() for v in row.values)
    has_num = any(_cell_is_numeric(v) for v in row.values)
    return has_text, has_num


def _row_height(row: TableRow) -> float:
    boxes = [c for c in row.cells if c]
    if not boxes:
        return 0.0
    return max(b[3] for b in boxes) - min(b[1] for b in boxes)


def _median_height(rows: list[TableRow]) -> float:
    heights = sorted(h for h in (_row_height(r) for r in rows) if h > 0)
    if not heights:
        return 0.0
    return heights[len(heights) // 2]


def _is_fragment(row: TableRow, median_height: float) -> bool:
    """这一行是否是被拆散的碎片（行高明显小于本表的常规行高）。

    实测：正常文本行 9-10pt，被 find_tables 拆出来的碎片行 6pt。
    行高是最稳的判据 —— 它不依赖内容猜测。
    """
    if median_height <= 0:
        return False
    return _row_height(row) < median_height * 0.75


def _combine(group: list[TableRow]) -> TableRow:
    """把一组碎片行合成一个逻辑行，同列 bbox 取纵向并集。"""
    ncols = max(len(r.values) for r in group)
    values: list[str] = []
    cells: list[BBox | None] = []
    for col in range(ncols):
        parts = [
            r.values[col] for r in group if col < len(r.values) and r.values[col]
        ]
        values.append("".join(parts).strip())
        boxes = [r.cells[col] for r in group if col < len(r.cells) and r.cells[col]]
        cells.append(
            (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            if boxes
            else None
        )
    return TableRow(
        page=group[0].page,
        row_index=-1,
        values=values,
        cells=cells,
    )


def merge_wrapped_rows(rows: list[TableRow], height_gate: bool = False) -> list[TableRow]:
    """把被 find_tables 拆散的逻辑行合并回去。

    合并规则（刻意保守，宁可漏合不可错合）：

    1. 全空行直接丢弃；
    2. 一行「有文本无数值」说明它只是标签片段，向后合并直到拿到数值；
    3. 拿到数值后，若下一行「有文本无数值」，那是标签的**续行**，
       继续并入 —— 实测里就是 ``...以"-"号`` + 数值 + ``填列)`` 这种结构；
    4. 「既有文本又有数值」的行一定开启新的逻辑行，绝不与相邻行合并。

    合并时同一列的 bbox 取纵向并集，坐标依然精确可溯源。

    Parameters
    ----------
    height_gate:
        实验用开关。开启后只有行高明显偏小的碎片行才允许参与合并。

        **默认关闭，且实测应当保持关闭。** 最初加这道闸门是想防住
        「一行完整数据被误判成标签碎片、吞掉下一行」，但那个事故的真因
        是破折号 ``四、净利润  -  -`` 不被当作数值（见 ``_ZERO_LIKE``），
        破折号归零后 ``has_text and has_num`` 就会立即 flush，闸门已多余。

        更糟的是闸门方向是反的：实测标签起始行高度正常（永泰运 p103
        r22 为 9pt，与常规行同高），数值与标签续行才是 6pt 碎片。闸门
        把起始行判成「非碎片」原样放行，分组永远无法累积，合并彻底失效
        —— 18 份真实年报实测指标数 140 → 140，一行都没多抽出来。

        保留参数只为让 ``bench/ab_gate.py`` 能复现这个对照结论。
    """
    median_height = _median_height(rows) if height_gate else 0.0
    merged: list[TableRow] = []
    group: list[TableRow] = []
    has_text = has_num = False

    def flush() -> None:
        nonlocal group, has_text, has_num
        if not group:
            return
        combined = _combine(group)
        combined.row_index = len(merged)
        merged.append(combined)
        group, has_text, has_num = [], False, False

    for row in rows:
        if row.is_blank:
            continue  # 全空行没有信息，只会污染合并

        row_text, row_num = _row_shape(row)

        # 实验闸门：开启时，非碎片行（一行完整数据）原样保留，绝不与邻行合并。
        # 默认关闭时这一段完全跳过，合并只由内容特征驱动。
        if height_gate and not _is_fragment(row, median_height):
            flush()
            row.row_index = len(merged)
            merged.append(row)
            continue

        if not group:
            group, has_text, has_num = [row], row_text, row_num
            if has_text and has_num:
                flush()
            continue

        if has_text and has_num:
            # 当前逻辑行已完整：只有「纯文本碎片」才可能是它的续行
            if row_text and not row_num:
                group.append(row)
            else:
                flush()
                group, has_text, has_num = [row], row_text, row_num
                if has_text and has_num:
                    flush()
        else:
            group.append(row)
            has_text = has_text or row_text
            has_num = has_num or row_num

    flush()
    return merged

@dataclass(slots=True)
class Document:
    """解析后的文档。"""

    path: Path
    n_pages: int
    tables: list[PageTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        total_rows = sum(len(t.rows) for t in self.tables)
        return (
            f"{self.path.name}: {self.n_pages} 页, "
            f"{len(self.tables)} 张表, {total_rows} 行"
        )


def load_document(
    pdf_path: str | Path,
    strategy: str = "text",
    merge_fragments: bool = True,
) -> Document:
    """解析 PDF，返回所有识别到的表格行（带页码与坐标）。

    Parameters
    ----------
    merge_fragments:
        是否重建被 find_tables 拆散的逻辑行。默认开启 ——
        真实年报是无框线表，逻辑一行常占 2-3 个视觉行，
        不重建的话数值根本不在标签所在的那一行里。
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    doc = Document(path=path, n_pages=0, tables=[])
    pdf = pymupdf.open(str(path))
    try:
        doc.n_pages = pdf.page_count
        for pno in range(pdf.page_count):
            page = pdf[pno]
            try:
                finder = page.find_tables(strategy=strategy)
            except Exception as exc:  # noqa: BLE001
                doc.warnings.append(f"第 {pno + 1} 页表格识别失败: {exc}")
                continue

            for tbl in finder.tables:
                extracted = tbl.extract()
                pt = PageTable(page=pno + 1, bbox=tuple(tbl.bbox))  # type: ignore[arg-type]
                # strict=False：真实年报的表格常有参差行，一处 ragged
                # 不应让整份审计失败 —— 截断后继续，缺的部分交给下游三态处理。
                for ridx, (vals, row_obj) in enumerate(
                    zip(extracted, tbl.rows, strict=False)
                ):
                    cells = [tuple(c) if c else None for c in (row_obj.cells or [])]
                    pt.rows.append(
                        TableRow(
                            page=pno + 1,
                            row_index=ridx,
                            values=[normalize_text(v or "").strip() for v in vals],
                            cells=cells,
                        )
                    )
                if merge_fragments:
                    pt.rows = merge_wrapped_rows(pt.rows)
                doc.tables.append(pt)
    finally:
        pdf.close()

    return doc


def page_text_lines(pdf_path: str | Path, page_no: int) -> list[tuple[int, BBox, str]]:
    """取某页的文本行（含坐标），用于给表格补上下文（如表标题）。

    单页版本。**批量场景请用** :func:`all_text_lines` ——
    本函数每调用一次就 open/close 一次 PDF，对 200 页年报来说是 200 次
    文件打开，实测会让全量评测慢一个数量级。
    """
    with pymupdf.open(str(pdf_path)) as pdf:
        return _lines_of_page(pdf, page_no)


def _lines_of_page(pdf, page_no: int) -> list[tuple[int, BBox, str]]:
    page = pdf[page_no - 1]
    out: list[tuple[int, BBox, str]] = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if text.strip():
                out.append(
                    (page_no, tuple(line["bbox"]), text.strip())
                )  # type: ignore[arg-type]
    return out


def all_text_lines(
    pdf_path: str | Path, max_pages: int | None = None
) -> list[tuple[int, BBox, str]]:
    """一次打开，取全部页面的文本行。

    这是 :func:`page_text_lines` 的批量版本，口径识别、标签重建等
    需要全文扫描的场景都应该用这个。
    """
    out: list[tuple[int, BBox, str]] = []
    with pymupdf.open(str(pdf_path)) as pdf:
        limit = pdf.page_count if max_pages is None else min(
            max_pages, pdf.page_count
        )
        for pno in range(1, limit + 1):
            out.extend(_lines_of_page(pdf, pno))
    return out
