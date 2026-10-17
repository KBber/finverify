"""打包 v0.5.0 交付件：排除缓存与生成物，解压后独立验证。"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

import _paths

ROOT = _paths.ROOT
# 交付件放桌面：那是用户第一眼会找的地方。第一个命令行参数可换位置。
OUT = (
    pathlib.Path(sys.argv[1])
    if len(sys.argv) > 1
    else _paths.desktop() / "finverify-v0.5.zip"
)

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
# CLI 的退出码契约：0=无问题 1=发现问题 2=输入不可用。
# 先跑一遍拿实际码，再逐条比对，所以这张表要用两次。
EXIT_CODES = (
    ("samples/demo_annual_report.pdf", 0),
    ("samples/demo_injected_error.pdf", 1),
    ("samples/demo_injected_error_tax.pdf", 1),
    ("samples/does_not_exist.pdf", 2),
)
L: list[str] = []


def collect() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts):
            continue
        if p.name.startswith("bench_") and p.name.endswith(".pdf"):
            continue
        out.append(p)
    return out


files = collect()
if OUT.exists():
    OUT.unlink()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, p.relative_to(ROOT).as_posix())

L.append(f"打包 {len(files)} 个文件 -> {OUT}")
L.append(f"体积 {OUT.stat().st_size / 1024:.1f} KB")
L.append("")

tmp = pathlib.Path(tempfile.mkdtemp()) / "pkg"
with zipfile.ZipFile(OUT) as z:
    z.extractall(tmp)

L.append("=== 解压后独立验证 ===")
env_cmd = [sys.executable, "-m", "pytest", "tests", "-q"]
r = subprocess.run(env_cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8")
L.append(f"pytest: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}")

codes = {}
for name, _expect in EXIT_CODES:
    r = subprocess.run(
        [sys.executable, "-m", "finverify.cli", "audit", name],
        cwd=tmp, capture_output=True, text=True, encoding="utf-8",
    )
    codes[name] = r.returncode
L.append("CLI 退出码契约:")
ok = True
for name, expect in EXIT_CODES:
    got = codes[name]
    flag = "OK" if got == expect else "不符"
    if got != expect:
        ok = False
    L.append(f"  {name:<44} 期望{expect} 实际{got}  {flag}")

L.append("")
L.append("打包文件清单（顶层）:")
L.append("  " + ", ".join(sorted({p.relative_to(ROOT).parts[0] for p in files})))

report = _paths.note("package_report.txt")
report.write_text("\n".join(L), encoding="utf-8")
shutil.rmtree(tmp, ignore_errors=True)
print("written ->", report)
print("CLI 契约:", "全部符合" if ok else "有偏差")
