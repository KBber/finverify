# finverify

**财报数字溯源与会计勾稽校验 —— 让 LLM 给出的每个财务数字都能点开验证**

> LLM 在财报上最贵的失败不是"答不上来"，而是**给出一个看起来合理但错误的数字**。
> finverify 的立场：**无法溯源的数字不应该被信任；能通过恒等式检验的数字才值得相信。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-24%2F24%20passing-brightgreen)]()

---

## 🎯 它解决什么

传统 RAG 无法发现财务数字幻觉，因为**检索结果本身就是错的**。

但财报有一个 RAG 不具备的性质：**它受会计恒等式约束**。

```
资产 = 负债 + 所有者权益
净利润 = 利润总额 − 所得税费用
现金净增加额 = 经营 + 投资 + 筹资活动现金流量净额
```

这些约束是**确定性的**，不依赖任何模型推理。

**策略因此是：让抽取环节尽量确定性（规则驱动），让恒等式负责裁决。**
LLM 在这里的正确位置是处理非结构化问答，而不是替代确定性的数字抽取。

---

## ✨ 实测效果

```bash
python -m finverify demo
```

合成三张表，故意注入两处勾稽错误 —— **错误数字在视觉上与正常数字完全一致，没有任何可辨识异常**：

| 样本 | 注入 | 检出 |
|---|---|---|
| 正常 | — | ✅ 三条恒等式全部 PASS |
| 负样本 A | 所有者权益多记 5,000 万 | ❌ `BS_BALANCE` 捕获，差异 -50,000,000.00 |
| 负样本 B | 所得税少记 1,200 万 | ❌ `IS_NET_PROFIT` 捕获，差异 -12,000,000.00 |

传统 RAG 永远不会发现这两类错误。finverify 是靠会计恒等式在**确定性层面**抓到的。

---

## 🚀 快速开始

```bash
pip install -e ".[parse]"

python -m finverify demo                      # 生成样本 + 可交互 HTML 报告
python -m finverify audit 年报.pdf            # 审计真实财报
python -m finverify audit 年报.pdf --format markdown --out 报告.md
python -m finverify audit 年报.pdf --format json
python -m finverify audit 年报.pdf --format html --out 报告.html   # 双击即开
```

退出码即结论，便于接 CI：`0=PASS` `1=FAIL` `3=REVIEW_REQUIRED`

```python
from finverify import audit

report = audit("2023年报.pdf", period="2023")
print(report.to_text())
```

---

## 三个核心设计

### 1. 溯源不是附加功能，是数据结构

裸 `float` 在本框架里不存在：

```python
from finverify import VerifiedNumber, EvidenceSpan

number = VerifiedNumber(
    value=9_450_000_000.00,
    unit="元",
    period="2023",
    evidence=EvidenceSpan(
        page=2,                                  # 第几页
        bbox=(463.0, 147.8, 538.9, 157.3),      # PDF 坐标，可直接高亮
        cell=(4, 1),                             # 表格 第4行第1列
        quality=EvidenceQuality.TABLE_CELL,
        snippet="资产总计 9,450,000,000.00",
    ),
)
```

**低质量证据在构造期就被拒绝**，不是在下游过滤：

```python
VerifiedNumber(..., evidence=span_with_UNVERIFIED)
# ValueError: 证据质量 UNVERIFIED 低于阈值，拒绝构造 VerifiedNumber
```

### 2. 三态而非二值

```python
class VerificationStatus(StrEnum):
    PASS               # 证据充分且通过全部勾稽
    FAIL               # 存在明确矛盾
    REVIEW_REQUIRED    # 证据不足 —— 转人工，绝不硬判
```

**关键原则：缺失 ≠ 错误。** 抽不到数字时返回 `REVIEW_REQUIRED` 而非 `FAIL` ——
那是把不确定伪装成确定。

### 3. 恒等式是带符号的线性组合

不是"左边等于右边求和"——`净利润 = 利润总额 − 所得税费用` 需要负系数：

```python
AccountingRule(
    rule_id="IS_NET_PROFIT",
    lhs=MetricKind.NET_PROFIT,
    terms=((MetricKind.TOTAL_PROFIT, 1.0),
           (MetricKind.INCOME_TAX, -1.0)),
    description="净利润 = 利润总额 − 所得税费用",
)
```

---

## 🖱️ 可视化报告：点数字，跳原文

勾稽失败时，最有说服力的不是一行 `Δ = -50,000,000.00`，而是**直接看到原文那一格**。

```bash
python -m finverify audit 年报.pdf --format html --out 报告.html
```

产出**单个 HTML 文件**，PDF 页面以 base64 内嵌 —— 不起服务、不联网、**双击就能打开**，
可以直接丢进简历附件或面试现场演示。

仓库里已附带三份现成产物：

| 文件 | 内容 |
|---|---|
| `samples/demo_clean.html` | 正常样本，三条恒等式全 PASS |
| `samples/demo_report.html` | 负样本 A：`BS_BALANCE` 失败 |
| `samples/demo_report_tax.html` | 负样本 B：`IS_NET_PROFIT` 失败 |

