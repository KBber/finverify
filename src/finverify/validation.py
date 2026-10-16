"""会计恒等式校验引擎。

为什么需要它
------------
LLM 在财报场景最常见的失败不是"答不上来"，而是**给出一个看起来合理但错误的数字**。
纯 RAG 无法发现这类错误 —— 因为检索结果本身就是错的。

但财报有一个 RAG 不具备的性质：**它受会计恒等式约束**。
资产必然等于负债加所有者权益，净利润必然等于利润总额减去所得税。
这些约束是**确定性的**，不依赖任何模型推理。

因此策略是：让 LLM 只负责抽取（可能有噪声），让恒等式负责裁决（不会错）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from finverify.schemas import FinancialMetric, MetricKind, VerificationStatus

# 容差：审计报表内部恒等式应当**精确成立**，只留浮点误差余量。
# 早期版本用 1% 相对容差，结果 0.5% 的注入错误能蒙混过关 ——
# 那等于给幻觉开后门。放宽容差只在跨数据源比对时才有意义，
# 且应由调用方显式构造规则时传入，不要改全局默认。
DEFAULT_REL_TOLERANCE = 1e-6
"""1ppm 相对容差。仅用于吸收浮点运算误差。"""

DEFAULT_ABS_TOLERANCE = 0.01
"""绝对容差 1 分。覆盖到"厘"级别的记账误差。"""


@dataclass(frozen=True, slots=True)
class AccountingRule:
    """一条勾稽规则。

    Attributes
    ----------
    rule_id:
        规则标识
    lhs:
        等式左侧指标（被检验项）
    terms:
        等式右侧的**带符号项** ``(指标, 系数)``。
        会计恒等式都是线性组合，只支持求和是不够的 ——
        ``净利润 = 利润总额 - 所得税费用`` 需要负系数。
    description:
        人读描述，用于报告输出
    """

    rule_id: str
    lhs: MetricKind
    terms: tuple[tuple[MetricKind, float], ...]
    description: str
    tolerance_rel: float = DEFAULT_REL_TOLERANCE
    tolerance_abs: float = DEFAULT_ABS_TOLERANCE

    @property
    def rhs_kinds(self) -> tuple[MetricKind, ...]:
        return tuple(k for k, _ in self.terms)


@dataclass(slots=True)
class RuleResult:
    """单条规则的校验结果。"""

    rule_id: str
    status: VerificationStatus
    detail: str
    lhs_value: float | None = None
    rhs_value: float | None = None
    delta: float | None = None
    """lhs - rhs。"""

    missing: tuple[MetricKind, ...] = ()
    """缺失项。缺失即无法判定，返回 REVIEW_REQUIRED 而非 FAIL。"""

    def __str__(self) -> str:
        if self.status is VerificationStatus.FAIL:
            return f"[FAIL] {self.rule_id}: {self.detail}"
        if self.status is VerificationStatus.REVIEW_REQUIRED:
            return f"[REVIEW] {self.rule_id}: {self.detail}"
        return f"[PASS] {self.rule_id}: {self.detail}"


# --------------------------------------------------------------------------
# 标准会计恒等式
# --------------------------------------------------------------------------

BALANCE_SHEET_IDENTITY = AccountingRule(
    rule_id="BS_BALANCE",
    lhs=MetricKind.TOTAL_ASSETS,
    terms=(
        (MetricKind.TOTAL_LIABILITIES, 1.0),
        (MetricKind.TOTAL_EQUITY, 1.0),
    ),
    description="资产 = 负债 + 所有者权益",
)

NET_PROFIT_IDENTITY = AccountingRule(
    rule_id="IS_NET_PROFIT",
    lhs=MetricKind.NET_PROFIT,
    terms=(
        (MetricKind.TOTAL_PROFIT, 1.0),
        (MetricKind.INCOME_TAX, -1.0),  # 减项，系数为负
    ),
    description="净利润 = 利润总额 − 所得税费用",
)

CASH_FLOW_BRIDGE = AccountingRule(
    rule_id="CF_BRIDGE",
    lhs=MetricKind.NET_CASH_INCREASE,
    terms=(
        (MetricKind.OPERATING_CASH_FLOW, 1.0),
        (MetricKind.INVESTING_CASH_FLOW, 1.0),
        (MetricKind.FINANCING_CASH_FLOW, 1.0),
    ),
    description="现金净增加额 = 经营 + 投资 + 筹资活动现金流量净额",
)

DEFAULT_RULES: tuple[AccountingRule, ...] = (
    BALANCE_SHEET_IDENTITY,
    NET_PROFIT_IDENTITY,
    CASH_FLOW_BRIDGE,
)


class ValidationEngine:
    """勾稽校验引擎。

    核心原则：**缺失 ≠ 错误**。数据不全时返回 ``REVIEW_REQUIRED``，
    绝不因为"抽不到数字"就判定 FAIL —— 那是把不确定伪装成确定。
    """

    def __init__(
        self,
        rules: Iterable[AccountingRule] = DEFAULT_RULES,
        extra_checks: Sequence[Callable[[Sequence[FinancialMetric]], RuleResult]] = (),
    ) -> None:
        self._rules = tuple(rules)
        self._extra = tuple(extra_checks)

    # ------------------------------------------------------------------

    def validate(
        self, metrics: Sequence[FinancialMetric], period: str | None = None
    ) -> list[RuleResult]:
        """对一组指标执行全部规则。"""
        bucket = self._index(metrics, period)
        results = [self._run_rule(rule, bucket) for rule in self._rules]

        for check in self._extra:
            scoped = self._scope(metrics, period)
            results.append(check(scoped))

        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _scope(
        metrics: Sequence[FinancialMetric], period: str | None
    ) -> list[FinancialMetric]:
        if period is None:
            return list(metrics)
        return [m for m in metrics if m.number.period == period]

    @staticmethod
    def _index(
        metrics: Sequence[FinancialMetric], period: str | None
    ) -> dict[MetricKind, FinancialMetric]:
        """按 ``(period, kind)`` 建索引，同期同项后者覆盖前者（多来源时取后者）。"""
        out: dict[MetricKind, FinancialMetric] = {}
        for m in ValidationEngine._scope(metrics, period):
            out[m.kind] = m
        return out

    @staticmethod
    def _run_rule(
        rule: AccountingRule, bucket: dict[MetricKind, FinancialMetric]
    ) -> RuleResult:
        lhs_metric = bucket.get(rule.lhs)
        if lhs_metric is None:
            return RuleResult(
                rule_id=rule.rule_id,
                status=VerificationStatus.REVIEW_REQUIRED,
                detail=f"{rule.description} —— 未抽到 {rule.lhs.value}",
                missing=(rule.lhs,),
            )

        present: list[tuple[FinancialMetric, float]] = []
        missing: list[MetricKind] = []
        for kind, coeff in rule.terms:
            m = bucket.get(kind)
            if m is None:
                missing.append(kind)
            else:
                present.append((m, coeff))

        if missing:
            names = ", ".join(k.value for k in missing)
            return RuleResult(
                rule_id=rule.rule_id,
                status=VerificationStatus.REVIEW_REQUIRED,
                detail=(
                    f"{rule.description} —— 缺失构成项 [{names}]，"
                    f"无法完成勾稽（已抽到 {len(present)}/{len(rule.terms)} 项）"
                ),
                lhs_value=lhs_metric.number.value,
                missing=tuple(missing),
            )

        lhs = lhs_metric.number.value
        rhs = sum(coeff * m.number.value for m, coeff in present)
        delta = lhs - rhs
        tol = max(abs(lhs) * rule.tolerance_rel, rule.tolerance_abs)

        if abs(delta) <= tol:
            status = VerificationStatus.PASS
            detail = (
                f"{rule.description} —— 成立 "
                f"(左 {lhs:,.2f} / 右 {rhs:,.2f} / 差异 {delta:,.2f})"
            )
        else:
            status = VerificationStatus.FAIL
            detail = (
                f"{rule.description} —— 不成立 "
                f"(左 {lhs:,.2f} / 右 {rhs:,.2f} / 差异 {delta:,.2f}，"
                f"超出容差 {tol:,.2f})"
            )

        return RuleResult(
            rule_id=rule.rule_id,
            status=status,
            detail=detail,
            lhs_value=lhs,
            rhs_value=rhs,
            delta=delta,
        )

    # ------------------------------------------------------------------

    def worst_status(self, results: Iterable[RuleResult]) -> VerificationStatus:
        """汇总为单一状态，FAIL 优先于 REVIEW_REQUIRED。"""
        status = VerificationStatus.PASS
        for r in results:
            if r.status is VerificationStatus.FAIL:
                return VerificationStatus.FAIL
            if r.status is VerificationStatus.REVIEW_REQUIRED:
                status = VerificationStatus.REVIEW_REQUIRED
        return status
