"""把重下的 873576 完整年报换入 eval 集，并同步 manifest。

旧文件是 0.18 MB / 4 页的「更正公告」空壳（去重打分 bug 的产物），
新文件是 2.22 MB / 141 页的年报正文。
"""

from __future__ import annotations

import json
import pathlib
import shutil

import _paths

new_dir = _paths.scratch_dir("fv-redl")
data_dir = _paths.data_dir()
CODE = "873576"

new_pdf = next(new_dir.glob(f"{CODE}_*.pdf"))
dest = _paths.pdf_for(CODE)
new_man = json.loads((new_dir / "manifest.json").read_text(encoding="utf-8"))[0]

out: list[str] = []
out.append(f"旧: {dest.name} {dest.stat().st_size / 1024 / 1024:.2f} MB")
shutil.copy2(new_pdf, dest)
out.append(f"新: {dest.name} {dest.stat().st_size / 1024 / 1024:.2f} MB")

man_path = data_dir / "manifest.json"
man = json.loads(man_path.read_text(encoding="utf-8"))
hit = 0
for m in man:
    if m["sec_code"] == CODE:
        m.update(
            title=new_man["title"],
            url=new_man["url"],
            page_url=new_man["page_url"],
            bytes=dest.stat().st_size,
            revised=new_man["revised"],
        )
        hit += 1
man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
out.append(f"manifest 更新 {hit} 条，共 {len(man)} 条")
out.append(f"manifest 合计 {sum(m['bytes'] for m in man) / 1024 / 1024:.1f} MB")

pathlib.Path(__file__).with_name("redl_check.txt").write_text(
    "\n".join(out) + "\n", encoding="utf-8"
)
print("ok")
