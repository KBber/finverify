"""校验引擎行为测试。

运行：``python -m pytest tests/ -v`` 或直接 ``python tests/test_validation.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finverify import (  # noqa: E402
    EvidenceQuality,
    EvidenceSpan,
    FinancialMetric,
    MetricKind,
    ProvenanceIndex,
    StatementKind,
    ValidationEngine,
    VerificationStatus,
    VerifiedNumber,
)

PERIOD = "2023"


def span(page: int = 10, quality=EvidenceQuality.TABLE_CELL, snippet="…") -> EvidenceSpan:
    return EvidenceSpan(
        page=page, bbox=(72.0, 640.0, 320.0, 664.0), quality=quality, snippet=snippet
    )


def metric(kind: MetricKind, value: float, label: str = "指标", page: int = 10,
           period: str = PERIOD):
    return FinancialMetric(
        kind=kind,
        label=label,
        number=VerifiedNumber(
            value=value, unit="元", period=period, evidence=span(page)
        ),
        statement=StatementKind.BALANCE_SHEET,
    )


# ----------------------------------------------------------------------
# 1. 勾稽成立
# ----------------------------------------------------------------------
def test_balance_identity_passes():
    metrics = [
        metric(MetricKind.TOTAL_ASSETS, 1000.0),
        metric(MetricKind.TOTAL_LIABILITIES, 600.0),
        metric(MetricKind.TOTAL_EQUITY, 400.0),
    ]
    (result,) = [
        r for r in ValidationEngine().validate(metrics, PERIOD)
        if r.rule_id == "BS_BALANCE"
    ]
    assert result.status is VerificationStatus.PASS, result
    assert abs(result.delta) < 1e-6


# ----------------------------------------------------------------------
# 2. 勾稽不成立 -> FAIL（抓 LLM 幻觉）
# ----------------------------------------------------------------------
def test_balance_identity_detects_hallucination():
    """资产 1000，负债 600，权益 500 —— 多出 100，应判 FAIL。"""
    metrics = [
        metric(MetricKind.TOTAL_ASSETS, 1000.0),
        metric(MetricKind.TOTAL_LIABILITIES, 600.0),
        metric(MetricKind.TOTAL_EQUITY, 500.0),
    ]
    (result,) = [
        r for r in ValidationEngine().validate(metrics, PERIOD)
        if r.rule_id == "BS_BALANCE"
    ]
    assert result.status is VerificationStatus.FAIL, result
    # delta 定义为 lhs - rhs = 1000 - 1100 = -100
    assert abs(result.delta - (-100.0)) < 1e-6


# ----------------------------------------------------------------------
# 3. 缺失项 -> REVIEW_REQUIRED，绝不判 FAIL
# ----------------------------------------------------------------------
def test_missing_term_yields_review_not_fail():
    metrics = [
        metric(MetricKind.TOTAL_ASSETS, 1000.0),
        metric(MetricKind.TOTAL_LIABILITIES, 600.0),
        # 缺 TOTAL_EQUITY
    ]
    (result,) = [
        r for r in ValidationEngine().validate(metrics, PERIOD)
        if r.rule_id == "BS_BALANCE"
    ]
    assert result.status is VerificationStatus.REVIEW_REQUIRED, result
    assert MetricKind.TOTAL_EQUITY in result.missing


# ----------------------------------------------------------------------
# 4. 低质量证据在构造期就被拒绝
# ----------------------------------------------------------------------
def test_unverified_evidence_rejected_at_construction():
    try:
        VerifiedNumber(
            value=1.0,
            unit="元",
            period=PERIOD,
            evidence=span(quality=EvidenceQuality.UNVERIFIED),
        )
    except ValueError:
        return  # 预期行为
    raise AssertionError("UNVERIFIED 级证据本应被拒绝")


# ----------------------------------------------------------------------
# 5. 期间隔离：不同年度不混算
# ----------------------------------------------------------------------
def test_periods_are_isolated():
    m2023 = metric(MetricKind.TOTAL_ASSETS, 1000.0, period="2023")
    m2024 = metric(MetricKind.TOTAL_ASSETS, 2000.0, period="2024")
    engine = ValidationEngine()
    (r23,) = [
        r for r in engine.validate([m2023, m2024], "2023") if r.rule_id == "BS_BALANCE"
    ]
    (r24,) = [
        r for r in engine.validate([m2023, m2024], "2024") if r.rule_id == "BS_BALANCE"
    ]
    # 各自只有资产项，缺构成项 -> 都应是 REVIEW_REQUIRED，且取值不同
    assert r23.status is VerificationStatus.REVIEW_REQUIRED
    assert r24.status is VerificationStatus.REVIEW_REQUIRED
    assert r23.lhs_value == 1000.0 and r24.lhs_value == 2000.0


# ----------------------------------------------------------------------
# 6. 容差内的小额差异应 PASS（四舍五入）
# ----------------------------------------------------------------------
def test_tolerance_absorbs_rounding():
    metrics = [
        metric(MetricKind.TOTAL_ASSETS, 1000.0),
        metric(MetricKind.TOTAL_LIABILITIES, 600.004),
        metric(MetricKind.TOTAL_EQUITY, 400.002),
    ]
    (result,) = [
        r for r in ValidationEngine().validate(metrics, PERIOD)
        if r.rule_id == "BS_BALANCE"
    ]
    assert result.status is VerificationStatus.PASS, result


# ----------------------------------------------------------------------
# 7. 溯源索引
# ----------------------------------------------------------------------
def test_provenance_index():
    metrics = [
        metric(MetricKind.REVENUE, 3_600_000_000.0, label="营业收入", page=142),
        metric(MetricKind.NET_PROFIT, 441_000_000.0, label="净利润", page=145),
    ]
    idx = ProvenanceIndex(metrics)

    assert idx.periods() == [PERIOD]
    ev = idx.locate(MetricKind.REVENUE.value, PERIOD)
    assert ev is not None and ev.page == 142

    md = idx.to_markdown(MetricKind.REVENUE.value, PERIOD)
    assert "第 142 页" in md and "营业收入" in md

    cov = idx.coverage(PERIOD)
    assert cov["total"] == 2.0 and cov["direct"] == 1.0

    js = idx.export_json()
    assert '"page": 142' in js or '"page":142' in js


# ----------------------------------------------------------------------
# 8. 全局汇总：FAIL 优先
# ----------------------------------------------------------------------
def test_worst_status_priority():
    engine = ValidationEngine()
    ok = [
        metric(MetricKind.TOTAL_ASSETS, 1000.0),
        metric(MetricKind.TOTAL_LIABILITIES, 600.0),
        metric(MetricKind.TOTAL_EQUITY, 400.0),
    ]
    # 注意：默认规则集含 3 条恒等式，只有提供了完整数据的那条能 PASS。
    # 只给资产负债表三项时，利润表/现金流量表规则必然是 REVIEW_REQUIRED。
    results = engine.validate(ok, PERIOD)
    bs = [r for r in results if r.rule_id == "BS_BALANCE"][0]
    assert bs.status is VerificationStatus.PASS, bs

    assert engine.worst_status(results) is VerificationStatus.REVIEW_REQUIRED
    assert (
        engine.worst_status([bs]) is VerificationStatus.PASS
    ), "只取已通过的那条规则时，应汇总为 PASS"

    broken = engine.validate(
        [
            metric(MetricKind.TOTAL_ASSETS, 1000.0),
            metric(MetricKind.TOTAL_LIABILITIES, 600.0),
            metric(MetricKind.TOTAL_EQUITY, 900.0),
        ],
        PERIOD,
    )
    assert engine.worst_status(broken) is VerificationStatus.FAIL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
