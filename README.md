# finverify

**财报数字溯源与会计勾稽校验 —— 让 LLM 给出的每个财务数字都能点开验证**

> LLM 在财报上最贵的失败不是"答不上来"，而是**给出一个看起来合理但错误的数字**。
> finverify 的立场：**无法溯源的数字不应该被信任；能通过恒等式检验的数字才值得相信。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-68%2F68%20passing-brightgreen)]()

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

## 📊 实测：LLM 会把财报数字抽错吗？

让 3 个模型做 finverify 做的**同一件事**（从三表文本抽 12 个科目），
再把 LLM 输出的数字送进 finverify 自己的校验引擎：

```bash
python bench/run_bench.py --budget 0.50 --repeats 3   # 预算硬上限写在代码里
```

完整结果见 **[bench/RESULTS.md](bench/RESULTS.md)**（由 `results.json` 自动生成，
每个数字都可追溯）。

### 结论一：在本实验难度下，模型没抽错

`deepseek-chat` / `deepseek-reasoner` / `qwen-plus` 合计 108 个指标次，
**抽取准确率 100%，凭空捏造 0 次**，派生指标计算也全对。

> 如果只有这个结论，finverify 的价值主张是不成立的。

### 结论二：真正的风险是「忠实地抽错」

注入勾稽错误后，模型的抽取准确率**依然是 12/12 满分** ——
它完美复制了原文里那个被篡改的数字。

| 样本 | 注入额 | 恒等式实测差异 | 跨模型 |
|---|---:|---:|---|
| 权益多记 5,000 万 | -50,000,000 | **-50,000,000.00** | ✅ 3/3 恰好吻合 |
| 所得税少记 1,200 万 | -12,000,000 | **-12,000,000.00** | ✅ 3/3 恰好吻合 |

干净样本上 0 误报，注入样本上差异额精确等于注入额。

**抽取准确率回答的是「你抄对了吗」，业务要问的是「你抄的这组数字说得通吗」。
这是两个不同的问题。**

> 这份评测的样本量很小、样本是合成的、模型覆盖有限，
> 它的正确用法是**证明方法可行、给出可复现基线**，
> 而不是宣称「解决了财报幻觉」。真实年报评测是下一步。详见 RESULTS.md 的「局限」一节。

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
│   ├── schemas.py       # EvidenceSpan / VerifiedNumber / FinancialMetric / ScopeKind
│   ├── ingestion.py     # PDF → 带页码与坐标的表格行（含无框线表的行重建）
│   ├── scope.py         # 报表口径识别（合并 / 母公司 / 摘要 / 附注）
│   ├── extraction.py    # 表格行 → FinancialMetric（规则驱动）
│   ├── validation.py    # 会计恒等式引擎（带符号线性组合）
│   ├── provenance.py    # 溯源索引与渲染
│   ├── samples.py       # 合成年报生成器（含负样本注入）
│   ├── pipeline.py      # 编排 + 报告渲染
│   ├── web.py           # 单文件 HTML 报告（PDF 内嵌，零依赖）
│   └── cli.py           # 命令行入口
├── bench/
│   ├── llm_client.py       # LLM 调用 + 预算守卫（超 85% 即抛错终止）
│   ├── run_bench.py        # 多模型对照主运行器
│   ├── make_report.py      # 从原始结果自动生成报告（数字不经手工填写）
│   ├── fetch_annual_reports.py  # 从巨潮批量获取真实年报 PDF
│   ├── batch_analyze.py    # 19 份真实年报的批量审计
│   ├── ab_merge_final.py   # 行重建 A/B 对照（决定 merge_fragments 默认值）
│   ├── results.json        # 原始输出全文（含模型回复）
│   └── RESULTS.md          # 评测结果
├── samples/
│   ├── demo_annual_report.pdf        # 正常样本
│   ├── demo_injected_error.pdf       # 负样本 A：权益多记 5,000 万
│   ├── demo_injected_error_tax.pdf   # 负样本 B：所得税少记 1,200 万
│   ├── demo_report.html              # 负样本 A 的可交互报告（双击即开）
│   └── demo_report_tax.html         # 负样本 B 的可交互报告
├── tests/
│   ├── test_validation.py   # 8 项：校验层行为
│   ├── test_pipeline.py     # 23 项：端到端 + 回归 + 文本规范化
│   ├── test_scope.py        # 21 项：口径识别
│   ├── test_merge_rows.py   # 16 项：无框线表行重建
│   └── test_bench_paths.py  # 15 项：bench 路径解析（无写死盘符）
└── docs/
    ├── EVALUATION.md        # benchmark 设计
    └── REPORT.md            # HTML 报告的交互契约与自查方法
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
| **CJK 兼容汉字未规范化** | 换字体即静默丢指标，见下 |
| **只认 ASCII 负号 / ASCII 括号** | 负数被读成正数，见下 |

