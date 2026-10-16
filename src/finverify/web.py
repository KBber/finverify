"""静态可视化 Demo 生成器。

为什么生成静态 HTML 而不是起服务
-------------------------------
求职场景下，一个"双击就能打开、不用装环境、不用联网"的文件
比一个需要 ``pip install`` + ``uvicorn`` 的仓库有用得多。
生成物是**单个 HTML 文件**，PDF 页面以 base64 内嵌，
溯源高亮用绝对定位的 div 叠加实现，纯前端零依赖。

核心交互：**点左边的数字 -> 右边自动跳到原文位置并高亮**。
这正是 finverify 的核心主张 —— 结论必须能被验证。
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from finverify.pipeline import AuditReport
from finverify.schemas import VerificationStatus

ZOOM = 2.0
"""渲染倍率。2.0 在 1080p 屏上足够清晰，且文件体积可控。"""

_STAMP = {"PASS": "PASS", "FAIL": "FAIL", "REVIEW_REQUIRED": "REVIEW"}

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128; --line:#30363d;
  --fg:#e6edf3; --dim:#8b949e; --accent:#58a6ff;
  --pass:#3fb950; --fail:#f85149; --warn:#d29922;
}
html,body{height:100%}
body{
  background:var(--bg); color:var(--fg);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  display:flex; flex-direction:column; overflow:hidden;
}
header{
  padding:14px 22px; border-bottom:1px solid var(--line);
  background:var(--panel); display:flex; align-items:center; gap:16px;
  flex-wrap:wrap; flex:0 0 auto;
}
h1{font-size:17px; font-weight:600; letter-spacing:.3px}
.badge{
  padding:3px 11px; border-radius:999px; font-size:12px; font-weight:700;
  letter-spacing:.6px;
}
.badge.PASS{background:rgba(63,185,80,.15);color:var(--pass);border:1px solid rgba(63,185,80,.4)}
.badge.FAIL{background:rgba(248,81,73,.15);color:var(--fail);border:1px solid rgba(248,81,73,.4)}
.badge.REVIEW_REQUIRED{background:rgba(210,153,34,.15);color:var(--warn);border:1px solid rgba(210,153,34,.4)}
.meta{color:var(--dim); font-size:12.5px}
.meta b{color:var(--fg); font-weight:600}

main{flex:1 1 auto; display:flex; min-height:0; min-width:0}

/* ---------- 左栏 ---------- */
aside{
  width:400px; flex:0 0 400px; border-right:1px solid var(--line);
  background:var(--panel); overflow-y:auto; padding:16px; min-width:0;
}
h2{
  font-size:11.5px; text-transform:uppercase; letter-spacing:1.2px;
  color:var(--dim); margin:18px 0 9px; font-weight:700;
}
h2:first-child{margin-top:0}

.rule{
  border:1px solid var(--line); border-radius:8px; padding:10px 12px;
  margin-bottom:8px; background:var(--panel2);
}
.rule .top{display:flex; align-items:center; gap:8px; margin-bottom:4px}
.rule .rid{
  font:600 11px ui-monospace,Consolas,monospace; color:var(--dim);
  letter-spacing:.4px;
}
.rule .desc{color:var(--dim); font-size:11.5px; line-height:1.5}
.rule .delta{
  font:600 12px ui-monospace,Consolas,monospace; margin-top:5px;
}
.rule.PASS{border-left:3px solid var(--pass)}
.rule.FAIL{border-left:3px solid var(--fail)}
.rule.REVIEW_REQUIRED{border-left:3px solid var(--warn)}
.rule.PASS .delta{color:var(--pass)}
.rule.FAIL .delta{color:var(--fail)}
.rule.REVIEW_REQUIRED .delta{color:var(--warn)}

.item{
  display:flex; justify-content:space-between; align-items:baseline; gap:10px;
  padding:8px 11px; border:1px solid var(--line); border-radius:7px;
  margin-bottom:5px; cursor:pointer; background:var(--panel2);
  transition:background .12s,border-color .12s,transform .12s;
}
.item:hover{background:#21262d; border-color:#484f58; transform:translateX(2px)}
.item.sel{background:rgba(88,166,255,.13); border-color:var(--accent)}
.item .lbl{font-size:12.5px}
.item .src{
  font:10.5px ui-monospace,Consolas,monospace; color:var(--dim);
  white-space:nowrap;
}
.item .val{
  font:600 13px ui-monospace,Consolas,monospace;
  color:var(--accent); white-space:nowrap;
}
.item.tainted{border-color:rgba(248,81,73,.55)}
.item.tainted .val{color:var(--fail)}
.item.tainted .why{
  font-size:10.5px; color:var(--fail); margin-top:3px; line-height:1.4;
}

/* ---------- 右栏 ---------- */
/* 关键：页面宽度必须自适应容器。高亮层用百分比定位，
   这样无论页面被缩放到多小，坐标都对得上。 */
.viewer{
  flex:1 1 auto; min-width:0; overflow:hidden auto; padding:22px 18px 40px;
  background:#010409; display:flex; flex-direction:column;
  align-items:center; gap:22px;
}
.page{
  position:relative; width:100%; max-width:860px;
  /* 关键：作为 column flex 的子项，必须禁止收缩，
     否则总高度超出容器时会被压扁成一页一条标题。 */
  flex:0 0 auto;
  box-shadow:0 6px 26px rgba(0,0,0,.6); border-radius:4px;
  overflow:hidden; background:#fff;
}
.page img{display:block; width:100%; height:auto}
.page .pno{
  position:absolute; top:8px; left:10px; z-index:3;
  background:rgba(1,4,9,.82); color:var(--dim);
  font:600 10.5px ui-monospace,Consolas,monospace;
  padding:2px 7px; border-radius:4px; letter-spacing:.5px;
}
.hl{
  position:absolute; border:2.5px solid var(--accent);
  background:rgba(88,166,255,.22); border-radius:3px;
  pointer-events:none; opacity:0; transition:opacity .25s;
}
.hl.on{opacity:1; animation:pulse 1.5s ease-out 2}
.hl.bad{border-color:var(--fail); background:rgba(248,81,73,.26)}
@keyframes pulse{
  0%{box-shadow:0 0 0 0 rgba(88,166,255,.55)}
  70%{box-shadow:0 0 0 14px rgba(88,166,255,0)}
  100%{box-shadow:0 0 0 0 rgba(88,166,255,0)}
}
.hint{
  color:var(--dim); font-size:12.5px; text-align:center;
  padding:8px 16px; border:1px dashed var(--line); border-radius:8px;
}
footer{
  flex:0 0 auto; padding:8px 22px; border-top:1px solid var(--line);
  background:var(--panel); color:var(--dim); font-size:11.5px;
  display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px;
}
footer code{color:var(--accent); font-family:ui-monospace,Consolas,monospace}
"""


