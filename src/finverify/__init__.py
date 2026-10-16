"""finverify — 财报数字溯源与勾稽校验框架。

核心主张：LLM 在财报上给出一个数字时，必须同时给出它的**出处**，
并通过**会计恒等式**的确定性校验。

任何无法溯源、或未通过勾稽校验的数字，都不应该被信任。

最小可用示例::

    from finverify import audit
    report = audit("年报.pdf", period="2023")
    print(report.to_text())
"""

from finverify.extraction import (
    ExtractionTrace,
    classify_label,
    extract_metrics,
    parse_number,
)
from finverify.ingestion import Document, PageTable, TableRow, load_document
from finverify.pipeline import AuditReport, audit
from finverify.provenance import ProvenanceIndex
from finverify.schemas import (
    EvidenceQuality,
    EvidenceSpan,
    FinancialMetric,
    MetricKind,
    StatementKind,
    VerificationStatus,
    VerifiedNumber,
)
from finverify.validation import (
    BALANCE_SHEET_IDENTITY,
    CASH_FLOW_BRIDGE,
    DEFAULT_RULES,
    NET_PROFIT_IDENTITY,
    AccountingRule,
    RuleResult,
    ValidationEngine,
)

__version__ = "0.2.0"

__all__ = [
    # 数据结构
    "EvidenceSpan",
    "EvidenceQuality",
    "MetricKind",
    "FinancialMetric",
    "VerifiedNumber",
    "StatementKind",
    "VerificationStatus",
    # 校验
    "AccountingRule",
    "RuleResult",
    "ValidationEngine",
    "BALANCE_SHEET_IDENTITY",
    "NET_PROFIT_IDENTITY",
    "CASH_FLOW_BRIDGE",
    "DEFAULT_RULES",
    # 溯源
    "ProvenanceIndex",
    # 解析 / 抽取
    "Document",
    "PageTable",
    "TableRow",
    "load_document",
    "ExtractionTrace",
    "extract_metrics",
    "classify_label",
    "parse_number",
    # 编排
    "audit",
    "AuditReport",
    "__version__",
]
