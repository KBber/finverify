"""验证 is_full_report 修复：看 873576 / 688610 现在的候选长什么样。

要回答两个问题：
  1. 修完之后，873576 天力复合还会不会被选成「更正公告」？
  2. 688610 埃科光电的 249 页年报，在新规则下拿到的是哪一版？
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_annual_reports import (  # noqa: E402
    COLUMNS,
    is_full_report,
    query,
    score_title,
)

TARGETS = {"873576", "688610"}
L: list[str] = []

for col in COLUMNS:
    for page in range(1, 4):
        try:
            r = query(page, 30, "2024-01-01~2024-12-31", col)
        except Exception as exc:  # noqa: BLE001
            L.append(f"查询失败 {col} p{page}: {exc}")
            continue
        for a in r.get("announcements") or []:
            code = a["secCode"]
            if code not in TARGETS:
                continue
            title = a["announcementTitle"].replace("<em>", "").replace("</em>", "")
            ok = is_full_report(title)
            L.append(
                f"[{col} p{page}] {code} {a['secName']} "
                f"{'保留' if ok else '排除'} score={score_title(title)}"
            )
            L.append(f"        {title[:60]}")

L.append("")
L.append("=== 修复后各代码得分最高的候选 ===")
best: dict[str, tuple[int, str]] = {}
for col in COLUMNS:
    for page in range(1, 4):
        try:
            r = query(page, 30, "2024-01-01~2024-12-31", col)
        except Exception:  # noqa: BLE001
            continue
        for a in r.get("announcements") or []:
            code = a["secCode"]
            if code not in TARGETS:
                continue
            title = a["announcementTitle"].replace("<em>", "").replace("</em>", "")
            if not is_full_report(title):
                continue
            sc = score_title(title)
            if code not in best or sc > best[code][0]:
                best[code] = (sc, title)
for code, (sc, title) in sorted(best.items()):
    L.append(f"  {code} score={sc} {title[:60]}")
if not best:
    L.append("  (无)")

out = HERE / "notice_filter_check.txt"
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
