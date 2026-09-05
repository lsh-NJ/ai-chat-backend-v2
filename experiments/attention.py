"""Day 2 实验：纯 Python 实现 scaled dot-product attention。

不依赖 numpy/torch，只用标准库，目的是看清 Q/K/V 和 softmax 的每一步。
"""

from __future__ import annotations

import math
from typing import Sequence

# 为了方便阅读，用类型别名表示二维矩阵
Matrix = Sequence[Sequence[float]]


def softmax(scores: Sequence[float]) -> list[float]:
    """把一维分数变成概率分布。

    实现细节：先减去最大值，避免 exp 溢出。这是数值稳定性的常见做法。
    """
    max_score = max(scores)
    exps = [math.exp(s - max_score) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def scaled_dot_product_attention(
    q: Matrix,
    k: Matrix,
    v: Matrix,
) -> tuple[list[list[float]], list[list[float]]]:
    """计算 scaled dot-product attention。

    Q: (seq_len_q, d_k)
    K: (seq_len_k, d_k)
    V: (seq_len_k, d_v)

    返回:
      outputs: (seq_len_q, d_v)
      attention_weights: (seq_len_q, seq_len_k)，每行和为 1
    """
    if not q or not k or not v:
        raise ValueError("Q/K/V 都不能为空")

    seq_len_q = len(q)
    seq_len_k = len(k)
    d_k = len(q[0])
    d_v = len(v[0])

    # 防御性检查：保证矩阵形状一致，错误早暴露
    if any(len(row) != d_k for row in q):
        raise ValueError("Q 的每一行维度必须等于 d_k")
    if any(len(row) != d_k for row in k):
        raise ValueError("K 的每一行维度必须等于 d_k")
    if any(len(row) != d_v for row in v):
        raise ValueError("V 的每一行维度必须等于 d_v")
    if len(v) != seq_len_k:
        raise ValueError("V 的行数必须等于 K 的行数（seq_len_k）")

    outputs: list[list[float]] = []
    attention_weights: list[list[float]] = []

    # 对每一个 query 独立计算
    for i in range(seq_len_q):
        # 1. 计算当前 query 与所有 key 的点积，再除以 sqrt(d_k)
        scores: list[float] = []
        for j in range(seq_len_k):
            dot = sum(q[i][t] * k[j][t] for t in range(d_k))
            scores.append(dot / math.sqrt(d_k))

        # 2. softmax：把分数变成“对每个 key 的关注权重”
        weights = softmax(scores)

        # 3. 用权重对 V 做加权求和，得到当前 query 的输出
        output = [
            sum(weights[j] * v[j][t] for j in range(seq_len_k))
            for t in range(d_v)
        ]

        outputs.append(output)
        attention_weights.append(weights)

    return outputs, attention_weights


def main() -> None:
    # 构造一个极小的例子：3 个 token，d_k = 2
    # K 表示“每个 token 是什么”，V 表示“每个 token 能提供什么信息”
    k = [
        [1.0, 0.0],   # token 0：偏“我”
        [0.8, 0.6],   # token 1：偏“爱”
        [0.0, 1.0],   # token 2：偏“北京”
    ]
    v = [
        [1.0, 0.0],   # token 0 提供的信息
        [0.0, 1.0],   # token 1 提供的信息
        [1.0, 1.0],   # token 2 提供的信息
    ]

    # 让“爱”这个 token 去查询上下文
    q_llove = [[0.8, 0.6]]

    outputs, weights = scaled_dot_product_attention(q_llove, k, v)

    print("单个 query（“爱”）的 attention 权重：")
    for j, w in enumerate(weights[0]):
        print(f"  token {j}: {w:.4f}")
    print(f"加权求和后的输出: {outputs[0]}")

    # 再演示 self-attention：让每个 token 都查询所有 token
    print("\nSelf-Attention（Q = K，每个 token 都看一遍全部 token）：")
    all_outputs, all_weights = scaled_dot_product_attention(k, k, v)
    for i in range(len(k)):
        print(f"  query token {i} 的注意力分布: "
              f"{[round(w, 4) for w in all_weights[i]]}")

    # 不变量：每一行权重之和必须约等于 1
    for i, row in enumerate(all_weights):
        total = sum(row)
        assert abs(total - 1.0) < 1e-9, f"第 {i} 行权重之和不等于 1"
    print("\n校验通过：每行 attention 权重之和都等于 1")


if __name__ == "__main__":
    main()
