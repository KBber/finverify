"""v0.5.0 收尾：更新 Release 正文 + 把 tag 移到当前 main。

顺带说明连接问题：这条链路到 api.github.com 的 SSL 会被随机掐断
（UNEXPECTED_EOF_WHILE_READING，约一半调用失败），
所以这里复用 incremental_push 里那条带重试的长连接通道，
而不是原来的 PowerShell Invoke-RestMethod（不重试）。
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from incremental_push import gh  # noqa: E402

REPO = "KBber/finverify"
VER = "v0.5.0"

BODY = """## v0.5.0

财报数字溯源与会计勾稽校验。

### 一句话

> LLM 在财报上最贵的失败不是"答不上来"，而是给出一个看起来合理但错误的数字。
> **无法溯源的数字不应该被信任；能通过恒等式检验的数字才值得相信。**

### 快速开始

```bash
pip install -e "."
python -m finverify demo
python -m finverify audit 年报.pdf --format html --out 报告.html
```

### 本版核心：在 19 份真实年报上做评测

合成样本永远测不出真实世界的写法。这一版在 **19 份巨潮公开年报**
（70.3 MB，覆盖沪深主板 / 创业板 / 科创板 / 北交所，141-249 页）上
做批量审计，抓出七个缺陷 —— 每一个都会**静默产生错误数字**，而不是报错。

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | 三表标题带**中文序号前缀** | `（一） 合并资产负债表` 的 8 个真标题一条都匹配不上；口径标记反而落到**审计报告的报表目录**上，母公司报表被当成合并口径 |
| 2 | 三表标题**无前缀**（裸的） | 零口径标记 = **根本不做口径过滤**，附注里的数被当报表科目抽出（所得税费用取到递延所得税调节表的 -2,203,631.30） |
| 3 | **附注编号列**被当成金额 | `七、61`（附注七第 61 项）里的 `61` 被读成营业收入；真值 235,575,368.09 被忽略 |
| 4 | **一页两个口径被并成一张表** | 603117 p88 的表格 bbox 横跨整页，上半合并、下半母公司；母公司所得税 -2,878,668.74 被当成合并口径，恒等式报出差 10,002,568.40 的假 FAIL |
| 5 | 破折号 `-` 表示「零/无」 | `四、净利润  -  -` 被当成「只有标签」，去吞下一行，净利润读成 0.00 |
| 6 | 行重建时**全空行不切断分组** | 表头被吸进第一行数据。Windows 字体下碰巧还能匹配，**Linux / Noto CJK 上直接抽不到现金流量三个净额** |
| 7 | 净利润的两个分项被当成净利润 | `终止经营净利润` 被读成 `net_profit`，002825 因此把净利润读成 0.00 |

第 3 条最隐蔽，因为它产生的是**看起来合理的错值**：

```
[0] 一、营业收入   [1] 七、61   [3] 235,575,368.09   [4] 262,666,026.24
[0] 减:所得税费用 [1] 七、76   [3] -910,591.05      [4] 8,817,750.06
```

修法是分层的：**通用解析函数保持宽松**（仍需支持 `营业收入 3,600.00 万元`），
**单元格层面另加一道「整格必须是数字」的闸门**。

第 2 条的判断不靠推理，靠实测数值比对：
摘要区披露总资产 159,124.72 万元 = 1,591,247,200.00 元，
裸标题区资产总计 1,591,247,176.07 元，差 23.93 元 —— 正是万元四舍五入的精度差。

第 6 条是本版最有教育意义的一条：**本地 68 项测试全绿，只有 CI 红**。
跨平台 CI 不是形式主义，它抓到的是本地字体布局永远复现不出的路径。

### 无框线表的行重建

真实年报大量使用无框线表，一个逻辑行在视觉上占 2-3 个文本行：

```
r22  非空1/3  ['四、利润总额(亏损总额以"-"号', '', '']          y高 9pt
r23  非空2/3  ['', '215,459,982.04', '417,326,200.27']      y高 6pt  ← 数值在这
r24  非空1/3  ['填列)', '', '']                              y高 6pt
```

**数值根本不在标签那一行里**，所以标签正则写得再全也没用。

曾经加过一道「按行高判断碎片」的闸门，实测 18 份年报指标数 140 → 140，
一行都没多抽。查下去发现**闸门方向是反的**：标签起始行高度正常（9pt，与常规行同高），
只有数值行和续行是 6pt 碎片，闸门把起始行判成"非碎片"原样放行，合并彻底失效。
闸门已默认关闭并保留为实验开关 —— **证伪一个方案本身也是结果**。

### 实测收益（19 份真实年报）

| 指标 | 不重建 | 重建 |
|---|---:|---:|
| 抽到的指标数 | 148 | **160** (+12) |
| 恒等式 FAIL | 15 | **15** (±0) |
| REVIEW_REQUIRED | 39 | **32** (-7) |

12 份更好、7 份无变化、**0 份退化**。

判据不是指标数，而是 **FAIL 数与最终结论** —— 指标变多可能同时意味着抽错。

### 性能

`ingestion.all_text_lines` 一次打开取全文，替代逐页重开
（200 页年报原本是 200 次文件打开），单份审计 **35s → 15.5s**。
`scope.detect_scopes_cached` 按 `(路径, mtime, 页数上限)` 缓存。

### 质量

- 68 项测试全部通过（本轮新增 27 项）
- ruff 干净
- CI 覆盖 Python 3.10 / 3.11 / 3.12，断言 CLI 退出码契约
"""


def main() -> int:
    me = gh("GET", "/user")
    email = me.get("email") or f"{me['id']}+{me['login']}@users.noreply.github.com"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    ref = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
    sha = ref["object"]["sha"]
    print(f"main = {sha[:7]}")

    old = gh("GET", f"/repos/{REPO}/git/ref/tags/{VER}")
    old_target = old["object"]["sha"]
    print(f"旧 tag {VER} -> {old_target[:7]}")

    tag = gh(
        "POST",
        f"/repos/{REPO}/git/tags",
        {
            "tag": VER,
            "message": (
                f"{VER} —— 真实年报评测：七个口径与取列缺陷，行级口径判定，"
                "无框线表行重建"
            ),
            "object": sha,
            "type": "commit",
            "tagger": {"name": "Xue Dingtian", "email": email, "date": now},
        },
    )
    gh(
        "PATCH",
        f"/repos/{REPO}/git/refs/tags/{VER}",
        {"sha": tag["sha"], "force": True},
    )
    print(f"tag {VER} -> {sha[:7]}")

    rel = gh("GET", f"/repos/{REPO}/releases/tags/{VER}")
    gh(
        "PATCH",
        f"/repos/{REPO}/releases/{rel['id']}",
        {"body": BODY, "target_commitish": "main"},
    )
    print(f"release 已更新: {rel['html_url']}")

    runs = gh("GET", f"/repos/{REPO}/actions/runs?per_page=3")
    print("\nCI:")
    for r in runs["workflow_runs"]:
        print(f"  {r['head_sha'][:7]}  {r['status']}  {r['conclusion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
