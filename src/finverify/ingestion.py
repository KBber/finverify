"""文档解析层 —— PDF → 带坐标的表格行。

这一层的唯一职责是**保住坐标**。文本可以丢，但只要还知道
"这个值在第几页、第几行、第几列"，溯源能力就在。

PyMuPDF 的表格识别默认走 ``lines`` 策略，只认有完整框线的表；
中文财报里大量是无框线表，这里统一走 ``text`` 策略。
"""

from __future__ import annotations

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
    pdf_path: str | Path, strategy: str = "text"
) -> Document:
    """解析 PDF，返回所有识别到的表格行（带页码与坐标）。"""
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
                doc.tables.append(pt)
    finally:
        pdf.close()

    return doc


def page_text_lines(pdf_path: str | Path, page_no: int) -> list[tuple[int, BBox, str]]:
    """取某页的文本行（含坐标），用于给表格补上下文（如表标题）。"""
    pdf = pymupdf.open(str(pdf_path))
    try:
        page = pdf[page_no - 1]
        out: list[tuple[int, BBox, str]] = []
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", []))
                if text.strip():
                    out.append((page_no, tuple(line["bbox"]), text.strip()))  # type: ignore[arg-type]
        return out
    finally:
        pdf.close()
