"""行重建（merge_wrapped_rows）的回归测试。

用例全部取自真实年报实测，不是构造出来的理想数据：

- 永泰运 001228 2023 年报 p103，利润表里
  ``四、利润总额(亏损总额以"-"号填列)`` 这个标签被 find_tables
  拆成 3 行，**数值夹在中间**，标签行里根本没有数字。
- 纳尔股份 002825 出现过的 ``四、净利润  -  -``：破折号在中文
  会计里表示零/无。不认它，这行**完整数据**会被当成「标签碎片」
  去吞掉下一行，把 net_profit 读成 0.00 —— 实测假阳性。
- 行高闸门被证伪：18 份年报指标数 140 → 140，一行都没多抽出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finverify.ingestion import (  # noqa: E402
    TableRow,
    _cell_is_numeric,
    _row_height,
    merge_wrapped_rows,
)


def mk(values: list[str], y0: float, y1: float, page: int = 103) -> TableRow:
    """造一行，列宽按 n 列均分，行高由 y0/y1 决定。

    注意：**空白值也要有 bbox**。永泰运 p103 实测的空白行 r25
    是 ``cells=[(57,257,304,264), (304,257,465,264), ...]``，
    坐标齐全只是文本为空 —— PyMuPDF 不会因为抽不出文字就丢掉格子。
    行高是按 bbox 算的，把空格子当成没有格子会算错行高。
    """
    n = len(values)
    cells = [(57.0 + 200 * i, y0, 57.0 + 200 * (i + 1), y1) for i in range(n)]
    return TableRow(page=page, row_index=-1, values=values, cells=cells)


# ---- 1. 核心用例：永泰运 p103 实测的三行碎片 ----


def test_wrapped_label_merged_with_interior_number():
    """标签首行 + 数值行 + 标签续行 -> 一个完整逻辑行。"""
    rows = [
        mk(['四、利润总额(亏损总额以"-"号', "", ""], 236, 245),
        mk(["", "215,459,982.04", "417,326,200.27"], 245, 251),
        mk(["填列)", "", ""], 251, 257),
        mk(["", "", ""], 257, 264),  # 全空行
        mk(["减:所得税费用", "62,978,276.33", "110,948,764.62"], 264, 273),
    ]
    out = merge_wrapped_rows(rows)

    assert len(out) == 2, "应合成 2 行（利润总额 / 减:所得税费用）"
    first = out[0]
    assert first.values[0] == '四、利润总额(亏损总额以"-"号填列)'
    assert first.values[1] == "215,459,982.04"
    assert first.values[2] == "417,326,200.27"
    # 数值必须留在这一行里 —— 这正是重建要解决的问题
    assert _cell_is_numeric(first.values[1])


def test_merged_row_bbox_is_vertical_union():
    """同列 bbox 取纵向并集，溯源坐标不因重建而丢失。

    注意并集是**整段纵向**：PyMuPDF 对抽不出文字的格子照样返回 bbox
    （永泰运 p103 r22 的三个 cells 全在，只是 values 后两个为空），
    所以数值列的并集覆盖 236→257 整段。这与真实抽取结果一致。
    """
    rows = [
        mk(['四、利润总额(亏损总额以"-"号', "", ""], 236, 245),
        mk(["", "215,459,982.04", "417,326,200.27"], 245, 251),
        mk(["填列)", "", ""], 251, 257),
    ]
    out = merge_wrapped_rows(rows)

    assert len(out) == 1
    for col, (y0, y1) in enumerate([(236, 245), (245, 251), (251, 257)]):
        box = out[0].cells[col]
        assert box is not None
        # 并集 = 该列所有原始 bbox 的纵向覆盖
        assert box[1] == min(236, y0) and box[3] == max(257, y1)
    assert out[0].row_bbox[1] == 236 and out[0].row_bbox[3] == 257


def test_blank_rows_are_dropped():
    rows = [mk(["", "", ""], 10, 20), mk(["资产总计", "1,000", "2,000"], 20, 29)]
    out = merge_wrapped_rows(rows)
    assert len(out) == 1
    assert out[0].values[0] == "资产总计"


# ---- 2. 破折号归零：不得吞掉下一行 ----


@pytest.mark.parametrize("dash", ["-", "\u2014", "\u2212", "\uff0d", "\u2013"])
def test_dash_counts_as_zero_number(dash: str):
    """中文会计惯例：破折号表示零/无，必须当数值看。"""
    assert _cell_is_numeric(dash)
    assert _cell_is_numeric(f" {dash} ")


def test_zero_row_does_not_swallow_next_row():
    """``四、净利润 - -`` 是一行完整数据，绝不能并掉下一行。"""
    rows = [
        mk(["四、净利润", "-", "-"], 100, 109),
        mk(["归属于母公司股东的净利润", "10,899,177.63", "9,000,000.00"], 109, 118),
    ]
    out = merge_wrapped_rows(rows)

    assert len(out) == 2, "破折号行必须独立成行"
    assert out[0].values == ["四、净利润", "-", "-"]
    # 关键回归：net_profit 不能被读成 0.00，也不能串到下一行去
    assert out[1].values[1] == "10,899,177.63"


def test_leading_negative_number_still_parses():
    rows = [
        mk(["归属于母公司股东的净利润", "-255,748,522.08", "-1,000.00"], 100, 109),
        mk(["少数股东损益", "12,000.00", "13,000.00"], 109, 118),
    ]
    out = merge_wrapped_rows(rows)
    assert len(out) == 2
    # 负号不能被当成破折号归零
    assert out[0].values[1] == "-255,748,522.08"


# ---- 3. 行高闸门：已证伪，默认必须关闭 ----


def test_height_gate_off_by_default_merges_normal_height_rows():
    """闸门默认关闭。永泰运实测标签起始行 9pt，与常规行同高 ——
    闸门若开着会把它判成「非碎片」原样放行，合并彻底失效。"""
    rows = [
        mk(['四、利润总额(亏损总额以"-"号', "", ""], 236, 245),  # 9pt
        mk(["", "215,459,982.04", "417,326,200.27"], 245, 251),  # 6pt
        mk(["填列)", "", ""], 251, 257),  # 6pt
    ]
    # 再加一批 9pt 的常规行，撑高中位数，让闸门若开就会误判
    rows += [mk([f"科目{i}", "1,000", "2,000"], 260 + 9 * i, 269 + 9 * i) for i in range(5)]

    assert merge_wrapped_rows(rows)[0].values[1] == "215,459,982.04"
    gated = merge_wrapped_rows(rows, height_gate=True)
    assert gated[0].values[1] == "", "闸门开启时确实不再合并 —— 这就是它无效的证据"


def test_row_height_measured_from_bbox():
    r = mk(["x", "1", "2"], 100, 109)
    assert _row_height(r) == pytest.approx(9)
    assert _row_height(mk(["", "", ""], 100, 109)) == pytest.approx(9)


# ---- 4. 完整数据行绝不与邻行合并 ----


def test_complete_rows_never_merge():
    rows = [
        mk(["营业收入", "1,000.00", "2,000.00"], 100, 109),
        mk(["营业成本", "500.00", "600.00"], 109, 118),
        mk(["净利润", "100.00", "200.00"], 118, 127),
    ]
    out = merge_wrapped_rows(rows)
    assert len(out) == 3
    assert [r.values[0] for r in out] == ["营业收入", "营业成本", "净利润"]


def test_row_index_is_renumbered():
    rows = [
        mk(['四、利润总额(亏损总额以"-"号', "", ""], 236, 245),
        mk(["", "215,459,982.04", "417,326,200.27"], 245, 251),
        mk(["填列)", "", ""], 251, 257),
    ]
    out = merge_wrapped_rows(rows)
    assert [r.row_index for r in out] == [0]
