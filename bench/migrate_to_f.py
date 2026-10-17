"""把 finverify 项目复制到新位置，并在新位置独立验证。

用**复制**而不是移动：迁移出错时源目录还在，可逆。
复制完会在新位置真跑一遍测试与 CLI 退出码契约，
「文件拷过去了」不等于「拷过去还能用」。

用法：``python bench/migrate_to_f.py <源目录> <目标目录>``，
不传参数则迁移到项目同级的 ``finverify``。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import time

import _paths

SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _paths.ROOT
DST = (
    pathlib.Path(sys.argv[2])
    if len(sys.argv) > 2
    else _paths.ROOT.parent / "finverify"
)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git"}

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
L: list[str] = []


def collect(base: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if SKIP_DIRS & set(rel.parts):
            continue
        if p.name.startswith("bench_") and p.name.endswith(".pdf"):
            continue
        out.append(p)
    return out


if not DST.exists():
    DST.mkdir(parents=True)
existing = list(DST.iterdir())
if existing:
    raise SystemExit(
        f"目标目录非空（{len(existing)} 项），拒绝覆盖："
        + ", ".join(x.name for x in existing[:8])
    )

files = collect(SRC)
for p in files:
    rel = p.relative_to(SRC)
    target = DST / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, target)

total = sum((DST / p.relative_to(SRC)).stat().st_size for p in files)
L.append(f"复制 {len(files)} 个文件，合计 {total / 1024 / 1024:.2f} MB")
L.append(f"  {SRC}")
L.append(f"  -> {DST}")
L.append("")

L.append("=== 新位置独立验证 ===")
env = {"PYTHONPATH": str(DST / "src")}
import os  # noqa: E402

run_env = {**os.environ, **env}

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests", "-q"],
    cwd=DST, capture_output=True, text=True, encoding="utf-8", env=run_env,
)
tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
L.append(f"  pytest      -> {tail}")

for name, expect in (
    ("samples/demo_annual_report.pdf", 0),
    ("samples/demo_injected_error.pdf", 1),
    ("samples/demo_injected_error_tax.pdf", 1),
    ("samples/does_not_exist.pdf", 2),
):
    r = subprocess.run(
        [sys.executable, "-m", "finverify.cli", "audit", name],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", env=run_env,
    )
    ok = "OK" if r.returncode == expect else "不符"
    L.append(f"  {name:<44} 期望{expect} 实际{r.returncode}  {ok}")

L.append("")
r = subprocess.run(
    [sys.executable, "-c", "import finverify; print(finverify.__file__)"],
    cwd=DST, capture_output=True, text=True, encoding="utf-8", env=run_env,
)
L.append(f"  导入自: {r.stdout.strip() or r.stderr.strip()[-160:]}")
L.append(f"  耗时:   {time.strftime('%Y-%m-%d %H:%M:%S')}")

out = pathlib.Path(__file__).with_name("migration_report.txt")
out.write_text("\n".join(L), encoding="utf-8")
print("written ->", out)
