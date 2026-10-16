"""校验重下的 873576 年报是否真的是完整正文，再决定是否替换旧文件。"""

from __future__ import annotations

import json
import pathlib

import pymupdf

new_dir = pathlib.Path(r"C:\Users\Admin\AppData\Local\Temp\fv-redl")
old = pathlib.Path(r"G:\minimax_workspace\github-kit\data\real\873576_天力复合.pdf")

new_pdf = next(new_dir.glob("*.pdf"))
out: list[str] = []


def probe(p: pathlib.Path, tag: str) -> None:
    try:
        with pymupdf.open(str(p)) as d:
            pages = d.page_count
            txt = "".join(d[i].get_text() for i in range(min(pages, 40)))
    except Exception as exc:  # noqa: BLE001
        out.append(f"{tag}: 打不开 {exc}")
        return
    marks = [k for k in ("资产负债表", "利润表", "现金流量表") if k in txt]
    out.append(f"{tag}: {p.name} {p.stat().st_size / 1024 / 1024:.2f} MB {pages} 页")
    out.append(f"    前 40 页命中表名: {marks or '无'}")


probe(new_pdf, "新下")
if old.exists():
    probe(old, "原有")

man = new_dir / "manifest.json"
if man.exists():
    for m in json.loads(man.read_text(encoding="utf-8")):
        out.append(f"    标题: {m['title']}")
        out.append(f"    URL:  {m['url']}")

pathlib.Path(r"G:\minimax_workspace\github-kit\finverify\bench\redl_check.txt").write_text(
    "\n".join(out), encoding="utf-8"
)
print("ok")