@dataclass(slots=True)
class RenderedPage:
    page_no: int
    data_uri: str
    width_px: float
    height_px: float


def _page_size_pt(pdf_path: str | Path) -> tuple[float, float]:
    """取 PDF 页面尺寸（点）。高亮的百分比换算依赖它。"""
    doc = pymupdf.open(str(pdf_path))
    try:
        r = doc[0].rect
        return float(r.width), float(r.height)
    finally:
        doc.close()


def _render_pages(pdf_path: str | Path, max_pages: int = 12) -> list[RenderedPage]:
    """把 PDF 页面渲染成 PNG（base64 内嵌）。"""
    out: list[RenderedPage] = []
    doc = pymupdf.open(str(pdf_path))
    try:
        n = min(doc.page_count, max_pages)
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
            png = pix.tobytes("png")
            out.append(
                RenderedPage(
                    page_no=i + 1,
                    data_uri="data:image/png;base64,"
                    + base64.b64encode(png).decode("ascii"),
                    width_px=pix.width,
                    height_px=pix.height,
                )
            )
    finally:
        doc.close()
    return out


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def build_static_report(
    report: AuditReport, out_html: str | Path | None = None
) -> Path:
    """把一份审计结果渲染成单文件静态 HTML。"""
    pdf_path = report.source
    pages = _render_pages(pdf_path)
    page_w_pt, page_h_pt = _page_size_pt(pdf_path)

    # ---- 标记出"参与 FAIL 校验"的指标，让 UI 能把它们染红 ----------
    tainted: set[str] = set()
    rule_by_kind: dict[str, str] = {}
    for r in report.rule_results:
        if r.status is VerificationStatus.FAIL:
            tainted.add(r.rule_id)
            rule_by_kind[r.rule_id] = r.detail

    # 找出可疑指标：出现在 FAIL 规则里的 kind
    bad_kinds: set[str] = set()
    if report.status is VerificationStatus.FAIL:
        lhs_kinds: dict[str, list[str]] = {}
        for r in report.rule_results:
            if r.status is VerificationStatus.FAIL:
                lhs_kinds.setdefault(r.rule_id, [])
        # 简化为：资产负债表恒等式失败 -> 权益/资产/负债 标红
        for r in report.rule_results:
            if r.status is VerificationStatus.FAIL and r.rule_id == "BS_BALANCE":
                bad_kinds |= {"total_equity", "total_assets", "total_liabilities"}
            if r.status is VerificationStatus.FAIL and r.rule_id == "IS_NET_PROFIT":
                bad_kinds |= {"net_profit", "total_profit", "income_tax"}

    # ---- 左栏：规则 ----
    rules_html = []
    for r in report.rule_results:
        cls = _STAMP[r.status.value]
        delta = (
            f"Δ = {r.delta:,.2f}"
            if r.delta is not None
            else "证据不足，需人工复核"
        )
        rules_html.append(
            f'<div class="rule {cls}">'
            f'<div class="top"><span class="badge {cls}">{cls}</span>'
            f'<span class="rid">{html.escape(r.rule_id)}</span></div>'
            f'<div class="desc">{html.escape(r.detail)}</div>'
            f'<div class="delta">{html.escape(delta)}</div>'
            f"</div>"
        )

    # ---- 左栏：指标 ----
    items_html = []
    for m in sorted(
        report.metrics, key=lambda x: (x.statement.value, x.kind.value)
    ):
        ev = m.number.evidence
        cell = (
            f"行{ev.cell[0]}列{ev.cell[1]}" if ev.cell else "文本"
        )
        src = f"p{ev.page} · {cell}"
        is_bad = m.kind.value in bad_kinds
        why = ""
        if is_bad:
            related = [
                r.detail
                for r in report.rule_results
                if r.status is VerificationStatus.FAIL
            ]
            if related:
                why = (
                    f'<div class="why">勾稽不符：{html.escape(related[0][:110])}…</div>'
                )
        items_html.append(
            f'<div class="item{" tainted" if is_bad else ""}" '
            f'data-page="{ev.page}" '
            f'data-bbox="{",".join(str(round(c, 2)) for c in ev.bbox)}" '
            f'data-bad="{"1" if is_bad else "0"}">'
            f'<div><div class="lbl">{html.escape(m.label)}</div>'
            f'<div class="src">{html.escape(src)}</div>{why}</div>'
            f'<div class="val">{_fmt(m.number.value)}</div>'
            f"</div>"
        )

    # ---- 右栏：页面 + 高亮层 ----
    pages_html = []
    for p in pages:
        pages_html.append(
            f'<div class="page" id="pg{p.page_no}" '
            f'data-w="{p.width_px:.0f}">'
            f'<div class="pno">PAGE {p.page_no}</div>'
            f'<img src="{p.data_uri}" width="{p.width_px:.0f}" '
            f'height="{p.height_px:.0f}" alt="page {p.page_no}">'
            f'<div class="hl" id="hl{p.page_no}"></div>'
            f"</div>"
        )

    status = _STAMP[report.status]
    title = Path(pdf_path).name

    return _write(
        out_html,
        f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>finverify · {html.escape(title)} · {status}</title>
<style>{_CSS}</style></head><body>

<header>
  <h1>finverify · 财报数字溯源审计</h1>
  <span class="badge {status}">{status}</span>
  <span class="meta">数据源 <b>{html.escape(title)}</b></span>
  <span class="meta">报告期 <b>{html.escape(report.period)}</b></span>
  <span class="meta">指标 <b>{len(report.metrics)}</b></span>
  <span class="meta">勾稽 <b>{len(report.rule_results)}</b> 条</span>
</header>

<main>
  <aside>
    <h2>勾稽校验</h2>
    {''.join(rules_html)}
    <h2>可溯源指标（点击定位原文）</h2>
    {''.join(items_html)}
  </aside>
  <section class="viewer" id="viewer">
    <div class="hint">
      点击左侧任意指标 → 右侧自动跳转并在原文中高亮其所在单元格
    </div>
    {''.join(pages_html)}
  </section>
</main>

<footer>
  <span>每个数字都可回溯到 <code>page + bbox + cell</code>，结论不依赖信任，只依赖验证</span>
  <span>REVIEW_REQUIRED 表示证据不足，不等同于数据错误</span>
</footer>

<script>
const PW = {page_w_pt}, PH = {page_h_pt};   // PDF 页面尺寸（点）
const viewer = document.getElementById('viewer');
const items = [...document.querySelectorAll('.item')];

function clearAll(){{
  document.querySelectorAll('.hl').forEach(e => e.className = 'hl');
  items.forEach(e => e.classList.remove('sel'));
}}

function focusItem(el){{
  clearAll();
  el.classList.add('sel');
  const pg = document.getElementById('pg' + el.dataset.page);
  const hl = document.getElementById('hl' + el.dataset.page);
  if(!pg || !hl) return;

  // PDF 点坐标 -> 页面百分比（页面显示宽度随容器变化，故必须用百分比）
  const [x0,y0,x1,y1] = el.dataset.bbox.split(',').map(Number);
  const px = 0.4, py = 0.25;               // 视觉留白（百分点）
  hl.style.left   = (x0 / PW * 100 - px) + '%';
  hl.style.top    = (y0 / PH * 100 - py) + '%';
  hl.style.width  = ((x1 - x0) / PW * 100 + px * 2) + '%';
  hl.style.height = ((y1 - y0) / PH * 100 + py * 2) + '%';
  hl.className = 'hl on' + (el.dataset.bad === '1' ? ' bad' : '');

  // 用 rect 差值算滚动量，避免 offsetTop 参照系错误
  const vr = viewer.getBoundingClientRect();
  const pr = pg.getBoundingClientRect();
  viewer.scrollTo({{ top: viewer.scrollTop + (pr.top - vr.top) - 28,
                    behavior:'smooth' }});
}}

items.forEach(el => el.addEventListener('click', () => focusItem(el)));

// 初始：自动聚焦第一个被判定有问题的指标
const firstBad = items.find(e => e.dataset.bad === '1') || items[0];
if(firstBad) setTimeout(() => focusItem(firstBad), 400);
</script>
</body></html>""",
    )


def _write(out_html: str | Path | None, content: str) -> Path:
    if out_html is None:
        from finverify.samples import default_samples_dir

        p = default_samples_dir() / "demo_report.html"
    else:
        p = Path(out_html)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p
