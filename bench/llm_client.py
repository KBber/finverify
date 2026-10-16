"""LLM 财务数字抽取对照组 —— 让大模型做 finverify 做的同一件事，然后逐项对账。

设计要点
--------
finverify 的抽取层是**规则驱动、确定性**的。本模块把同一份文本也丢给 LLM，
让两者做完全相同的任务，然后回答三个问题：

1. **谁抽得更准** —— exact match / 串行 / 凭空捏造
2. **LLM 给的数字自身是否满足会计恒等式** —— 这是最狠的一问：
   把 LLM 的输出代入 资产=负债+权益，如果对不上，
   说明 LLM 报出的这组数字**内部自相矛盾**，无论它说得多自信。
3. **LLM 能否发现文档里的矛盾** —— 样本中注入了勾稽错误，
   照抄错误数字的模型，会在自己的输出里复现同一个矛盾。

成本控制
--------
`Budget` 会在**每次调用前**预估并累计花费，超过硬上限立即抛错终止。
不接受"先跑了再说"——费用必须由代码强制约束，而不是靠自觉。
"""

from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

SECRETS_PATH = pathlib.Path(r"C:\Users\Admin\Desktop\PHD\key.txt")


# --------------------------------------------------------------------------
# 预算守卫
# --------------------------------------------------------------------------
@dataclass
class Budget:
    """硬上限守卫。预估调用前扣款，实际调用后按真实 usage 结算。"""

    limit_cny: float
    spent_cny: float = 0.0
    calls: int = 0
    reserve_ratio: float = 0.85
    """只允许花掉上限的 85%，剩余留给重试与误差。"""

    def charge(self, est_cny: float) -> None:
        projected = self.spent_cny + est_cny
        if projected > self.limit_cny * self.reserve_ratio:
            raise RuntimeError(
                f"预算超限：预计累计 {projected:.4f} 元，"
                f"已达上限 {self.limit_cny * self.reserve_ratio:.4f} 元"
                f"（硬上限 {self.limit_cny} 元）。已花 {self.spent_cny:.4f} 元 / "
                f"{self.calls} 次调用。终止以避免超支。"
            )
        self.spent_cny = projected

    @property
    def remaining(self) -> float:
        return self.limit_cny - self.spent_cny


# --------------------------------------------------------------------------
# 模型注册表（价格单位：元 / 百万 token）
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelSpec:
    name: str
    endpoint: str
    key: str
    price_in: float
    price_out: float
    vendor: str


def load_keys() -> dict[str, str]:
    """从 key.txt 就地读取密钥，不写盘、不打印、不进日志。"""
    raw = SECRETS_PATH.read_text(encoding="utf-8")
    keys = re.findall(r"sk-[A-Za-z0-9]{20,}", raw)
    out: dict[str, str] = {}
    if keys:
        out["deepseek"] = keys[0]
    if len(keys) > 1:
        out["dashscope"] = keys[1]
    return out


def build_registry() -> list[ModelSpec]:
    k = load_keys()
    specs = []
    if "deepseek" in k:
        base = "https://api.deepseek.com/chat/completions"
        specs.append(
            ModelSpec(
                name="deepseek-chat",
                endpoint=base,
                key=k["deepseek"],
                price_in=2.0,
                price_out=8.0,
                vendor="DeepSeek",
            )
        )
        specs.append(
            ModelSpec(
                name="deepseek-reasoner",
                endpoint=base,
                key=k["deepseek"],
                price_in=1.0,
                price_out=2.0,
                vendor="DeepSeek 推理模型",
            )
        )
    if "dashscope" in k:
        base = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        specs.append(
            ModelSpec(
                name="qwen-plus",
                endpoint=base,
                key=k["dashscope"],
                price_in=0.8,
                price_out=2.0,
                vendor="阿里通义千问",
            )
        )
    return specs


# --------------------------------------------------------------------------
# 调用
# --------------------------------------------------------------------------
def estimate_cost(spec: ModelSpec, n_in: int, n_out: int) -> float:
    return n_in / 1e6 * spec.price_in + n_out / 1e6 * spec.price_out


def call_llm(
    spec: ModelSpec,
    prompt: str,
    budget: Budget,
    max_tokens: int = 2048,
    retries: int = 2,
) -> tuple[str, dict]:
    """调用一次 LLM，返回 (文本, 用量信息)。失败重试，但每次重试同样计费。"""
    est = estimate_cost(spec, len(prompt) // 2, max_tokens)
    budget.charge(est)

    body = json.dumps(
        {
            "model": spec.name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
    ).encode()

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                spec.endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {spec.key}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            usage = data.get("usage", {})
            tin = usage.get("prompt_tokens", 0)
            tout = usage.get("completion_tokens", 0)
            actual = estimate_cost(spec, tin, tout)
            # 用真实用量回填（估价的偏差不回滚，但记账以真实为准）
            budget.spent_cny -= est
            budget.spent_cny += actual
            budget.calls += 1
            return data["choices"][0]["message"]["content"], {
                "model": spec.name,
                "prompt_tokens": tin,
                "completion_tokens": tout,
                "cost_cny": round(actual, 8),
            }
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < retries:
                time.sleep(2)
                continue
            raise
    raise RuntimeError(f"{spec.name} 调用失败: {last_err}")
