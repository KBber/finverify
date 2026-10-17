"""bench/_paths.py 的行为测试。

为什么产品代码不覆盖它、却要给 bench 写测试
-------------------------------------------
``_paths`` 只服务于诊断脚本，但它坏掉的方式很难察觉：路径解析失败时
脚本不会安静地跑错，会在 ``scratch_file`` 这种地方直接
``FileExistsError`` 崩掉——这个坑真的踩过一次（把文件名传给了
只会 ``mkdir`` 的 ``scratch``）。

更值得防的是**静默回退**：一旦有人在解析链里加了个猜测的兜底路径，
脚本就会读到上一次的旧结果而不报错，那比崩掉难查一百倍。
下面的用例把「该报错就报错、该用环境变量就用」钉死。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import _paths  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    """所有解析函数都带 lru_cache，改环境变量后必须先清掉，
    否则测试之间会互相污染（前一个用例的目录串到后一个）。"""
    _paths.data_dir.cache_clear()
    _paths.manifest.cache_clear()
    _paths.pdf_for.cache_clear()
    _paths.desktop.cache_clear()
    yield
    for name in ("data_dir", "manifest", "pdf_for", "desktop"):
        getattr(_paths, name).cache_clear()


# --------------------------------------------------------------------------
# scratch：目录与文件必须分清
# --------------------------------------------------------------------------
def test_scratch_dir_creates_dir_and_is_idempotent():
    p = _paths.scratch_dir("fv_test_dir")
    assert p.is_dir()
    # 再调一次不能炸——exist_ok=True 是这里的全部意义
    assert _paths.scratch_dir("fv_test_dir") == p


def test_scratch_file_returns_existing_file_without_raising():
    p = _paths.scratch_file("fv_test_file.txt")
    p.write_text("x", encoding="utf-8")
    # 这正是踩过的坑：文件已存在时再调，旧的 scratch() 会 mkdir 失败
    assert _paths.scratch_file("fv_test_file.txt") == p
    assert p.is_file()


def test_commit_msg_resolves_to_a_usable_path(tmp_path, monkeypatch):
    """提交信息路径必须能直接 read_text——默认值落在临时目录而不是仓库里。"""
    monkeypatch.delenv("FINVERIFY_COMMIT_MSG", raising=False)
    p = _paths.commit_msg()
    assert p.parent.is_dir()
    p.write_text("测试提交信息\n", encoding="utf-8")
    assert p.read_text(encoding="utf-8") == "测试提交信息\n"


def test_commit_msg_honors_env(tmp_path, monkeypatch):
    f = tmp_path / "msg.txt"
    f.write_text("env\n", encoding="utf-8")
    monkeypatch.setenv("FINVERIFY_COMMIT_MSG", str(f))
    assert _paths.commit_msg() == f


# --------------------------------------------------------------------------
# 找不到就必须报错，绝不猜
# --------------------------------------------------------------------------
def test_data_dir_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FINVERIFY_DATA_DIR", str(tmp_path))
    assert _paths.data_dir() == tmp_path
    assert _paths.manifest() == tmp_path / "manifest.json"


def test_data_dir_rejects_nonexistent_env(monkeypatch):
    monkeypatch.setenv("FINVERIFY_DATA_DIR", r"X:\definitely\not\here")
    with pytest.raises(SystemExit) as e:
        _paths.data_dir()
    assert "FINVERIFY_DATA_DIR" in str(e.value)


def test_data_dir_reports_candidates_when_nothing_found(monkeypatch):
    """候选位全落空时要说清「怎么配」，而不是留一句 not found。"""
    monkeypatch.delenv("FINVERIFY_DATA_DIR", raising=False)
    monkeypatch.setattr(_paths, "_from_env", _raise_on_missing)
    with pytest.raises(SystemExit) as e:
        _paths.data_dir.cache_clear()
        _paths.data_dir()
    msg = str(e.value)
    assert "FINVERIFY_DATA_DIR" in msg and "候选" in msg


def _raise_on_missing(env, candidates, what, hint=""):
    raise SystemExit(f"找不到{what}。候选：{candidates}\n{hint}")


# --------------------------------------------------------------------------
# 密钥：只认环境变量
# --------------------------------------------------------------------------
def test_secrets_requires_env_var(monkeypatch):
    """公开仓库里不该有任何人的密钥文件路径，所以没有兜底候选位。"""
    monkeypatch.delenv("FINVERIFY_SECRETS", raising=False)
    with pytest.raises(SystemExit) as e:
        _paths.secrets()
    assert "FINVERIFY_SECRETS" in str(e.value)


def test_secrets_reads_from_env(tmp_path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text("ghp_" + "A" * 36 + "\nsk-" + "B" * 35 + "\n", encoding="utf-8")
    monkeypatch.setenv("FINVERIFY_SECRETS", str(f))
    assert _paths.secrets() == f


# --------------------------------------------------------------------------
# 按证券代码取年报
# --------------------------------------------------------------------------
def test_pdf_for_matches_by_prefix(tmp_path, monkeypatch):
    """文件名带中文简称（``688610_埃科光电.pdf``），只能前缀匹配。"""
    (tmp_path / "688610_埃科光电.pdf").write_bytes(b"%PDF-")
    monkeypatch.setenv("FINVERIFY_DATA_DIR", str(tmp_path))
    assert _paths.pdf_for("688610").name == "688610_埃科光电.pdf"


def test_pdf_for_lists_candidates_when_missing(tmp_path, monkeypatch):
    (tmp_path / "603111_珀莱雅.pdf").write_bytes(b"%PDF-")
    monkeypatch.setenv("FINVERIFY_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as e:
        _paths.pdf_for("688610")
    # 报错要带上「手上有什么」，否则得自己去翻目录
    assert "603111_珀莱雅.pdf" in str(e.value)


def test_pdf_for_refuses_to_guess_between_duplicates(tmp_path, monkeypatch):
    (tmp_path / "688610_甲.pdf").write_bytes(b"%PDF-")
    (tmp_path / "688610_乙.pdf").write_bytes(b"%PDF-")
    monkeypatch.setenv("FINVERIFY_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as e:
        _paths.pdf_for("688610")
    assert "多份" in str(e.value)


# --------------------------------------------------------------------------
# 输出位置
# --------------------------------------------------------------------------
def test_note_lands_beside_the_script():
    """结论文件统一落在 bench/ 下，跟着脚本走，不散落到 CWD。"""
    assert _paths.note("x.txt").parent == pathlib.Path(_paths.__file__).resolve().parent


def test_root_is_project_root_not_a_disk_path():
    """ROOT 由脚本位置推导，换机器不需要改代码。"""
    assert _paths.ROOT.name == "finverify"
    assert (_paths.ROOT / "pyproject.toml").is_file()


def test_no_remaining_hardcoded_paths():
    """回归护栏：bench 源码里不该再出现写死的盘符。

    用 AST 而不是逐行匹配——文档字符串里提到 ``D:\\datasets\\real``
    是正常的说明文字，逐行匹配会把它们一并判成违规。

    盘符用正则而不是 ``os.path.splitdrive``：后者把任何「单字符 + 冒号」
    都当盘符，中文标签 ``旧: `` 会被误判（踩过）。
    """
    drive = re.compile(r"^[A-Za-z]:[\\/]")
    offenders = []
    for f in sorted((ROOT / "bench").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docstrings = {
            ast.get_docstring(n, clean=False)
            for n in ast.walk(tree)
            if isinstance(
                n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in docstrings:
                continue
            if drive.match(node.value):
                offenders.append(f"{f.name}:{node.lineno} {node.value!r}")
    assert not offenders, f"仍有写死的盘符路径：{offenders}"
