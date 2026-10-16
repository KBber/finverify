"""命令行入口。

    python -m finverify demo        # 生成样本并跑完整演示
    python -m finverify audit x.pdf # 审计真实财报
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from finverify import MetricKind
from finverify.pipeline import audit
from finverify.samples import AnnualReportData, build_sample_pdf

_SAMPLES_DIR = Path("samples")


def _configure_stdio() -> None:
    """强制 UTF-8 输出。

    报告里含 ✅/❌ 与 Δ 等符号，Windows 控制台默认 GBK 会直接抛
    UnicodeEncodeError——不能让展示层的编码问题掩盖校验结论。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def _cmd_demo(args: argparse.Namespace) -> int:
    _SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    cases: list[tuple[str, AnnualReportData, str, str]] = [
        (
            "正常样本 —— 三表勾稽全部成立",
            AnnualReportData(),
            "demo_annual_report.pdf",
            "demo_clean.html",
        ),
        (
            "负样本 A —— 所有者权益被多记 5,000 万，破坏资产恒等式",
            AnnualReportData(
                injected_error=(MetricKind.TOTAL_EQUITY, 4_965_000_000.00)
            ),
            "demo_injected_error.pdf",
            "demo_report.html",
        ),
        (
            "负样本 B —— 所得税被少记 1,200 万，破坏净利润恒等式",
            AnnualReportData(
                injected_error=(MetricKind.INCOME_TAX, 79_800_000.00)
            ),
            "demo_injected_error_tax.pdf",
            "demo_report_tax.html",
        ),
    ]

    print()
    for title, data, filename, htmlname in cases:
        path = build_sample_pdf(_SAMPLES_DIR / filename, data)
        report = audit(path, period=data.period)
        print()
        print("#" * 72)
        print(f"# {title}")
        print("#" * 72)
        print(report.to_text())
        print()

        # 同时产出可交互 HTML（PDF 内嵌，双击即开），供 demo 与求职展示
        try:
            from finverify.web import build_static_report

            html_path = build_static_report(report, _SAMPLES_DIR / htmlname)
            print(f"可交互报告已生成: {html_path}")
        except ImportError:
            print("（未安装 PyMuPDF，跳过 HTML 报告生成）")
        print()

    print("说明：负样本中的错误数字在视觉上与正常数字完全一致，")
    print("      没有任何可辨识的异常 —— 传统 RAG 永远不会发现它们。")
    print("      finverify 是靠会计恒等式在确定性层面抓到的。")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    path = Path(args.pdf)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    report = audit(path, period=args.period, strategy=args.strategy)

    if args.format == "html":
        # HTML 报告由 finverify.web 生成（需 PyMuPDF），单独延迟导入，
        # 以便只跑 text/markdown/json 时不强制依赖渲染栈。
        from finverify.web import build_static_report

        out = Path(args.out) if args.out else path.with_suffix(".html")
        written = build_static_report(report, out)
        print(f"已写入可交互报告: {written}")
        print("双击该文件即可在浏览器中打开，点击左侧任一数字可在右侧定位原文。")
        return {"PASS": 0, "FAIL": 1, "REVIEW_REQUIRED": 3}[report.status.value]

    renderers = {
        "text": report.to_text,
        "markdown": report.to_markdown,
        "json": report.to_json,
    }
    rendered = renderers[args.format]()
    print(rendered)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        suffix = {"text": ".txt", "markdown": ".md", "json": ".json"}[args.format]
        out = out.with_suffix(out.suffix or suffix)
        out.write_text(rendered, encoding="utf-8")
        print(f"\n已写入: {out}")

    # 退出码即结论，便于接 CI
    return {"PASS": 0, "FAIL": 1, "REVIEW_REQUIRED": 3}[report.status.value]


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    p = argparse.ArgumentParser(
        prog="finverify",
        description="财报数字溯源与会计勾稽校验",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="生成合成样本并演示幻觉检出")
    d.set_defaults(func=_cmd_demo)

    a = sub.add_parser("audit", help="审计一份财报 PDF")
    a.add_argument("pdf", help="PDF 路径")
    a.add_argument("--period", default="2023", help="报告期，如 2023 / 2023H1")
    a.add_argument(
        "--format", default="text", choices=["text", "markdown", "json", "html"]
    )
    a.add_argument("--strategy", default="text", choices=["text", "lines"])
    a.add_argument("--out", default=None, help="输出文件路径")
    a.set_defaults(func=_cmd_audit)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
