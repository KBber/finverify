"""增量推送：只上传真正变化的文件，带重试。

为什么不用 push_commit.ps1
-------------------------
它把 95 个文件逐个重传一遍，而实测环境到 api.github.com 的连接并不稳定
（连续两次在 blob 上传中途被掐断：'基础连接已经关闭'）。
PowerShell 5.1 的 Invoke-RestMethod 不做重试，一次断连整个推送就废。

这个脚本先取远端整棵树（一次 API 调用），本地算 git blob SHA-1 比对，
只上传差异 —— 通常只有 1-2 个文件，连接断的概率和重试成本都低得多。

用法：
    python bench/incremental_push.py
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import pathlib
import sys
import time
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = "KBber/finverify"
MSG_FILE = pathlib.Path(r"G:\minimax_workspace\github-kit\_commit_msg.txt")
KEYS = pathlib.Path(r"C:\Users\Admin\Desktop\PHD\key.txt")
API = "https://api.github.com"

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def token() -> str:
    import re

    raw = KEYS.read_text(encoding="utf-8")
    m = re.search(r"ghp_[A-Za-z0-9]{30,}", raw)
    if not m:
        raise SystemExit("GitHub PAT 提取失败")
    return m.group(0)


TOK = token()


def gh(method: str, path: str, body: dict | None = None, tries: int = 10):
    """带指数退避的 GitHub API 调用。凭证只进 header，不进日志。

    用 http.client 而不是 urllib，是为了**复用长连接**。
    实测这条链路到 api.github.com 的 SSL 会随机被掐断
    （UNEXPECTED_EOF_WHILE_READING，约一半调用失败，
    urllib 每次调用都重新握手所以踩得更狠）；
    保持连接能大幅减少握手次数，断线时重建即可。
    """
    payload = (
        json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None
    )
    headers = {
        "Authorization": f"Bearer {TOK}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "finverify-push",
        "Content-Type": "application/json; charset=utf-8",
    }
    last: Exception | None = None
    for i in range(tries):
        conn: http.client.HTTPSConnection | None = None
        try:
            conn = http.client.HTTPSConnection("api.github.com", timeout=60)
            conn.request(method, path, payload, headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status >= 500:
                raise OSError(f"HTTP {resp.status}: {raw[:200]!r}")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body_txt = exc.read()[:300].decode("utf-8", "replace")
            # 4xx 是请求本身的问题（限流/权限/不存在），重试没有意义
            if exc.code not in (403, 429):
                raise SystemExit(f"API 拒绝 {method} {path}: {exc.code} {body_txt}")
            last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001,S110
                    pass
        if i < tries - 1:
            wait = min(1.5 ** i, 8) + (i * 0.3)
            print(
                f"    连接中断({type(last).__name__})，{wait:.1f}s 后重试"
                f" [{i + 1}/{tries}]",
                flush=True,
            )
            time.sleep(wait)
    raise SystemExit(f"API 调用失败 {method} {path}: {last}")


def blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def local_files() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts):
            continue
        # 遵守 .gitignore 里的生成物
        name = p.name
        if name.startswith("bench_") and name.endswith(".pdf"):
            continue
        out[rel.as_posix()] = p.read_bytes()
    return out


def main() -> int:
    me = gh("GET", "/user")
    email = me.get("email") or f"{me['id']}+{me['login']}@users.noreply.github.com"
    print(f"身份: {me['login']}  邮箱: {email}")

    ref = gh("GET", f"/repos/{REPO}/git/ref/heads/main")
    parent = ref["object"]["sha"]
    parent_commit = gh("GET", f"/repos/{REPO}/git/commits/{parent}")
    base_tree = parent_commit["tree"]["sha"]
    print(f"父提交: {parent[:7]}  tree: {base_tree[:7]}")

    remote = gh("GET", f"/repos/{REPO}/git/trees/{base_tree}?recursive=1")
    have = {
        e["path"]: e["sha"]
        for e in remote["tree"]
        if e["type"] == "blob"
    }
    print(f"远端文件 {len(have)} 个")

    files = local_files()
    entries: list[dict] = []
    changed = 0
    for rel, data in files.items():
        sha = blob_sha(data)
        if have.get(rel) == sha:
            continue
        blob = gh(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            },
        )
        entries.append(
            {"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
        changed += 1
        print(f"  上传 {rel} ({len(data)} B)")

    removed = [p for p in have if p not in files]
    for p in removed:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})
    print(f"变更 {changed} 个，删除 {len(removed)} 个")

    if not entries:
        print("远端已是最新，无需提交")
        return 0

    tree = gh("POST", f"/repos/{REPO}/git/trees", {"tree": entries, "base_tree": base_tree})
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    commit = gh(
        "POST",
        f"/repos/{REPO}/git/commits",
        {
            "message": MSG_FILE.read_text(encoding="utf-8"),
            "tree": tree["sha"],
            "parents": [parent],
            "author": {"name": "Xue Dingtian", "email": email, "date": now},
            "committer": {"name": "Xue Dingtian", "email": email, "date": now},
        },
    )
    gh("PATCH", f"/repos/{REPO}/git/refs/heads/main", {"sha": commit["sha"], "force": False})
    print(f"新提交: {commit['sha'][:7]}  main 已快进")
    return 0


if __name__ == "__main__":
    sys.exit(main())
