"""诊断：trees?recursive=1 到底是连不上还是被限流。"""

from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.error
import urllib.request

REPO = "KBber/finverify"
API = "https://api.github.com"
KEYS = pathlib.Path(r"C:\Users\Admin\Desktop\PHD\key.txt")

TOK = re.search(r"ghp_[A-Za-z0-9]{30,}", KEYS.read_text(encoding="utf-8")).group(0)


def probe(label: str, path: str) -> None:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {TOK}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "finverify-diag",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            dt = time.perf_counter() - t0
            print(f"  {label:<34} OK {resp.status} {len(raw):>8} B {dt:.1f}s")
            if "rate" in resp.headers:
                print(f"      rate limit remaining={resp.headers['x-ratelimit-remaining']}"
                      f" reset={resp.headers['x-ratelimit-reset']}")
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        print(f"  {label:<34} HTTP {exc.code}  {body}")
    except Exception as exc:
        print(f"  {label:<34} {type(exc).__name__}: {exc}")


print("=== 连续探测 ===")
for i in range(3):
    print(f"-- 第 {i + 1} 轮 --")
    probe("GET /user", "/user")
    probe("GET /rate_limit", "/rate_limit")
    probe("GET trees (非递归)", "/repos/KBber/finverify/git/trees/3002080")
    probe("GET trees (递归)", "/repos/KBber/finverify/git/trees/3002080?recursive=1")
    time.sleep(2)