### 两个值得单独说的坑

**1. CJK 兼容码位 —— 换台机器就丢指标**

部分字体（Noto Sans CJK 等）会把「利」字的字形反向映射到 **U+F9DD**
（显示完全相同，码位不同）。于是 PDF 抽出来的是 `三、利润总额`
在码位层面却是 `三、\uF9DD润总额`，正则 `利润总额` 匹配不上：

```
缺 total_profit / net_profit / 全部现金流指标
→ 勾稽校验因"证据不足"判 REVIEW_REQUIRED
→ 报告看起来"很合理"，没有任何报错
```

**换字体就静默丢数据，而且丢得毫无痕迹。** 修复是入口处做 NFKC 规范化
（`ingestion.normalize_text`），把兼容写法折叠成规范形式，
下游只需要处理一种写法。

**2. 负号不止 ASCII 一种**

实测 PDF 里抽到的负号可能是 `-`(U+002D)、`‑`(U+2011)、`−`(U+2212)、
`－`(U+FF0D)、`–`、`—`；括号也可能是全角 `（）`。
旧实现只认 ASCII，于是 **-8.8 亿被读成 +8.8 亿**，
现金流量表恒等式反而"通过"了——错误被校验层掩盖。

对财务工具来说，符号丢失比解析失败危险得多。

> 这两个 bug 都不是靠读代码发现的，是把同一份数据换三种字体跑出来的。
> 回归用例已固化（`test_cjk_compatibility_ideograph_is_normalised` 等 4 项）。

### 在 19 份真实年报上抓到的四个缺陷

合成样本永远测不出下面这些 —— 它们全部来自对 19 份巨潮公开年报的批量审计
（沪深主板 / 创业板 / 科创板 / 北交所，70.3 MB，141–249 页）。

| 缺陷 | 后果 |
|---|---|
| 三表标题带**中文序号前缀**（`（一） 合并资产负债表`） | 8 个真标题一条都匹配不上，口径标记反而落在**审计报告的报表目录**上，母公司报表被当成合并口径 |
| 三表标题**无前缀**（裸的 `资产负债表`） | 零口径标记 → **根本不做口径过滤** → 附注里的数被当报表科目抽出（实测所得税费用取到递延所得税调节表的 -2,203,631.30） |
| **附注编号列**被当成金额 | `七、61`（附注七第 61 项）里的 `61` 被读成营业收入，真正的本期数 235,575,368.09 被忽略 |
| 破折号 `-` 表示「零/无」 | `四、净利润  -  -` 被当成「只有标签没有数值」，去吞掉下一行，净利润读成 0.00 |
| **一页两个口径被并成一张表** | 实测 603117 p88 的表格 bbox 横跨整页（74→759），上半是合并利润表尾部、下半是母公司报表。表格级判定只能二选一地错，母公司所得税 -2,878,668.74 被当成合并口径，恒等式报出差 10,002,568.40 的假 FAIL |
| 行重建时**全空行不切断分组** | 表头被吸进第一行数据。Windows 字体下碰巧还能匹配，**Linux / Noto CJK 上直接抽不到现金流量三个净额**——本地全绿、只有 CI 红 |

第三类最隐蔽，因为它**产生的是一个看起来合理的错值**：

```
[0] 一、营业收入   [1] 七、61   [3] 235,575,368.09   [4] 262,666,026.24
[0] 减:所得税费用 [1] 七、76   [3] -910,591.05      [4] 8,817,750.06
```

`parse_number` 是「从文本里抠数字」的通用工具（search 语义），
拿它判断「某个单元格是不是数值」就会把附注编号当成金额。
修复是分层的：**工具函数保持宽松，单元格层面加一道「整体必须是数字」的闸门**。

第四类则体现了会计惯例与程序直觉的冲突 —— 在中文财报里，
破折号不是负号，是「这一项没有数」。

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
- [x] 28 项测试（含跨字体与文本规范化回归）
- [x] Web UI（单文件 HTML，点击数字定位原文并高亮 bbox）
- [x] 多模型对照评测（3 模型 × 双任务，含可复现结果与成本核算）
- [ ] 50 份真实中文年报评测集（当前仅合成样本，见 RESULTS.md 局限）
- [ ] 附注级深挖（表外关联）

---

## 📄 License

MIT
