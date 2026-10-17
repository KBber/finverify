"""v0.5.0 交付前最终复核。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from incremental_push import gh, local_files  # noqa: E402

# 控制台是 GBK，中文与个别码位编不出去；统一改成 UTF-8 且不抛异常
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = "KBber/finverify"

ref = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
main_sha = ref["object"]["sha"]
commit = gh("GET", f"/repos/{REPO}/commits/{main_sha}")
print(f"main        = {main_sha[:7]}  {commit['commit']['message'].splitlines()[0]}")

tag = gh("GET", f"/repos/{REPO}/git/ref/tags/v0.5.0")
tag_obj = gh("GET", f"/repos/{REPO}/git/tags/{tag['object']['sha']}")
print(
    f"tag v0.5.0  -> {tag_obj['object']['sha'][:7]}"
    f"  {'一致' if tag_obj['object']['sha'] == main_sha else '不一致!'}"
)

rel = gh("GET", f"/repos/{REPO}/releases/tags/v0.5.0")
print(f"release     = {rel['html_url']}")
body = rel["body"]
checks = {
    "68 项测试": "68 项测试" in body,
    "A/B 数字 160": "| 160 | (+12)" in body or "**160** (+12)" in body,
    "七个缺陷": "七个缺陷" in body,
    "CI 那一条": "只有 CI 红" in body,
}
for k, v in checks.items():
    print(f"  release 含「{k}」: {'是' if v else '否'}")

tree = gh("GET", f"/repos/{REPO}/git/trees/{commit['commit']['tree']['sha']}?recursive=1")
remote = {e["path"] for e in tree["tree"] if e["type"] == "blob"}
local = set(local_files().keys())
print("")
print(f"远端文件 {len(remote)}  本地 {len(local)}")
print(f"  只在远端: {sorted(remote - local) or '无'}")
print(f"  只在本地: {sorted(local - remote) or '无'}")
junk = [p for p in remote if "__pycache__" in p or ".pytest_cache" in p or ".ruff_cache" in p]
print(f"  缓存垃圾残留: {junk or '无'}")

for p in ("README.md", "pyproject.toml", "src/finverify/scope.py"):
    c = gh("GET", f"/repos/{REPO}/contents/{p}?ref=main")
    print(f"  OK {p} ({c['size']} B)")

runs = gh("GET", f"/repos/{REPO}/actions/runs?per_page=4")
print("\nCI:")
for r in runs["workflow_runs"]:
    print(f"  {r['head_sha'][:7]}  {r['status']:<10} {r['conclusion']}")
