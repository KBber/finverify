"""bench 脚本的路径解析：环境变量 -> 相对候选位 -> 明确报错。

为什么需要这个模块
------------------
评测集（19 份真实年报，70 MB）、提交信息文件、密钥文件**都不是项目的一部分**：
评测集体积大、可重新下载、不该进 git 仓库；后两者是一次性输入。
脚本里一旦写死绝对路径，项目换个机器（比如从 G 盘搬到 F 盘）就会
静默指向别的盘符——要么找不到数据，要么读到上一次的旧数据，
后者比直接报错更难查。

所以约定是：**代码里只出现相对路径和候选位**，不出现某台机器的盘符。

最省事的用法
------------
把评测集放在项目**同级**目录：::

    <任意位置>/
      data/real/*.pdf + manifest.json
      finverify/          <- 项目
        bench/_paths.py

或者环境变量指过去：``set FINVERIFY_DATA_DIR=D:\\datasets\\real``

所有解析都是**惰性**的：只有真正用到某个资源时才去找它。
这样只要 ``note()`` 的脚本不会因为缺评测集而 import 就失败。
"""

from __future__ import annotations

import functools
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
"""项目根目录（`finverify/`），由脚本自身位置推导，与盘符无关。"""


def _from_env(env: str, candidates: list[pathlib.Path], what: str, hint: str = "") -> pathlib.Path:
    """环境变量优先，其次候选链，全落空就报错——绝不猜、绝不静默回退。"""
    raw = os.environ.get(env)
    if raw:
        p = pathlib.Path(raw).expanduser()
        if not p.exists():
            raise SystemExit(f"{env} 指向的路径不存在：{p}")
        return p

    for c in candidates:
        if c.exists():
            return c

    tried = "".join(f"\n  {c}" for c in candidates)
    raise SystemExit(f"找不到{what}。已尝试：{tried or '（无候选位）'}\n{hint}")


# --------------------------------------------------------------------------
# 评测集：19 份真实年报 + manifest.json
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def data_dir() -> pathlib.Path:
    """评测集目录。刻意放在项目外：70 MB 且可重新下载，不该进 git 仓库。"""
    return _from_env(
        "FINVERIFY_DATA_DIR",
        [
            ROOT.parent / "data" / "real",  # 首选：项目同级，迁移后自然成立
            ROOT / "data" / "real",  # 次选：项目内（需自己加进 .gitignore）
        ],
        "评测集目录（19 份真实年报）",
        hint=(
            "二选一：设环境变量 FINVERIFY_DATA_DIR=<你的路径>，"
            "或把 real/ 放到上面某个候选位。"
        ),
    )


@functools.lru_cache(maxsize=1)
def manifest() -> pathlib.Path:
    return data_dir() / "manifest.json"


@functools.lru_cache(maxsize=1)
def pdf_for(sec_code: str) -> pathlib.Path:
    """按证券代码取年报。文件名带中文简称，所以只能前缀匹配——
    拼完整文件名的话，改一次简称就得改一次代码。"""
    hits = sorted(data_dir().glob(f"{sec_code}_*.pdf"))
    if not hits:
        raise SystemExit(
            f"评测集里没有 {sec_code} 的年报（目录：{data_dir()}）。"
            f"已找到：{sorted(p.name for p in data_dir().glob('*.pdf'))}"
        )
    if len(hits) > 1:
        raise SystemExit(f"{sec_code} 命中多份年报，无法判断用哪份：{hits}")
    return hits[0]


# --------------------------------------------------------------------------
# 一次性输入：提交信息、密钥
# --------------------------------------------------------------------------
def commit_msg() -> pathlib.Path:
    """提交信息文件。不走命令行参数——参数会留在 shell 历史里。

    写入务必 UTF-8 **无 BOM**：PowerShell 5.1 的 ``Set-Content -Encoding UTF8``
    会带 BOM，而 BOM 会原样进 GitHub 的提交信息（踩过一次）。
    """
    raw = os.environ.get("FINVERIFY_COMMIT_MSG")
    if raw:
        return pathlib.Path(raw).expanduser()
    return scratch_file("finverify_commit_msg.txt")


def secrets() -> pathlib.Path:
    """密钥文件（含 GitHub PAT 与各家 API key）。

    刻意**只认环境变量、不给候选位**：公开仓库里不该出现任何人的
    密钥文件路径，换机器时明文路径只会失效，而静默读到别的旧密钥才危险。
    本模块只读文件内容，不打印、不写盘、不入库。
    """
    return _from_env(
        "FINVERIFY_SECRETS",
        [],
        "密钥文件（GitHub PAT + 各家 API key）",
        hint=(
            "  PowerShell: $env:FINVERIFY_SECRETS='D:\\path\\to\\key.txt'\n"
            "  cmd:        set FINVERIFY_SECRETS=D:\\path\\to\\key.txt\n"
            "文件里一行一个：GitHub PAT（ghp_ 开头）与各家 API key（sk- 开头）。"
        ),
    )


# --------------------------------------------------------------------------
# 输出位置
# --------------------------------------------------------------------------
def note(name: str) -> pathlib.Path:
    """bench 的结论文件统一落在 bench/ 下，跟着脚本走，不散落各处。"""
    return pathlib.Path(__file__).resolve().with_name(name)


def scratch_dir(sub: str) -> pathlib.Path:
    """临时**目录**，用完即弃，不污染仓库。"""
    p = pathlib.Path(tempfile.gettempdir()) / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def scratch_file(name: str) -> pathlib.Path:
    """临时**文件**，用完即弃。刻意与 scratch_dir 分开：
    目录用 mkdir(exist_ok=True) 兜底，而文件路径上这么调会直接
    FileExistsError 崩掉——这个坑踩过一次。"""
    return pathlib.Path(tempfile.gettempdir()) / name


@functools.lru_cache(maxsize=1)
def desktop() -> pathlib.Path:
    """桌面。交付件放这里是因为用户第一眼会去找它。

    不写死盘符：OneDrive 重定向后桌面可能在 OneDrive 下，
    写死 ``C:\\Users\\...\\Desktop`` 会把文件丢到用户看不见的地方。
    """
    p = pathlib.Path.home() / "Desktop"
    if not p.is_dir():
        raise SystemExit(f"找不到桌面目录：{p}")
    return p
