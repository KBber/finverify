"""``python -m finverify`` 的入口。

单独放一个 ``__main__.py`` 是必要的：``python -m <package>`` 只认它，
不会去执行 ``cli.py``。少了这个文件，命令行会以
"'finverify' is a package and cannot be directly executed" 退出，
而且退出码是 1 —— 会被误读成"校验结论是 FAIL"。
"""

from finverify.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
