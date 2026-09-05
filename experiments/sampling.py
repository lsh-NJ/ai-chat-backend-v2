"""Day 4 实验：纯 Python 演示 temperature 和 top-p 采样。

不依赖 numpy，只用标准库。
这里不真正“随机采样”，而是直接观察概率分布怎么被 temperature / top-p 改变。
"""

from __future__ import annotations

import math
from typing import Sequence

TOKENS = ["A", "B", "C", "D", "E"]
LOGITS = [3.0, 2.0, 1.0, 0.5, 0.2]


def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    """带 temperature 的 softmax：logits 先除以 temperature 再归一化。"""
    if temperature <= 0:
        raise ValueError("temperature 必须大于 0")

    scaled = [x / temperature for x in logits]
    max_scaled = max(scaled)
    exps = [math.exp(x - max_scaled) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def top_p_indices(probs: Sequence[float], p: float) -> list[int]:
    """返回累计概率刚好 >= p 的最少 token 下标（按概率从高到低取）。"""
    if not 0 < p <= 1:
        raise ValueError("p 必须在 (0, 1] 之间")

    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    chosen: list[int] = []
    cumulative = 0.0
    for i in order:
        chosen.append(i)
        cumulative += probs[i]
        if cumulative >= p:
            break
    return sorted(chosen)


def main() -> None:
    print("原始 logits:", LOGITS)
    print()

    print("=== temperature 的影响 ===")
    for temp in (0.2, 1.0, 2.0):
        probs = softmax(LOGITS, temperature=temp)
        row = "  ".join(f"{t}={p:.3f}" for t, p in zip(TOKENS, probs, strict=True))
        print(f"temp={temp:<4} -> {row}")

    print()
    print("=== top-p 的影响（使用 temp=1.0 的分布）===")
    probs = softmax(LOGITS, temperature=1.0)
    for p in (0.9, 0.6):
        chosen = top_p_indices(probs, p)
        tokens = [TOKENS[i] for i in chosen]
        cum = sum(probs[i] for i in chosen)
        print(f"p={p}    -> 保留 token {tokens}，累计概率 {cum:.3f}")


if __name__ == "__main__":
    main()
