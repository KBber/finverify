"""修掉被 BOM 污染的提交信息。

问题：PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，
提交信息开头多了一个 U+FEFF，在 GBK 控制台上直接抛 UnicodeEncodeError。

修法：树和父提交都不变，只换提交信息，生成一个替代提交并强制更新引用。
历史里其它提交不受影响（只有这一条的信息有问题）。
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from incremental_push import gh  # noqa: E402

REPO = "KBber/finverify"
MSG_FILE = pathlib.Path(r"G:\minimax_workspace\github-kit\_commit_msg.txt")

# 控制台是 GBK，中文可能编不出去；统一改成 UTF-8 且不抛异常
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def clean(path: pathlib.Path) -> str:
    raw = path.read_bytes().decode("utf-8")
    # 去掉 BOM 与行尾空白
    return raw.lstrip("\ufeff").strip() + "\n"


def main() -> int:
    msg = clean(MSG_FILE)
    assert not msg.startswith("\ufeff"), "BOM 仍在"
    # 把干净版本写回去，避免下次再踩
    MSG_FILE.write_bytes(msg.encode("utf-8"))
    print(f"提交信息首行: {msg.splitlines()[0]}")

    me = gh("GET", "/user")
    email = me.get("email") or f"{me['id']}+{me['login']}@users.noreply.github.com"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    ref = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
    old_sha = ref["object"]["sha"]
    c = gh("GET", f"/repos/{REPO}/commits/{old_sha}")
    old_msg = c["commit"]["message"]
    print(f"原提交 {old_sha[:7]} 信息首字符码位: {ord(old_msg[0]):#06x}")

    if not old_msg.startswith("\ufeff"):
        print("远端信息已是干净的，无需处理")
        return 0

    parents = [p["sha"] for p in c["parents"]]
    fixed = gh(
        "POST",
        f"/repos/{REPO}/git/commits",
        {
            "message": msg,
            "tree": c["commit"]["tree"]["sha"],
            "parents": parents,
            "author": {"name": "Xue Dingtian", "email": email, "date": now},
            "committer": {"name": "Xue Dingtian", "email": email, "date": now},
        },
    )
    gh(
        "PATCH",
        f"/repos/{REPO}/git/refs/heads/main",
        {"sha": fixed["sha"], "force": True},
    )
    print(f"新提交 {fixed['sha'][:7]}  main 已强制更新（树与父提交不变）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
