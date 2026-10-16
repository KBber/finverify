"""重建 manifest.json 元数据，不重新下载 PDF。

背景
----
``fetch_annual_reports.py`` 的 manifest 是在**下载循环里**累积的，
一旦那轮没抓到任何文件就会写成 ``[]`` —— 实测踩过一次，元数据全丢，
而 19 份 PDF（68 MB）还在硬盘上。重下 68 MB 显然不划算，
但元数据（URL / 标题 / 是否修订版）是可复现的：它只来自巨潮的公告
列表接口，跟 PDF 本身无关。

所以这里只做一件事：重新查公告列表，按股票代码把元数据补回去。
PDF 用现成的，字节数取本地实际大小而不是重新下载后的长度。

用法：
    python bench/rebuild_manifest.py --data-dir data/real
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_annual_reports import (  # noqa: E402
    COLUMNS,
    PAGE_URL,
    STATIC_BASE,
    is_full_report,
    query,
    score_title,
)

# 北交所不在默认的 szse/sse 两列里。873576 天力复合就是北交所，
# 之前那轮漏掉它很可能就漏在这里。
ALL_COLUMNS = (*COLUMNS, "third")


def collect(se_date: str, max_page: int = 12) -> dict[str, dict]:
    """按股票代码收集年报候选，同一公司保留修订版。"""
    candidates: dict[str, dict] = {}
    for page in range(1, max_page + 1):
        done = True
        for col in ALL_COLUMNS:
            try:
                r = query(page, 30, se_date, col)
            except Exception as exc:  # noqa: BLE001
                print(f"  查询失败 {col} p{page}: {exc}")
                done = False
                continue
            anns = r.get("announcements") or []
            if anns:
                done = False
            for a in anns:
                title = a["announcementTitle"]
                if not is_full_report(title):
                    continue
                code = a["secCode"]
                prev = candidates.get(code)
                if prev is None or score_title(title) > score_title(
                    prev["announcementTitle"]
                ):
                    candidates[code] = a
        if done and page > 1:
            break  # 这一页三个列都没数据，且不是第一页 -> 到底了
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/real")
    ap.add_argument("--se-date", default="2024-01-01~2024-12-31")
    args = ap.parse_args()

    data_dir = pathlib.Path(args.data_dir)
    pdfs = sorted(data_dir.glob("*.pdf"))
    if not pdfs:
        print(f"{data_dir} 下没有 PDF")
        return 1

    want = {p.name.split("_", 1)[0]: p for p in pdfs}
    print(f"本地 PDF {len(want)} 份，开始查公告元数据…")

    cands = collect(args.se_date)
    print(f"候选公告 {len(cands)} 条")

    manifest, missing = [], []
    for code, path in want.items():
        a = cands.get(code)
        if a is None:
            missing.append(f"{code} {path.name}")
            continue
        title = a["announcementTitle"].replace("<em>", "").replace("</em>", "")
        manifest.append(
            {
                "sec_code": code,
                "sec_name": a["secName"],
                "title": title,
                "url": STATIC_BASE + a["adjunctUrl"],
                "page_url": PAGE_URL.format(code=code),
                "file": path.name,
                "bytes": path.stat().st_size,
                "revised": score_title(title) > 0,
            }
        )

    out = data_dir / "manifest.json"
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_mb = sum(m["bytes"] for m in manifest) / 1024 / 1024
    print(f"\n重建 {len(manifest)}/{len(want)} 条，合计 {total_mb:.1f} MB")
    print(f"元数据: {out}")
    if missing:
        print(f"\n未匹配 {len(missing)} 份（接口里查不到对应公告）:")
        for m in missing:
            print(f"  {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
