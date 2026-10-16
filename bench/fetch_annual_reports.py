"""从巨潮资讯网（cninfo）批量获取 A 股年报 PDF。

为什么不用 App/Connector
------------------------
实测环境里没有可用的金融数据连接器（tool_search 无匹配，
mcode-tools connector 目录请求超时）。但巨潮的公开接口
本身就够用，且不需要任何凭证：

  1. POST www.cninfo.com.cn/new/hisAnnouncement/query   查公告列表
  2. GET  static.cninfo.com.cn/{adjunctUrl}             下载 PDF

两者都是强制公开披露渠道，A 股年报本身即法定披露文件。

口径要点
--------
巨潮上同一份年报可能有「初版 / 更正后 / 更新版」多个版本。
做评测集必须**只取最新版本**，否则同一公司会被重复采样，
且可能拿到已被修正的错误数据。本脚本按公告编号去重，
优先保留标题里带「更新版/更正版/修订」的版本。

用法：
    python bench/fetch_annual_reports.py --count 20 --out-dir data/real
    python bench/fetch_annual_reports.py --codes 600519,000001,300750
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import time
import urllib.error
import urllib.parse
import urllib.request

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"
PAGE_URL = "http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# 年报类别
CATEGORY_NDBG = "category_ndbg_szsh"
COLUMNS = ("szse", "sse")

# 标题里出现这些词，说明是修订/更正/补充版本，权威性更高
REVISED_HINTS = ("更新", "更正", "修订", "补充", "重述")
# 这些不是完整年报，评测集里应当排除
EXCLUDE_HINTS = ("摘要", "英文", "取消", "问询", "审计报告", "内部控制",
                 "社会责任", "可持续", "ESG")


def query(page: int, size: int, se_date: str, column: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "pageNum": str(page),
            "pageSize": str(size),
            "column": column,
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": CATEGORY_NDBG,
            "trade": "",
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
    ).encode()
    req = urllib.request.Request(QUERY_URL, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def is_full_report(title: str) -> bool:
    t = title.replace("<em>", "").replace("</em>", "")
    if any(x in t for x in EXCLUDE_HINTS):
        return False
    return "年度报告" in t


def score_title(title: str) -> int:
    """修订版分数更高，用于同一公司多版本去重。"""
    t = title.replace("<em>", "").replace("</em>", "")
    return sum(1 for h in REVISED_HINTS if h in t)


def download(url: str, dest: pathlib.Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"不是 PDF（前 5 字节 {data[:5]!r}）")
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20, help="下载份数")
    ap.add_argument("--out-dir", default="data/real", help="输出目录")
    ap.add_argument("--se-date", default="2024-01-01~2024-12-31", help="公告日期区间")
    ap.add_argument("--codes", default="", help="指定股票代码，逗号分隔（忽略分页随机）")
    ap.add_argument("--manifest", default="", help="元数据输出路径")
    ap.add_argument("--delay", type=float, default=1.5, help="每次下载后的间隔秒（礼貌抓取）")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = pathlib.Path(
        args.manifest or out_dir / "manifest.json"
    )

    # ---- 1. 收集候选公告 ----
    candidates: dict[str, dict] = {}
    if args.codes:
        for page in range(1, 4):
            for col in COLUMNS:
                try:
                    r = query(page, 30, args.se_date, col)
                except Exception as exc:  # noqa: BLE001
                    print(f"查询失败 {col} p{page}: {exc}")
                    continue
                for a in r.get("announcements") or []:
                    code = a["secCode"]
                    if code not in args.codes.split(","):
                        continue
                    title = a["announcementTitle"]
                    if not is_full_report(title):
                        continue
                    prev = candidates.get(code)
                    if prev is None or score_title(title) > score_title(
                        prev["announcementTitle"]
                    ):
                        candidates[code] = a
    else:
        need = args.count * 3  # 多取一些，因为要过滤掉摘要/非完整版
        for page in range(1, 12):
            if len(candidates) >= need:
                break
            for col in COLUMNS:
                try:
                    r = query(page, 30, args.se_date, col)
                except Exception as exc:  # noqa: BLE001
                    print(f"查询失败 {col} p{page}: {exc}")
                    continue
                for a in r.get("announcements") or []:
                    title = a["announcementTitle"]
                    if not is_full_report(title):
                        continue
                    code = a["secCode"]
                    prev = candidates.get(code)
                    if prev is None or score_title(title) > score_title(
                        prev["announcementTitle"]
                    ):
                        candidates[code] = a

    picked = list(candidates.values())
    random.shuffle(picked)  # 避免只拿到同一行业/同一板块
    print(f"候选公告: {len(candidates)} 家，开始下载 {args.count} 份\n")

    # ---- 2. 下载 ----
    manifest = []
    for i, a in enumerate(picked[: args.count], 1):
        code = a["secCode"]
        name = a["secName"]
        title = a["announcementTitle"].replace("<em>", "").replace("</em>", "")
        url = STATIC_BASE + a["adjunctUrl"]
        dest = out_dir / f"{code}_{name}.pdf"
        try:
            size = download(url, dest)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{args.count}] {code} {name} 下载失败: {exc}")
            continue
        manifest.append(
            {
                "sec_code": code,
                "sec_name": name,
                "title": title,
                "url": url,
                "page_url": PAGE_URL.format(code=code),
                "local": str(dest),
                "bytes": size,
                "kb": round(size / 1024, 1),
                "revised": score_title(title) > 0,
            }
        )
        print(f"[{i}/{args.count}] {code} {name}  {size / 1024 / 1024:.2f} MB  {title[:30]}")
        time.sleep(args.delay)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_mb = sum(m["bytes"] for m in manifest) / 1024 / 1024
    print(f"\n完成: {len(manifest)} 份，合计 {total_mb:.1f} MB")
    print(f"元数据: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
