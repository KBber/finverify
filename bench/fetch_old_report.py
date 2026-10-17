"""下载 v0.4.0 的 demo_report.html 做对照，判断点击失效是不是本轮引入的回归。"""

from __future__ import annotations

import urllib.request

import _paths

OUT = _paths.scratch_dir("fv040") / "demo_report.html"
URL = "https://raw.githubusercontent.com/KBber/finverify/v0.4.0/samples/demo_report.html"

OUT.parent.mkdir(parents=True, exist_ok=True)
req = urllib.request.Request(URL, headers={"User-Agent": "finverify-diag"})
with urllib.request.urlopen(req, timeout=60) as resp:
    data = resp.read()
OUT.write_bytes(data)

txt = data.decode("utf-8", "replace")
print(f"下载 {len(data)} 字节 -> {OUT}")
print(f"含 <script>: {txt.count('<script>')}")
print(f"含 data-bbox: {txt.count('data-bbox=')}")

# 抽出第一个 item 的 class / bbox，跟本地版对照
i = txt.find('class="item')
print("\nv0.4.0 首个 item:")
print("  " + txt[i - 10 : i + 220])
