"""说明桌面 5 个 finverify 压缩包的内容与来源。"""

from __future__ import annotations

import pathlib
import zipfile

import _paths

DESKTOP = _paths.desktop()
L: list[str] = []

zips = sorted(DESKTOP.glob("finverify-v*.zip"), key=lambda p: p.name)
L.append("桌面上的 finverify 压缩包")
L.append("=" * 76)
for z in zips:
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
        tops = sorted({n.split("/")[0] for n in names})
    ver = z.stem.replace("finverify-", "")
    pyproj = next(
        (n for n in names if n.endswith("pyproject.toml")), None
    )
    L.append("")
    L.append(f"{z.name}   {z.stat().st_size / 1024:>7.1f} KB   {len(names)} 个文件")
    L.append(f"  顶层: {', '.join(tops)}")
    if pyproj:
        with zipfile.ZipFile(z) as f:
            txt = f.read(pyproj).decode("utf-8", "replace")
        ver_line = next(
            (ln for ln in txt.splitlines() if ln.startswith("version")), "?"
        )
        L.append(f"  内含 pyproject: {ver_line.strip()}")

L.append("")
L.append("=" * 76)
L.append("这些包是什么")
L.append("")
L.append("它们是 finverify 项目在每个版本节点上的**只读快照**，")
L.append("用途是「拿到没有开发环境的机器上双击就能看」：")
L.append("  - 直接双击 samples/*.html 就能看可交互报告，不需要装 Python；")
L.append("  - 解压后 `pip install -e .` 就是一个可运行的完整项目。")
L.append("")
L.append("真正的项目源码在项目目录里，不在桌面：")
L.append(f"  {_paths.ROOT}")
L.append("")
L.append("体积差异也说明它们是历史快照 —— v0.1 只有 18 KB（刚有骨架），")
L.append("v0.4 涨到 1.1 MB（加入了多模型评测），v0.5 反而回到 895 KB")
L.append("（把 11 个误入库的缓存文件和生成物清掉了）。")
L.append("")
L.append("需要保留的只有最新的 v0.5.zip，前四个是历史里程碑。")

out = pathlib.Path(__file__).with_name("zip_inventory.txt")
out.write_text("\n".join(L), encoding="utf-8")
print("written ->", out)