交互：**点击左侧任一数字**（无论 PASS 还是 FAIL）→ 右侧自动翻到对应 PDF 页，
并用红框精确套住那个数字所在的表格单元格。

```
┌─────────────────────┐        ┌──────────────────────────┐
│ 勾稽校验             │        │  [PAGE 2]                │
│ ❌ BS_BALANCE        │        │  项目        期末余额     │
│    资产 = 负债 + 权益 │  点击 → │  资产总计  9,450,000,000 │
│    Δ = -50,000,000   │        │  负债合计  4,535,000,000 │
│                     │        │  所有者权益合计           │
│ 可溯源指标           │        │      4,965,000,000  ←红框 │
│ ▸ 所有者权益合计     │        │  负债和所有者权益总计     │
└─────────────────────┘        └──────────────────────────┘
```

每个数字下方都标注了 `page / bbox / cell` 三级坐标 —— 这是"可验证"的物理证据，
而不是模型的自我声明。

---

## 📐 容差：一个被测试逼出来的决定

```python
tolerance = max(abs(lhs) * tolerance_rel, tolerance_abs)
tolerance_rel = 1e-6     # 1ppm
tolerance_abs = 0.01     # 1 分
```

早期版本用 **1% 相对容差**。结果是：注入 5,000 万（占总资产 0.5%）的错误**蒙混过关**。

**审计报表内部的恒等式应当精确成立**，1% 容差等于给幻觉开后门。
放宽容差只在跨数据源比对时有意义，且应由调用方显式构造规则时传入。

---

## 📁 结构

```
finverify/
├── src/finverify/
│   ├── schemas.py       # EvidenceSpan / VerifiedNumber / FinancialMetric
│   ├── ingestion.py     # PDF → 带页码与坐标的表格行
│   ├── extraction.py    # 表格行 → FinancialMetric（规则驱动）
│   ├── validation.py    # 会计恒等式引擎（带符号线性组合）
│   ├── provenance.py    # 溯源索引与渲染
│   ├── samples.py       # 合成年报生成器（含负样本注入）
│   ├── pipeline.py      # 编排 + 报告渲染
│   ├── web.py           # 单文件 HTML 报告（PDF 内嵌，零依赖）
│   └── cli.py           # 命令行入口
├── samples/
│   ├── demo_annual_report.pdf        # 正常样本
│   ├── demo_injected_error.pdf       # 负样本 A：权益多记 5,000 万
│   ├── demo_injected_error_tax.pdf   # 负样本 B：所得税少记 1,200 万
│   ├── demo_report.html              # 负样本 A 的可交互报告（双击即开）
│   └── demo_report_tax.html         # 负样本 B 的可交互报告
├── tests/
│   ├── test_validation.py   # 8 项：校验层行为
│   └── test_pipeline.py     # 16 项：端到端 + 回归
└── docs/
    └── EVALUATION.md        # benchmark 设计
```

---

## 🧪 测试覆盖的真实缺陷

开发过程中被测试抓出来、并已修复的问题（保留为回归用例）：

| 缺陷 | 后果 |
|---|---|
| 恒等式引擎只支持求和 | 净利润恒等式恒为 FAIL，校验层完全失效 |
| 容差 1% 过松 | 0.5% 的注入错误通过校验 |
| `parse_number` 负号取反两次 | -8.8 亿被解析成 +8.8 亿，现金流量表校验全错 |
| `"万元"` 被 `"元"` 抢先匹配 | 单位缩放静默失效 |
| 括号负数正则不含小数点 | `(1,234.00)` 识别不出负数 |
| `"负债和所有者权益总计"` 误映射为权益 | 资产恒等式失去意义 |
| `--format markdown --out` 落盘写成 text | 导出文件扩展名与内容不符 |
| Windows GBK 控制台输出 ✅ 崩溃 | `UnicodeEncodeError`，任何 Windows 用户跑 demo 即崩 |

---

## 📈 路线图

- [x] 溯源一等公民（`VerifiedNumber` 强制携带 `EvidenceSpan`）
- [x] 证据质量五级评分 + 构造期拒绝
- [x] 带符号线性组合的恒等式引擎
- [x] 三态决策 `PASS / FAIL / REVIEW_REQUIRED`
- [x] PDF 解析（PyMuPDF 无框线表格 + 行级坐标）
- [x] 中文科目名规则抽取（序号前缀、千分位、中文单位、括号负数）
- [x] 合成样本生成器 + 可注入负样本
- [x] CLI（text / markdown / json / html，退出码即结论）
- [x] 24 项测试
- [x] Web UI（单文件 HTML，点击数字定位原文并高亮 bbox）
- [ ] 中文年报 benchmark + 多模型数字幻觉率对比
- [ ] 附注级深挖（表外关联）

---

## 📄 License

MIT
