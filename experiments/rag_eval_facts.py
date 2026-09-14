"""Week 16 端到端评测语料事实（候选集，待人工复核）。

每个 `Fact` 对应一个独立 chunk id，便于评测脚本精确断言期望 chunk。
本文件只定义语料和问题模板；生成的 JSONL 默认 `reviewed=false`，
必须由人工逐条复核后才算正式评测集。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.chunking import Chunk


@dataclass(frozen=True, slots=True)
class Fact:
    chunk_id: str
    content: str
    topic: str
    answer_terms: tuple[str, ...]
    direct_question: str
    paraphrase_question: str
    keyword_question: str


FACTS: tuple[Fact, ...] = (
    Fact(
        chunk_id="refund-policy",
        content="退款政策：用户可以在七天内无理由申请退款。",
        topic="退款政策",
        answer_terms=("七天内", "无理由"),
        direct_question="退款政策是什么？",
        paraphrase_question="买错了可以退吗？",
        keyword_question="七天无理由退款是什么规定？",
    ),
    Fact(
        chunk_id="refund-steps",
        content="退款流程：先提交申请，再等待审核，审核通过后退款到账。",
        topic="退款流程",
        answer_terms=("提交申请", "审核"),
        direct_question="退款怎么申请？",
        paraphrase_question="我想把钱退回来，应该怎么操作？",
        keyword_question="退款需要先做什么？",
    ),
    Fact(
        chunk_id="refund-time",
        content="退款到账时间：审核通过后通常需要三到五个工作日。",
        topic="退款到账时间",
        answer_terms=("三到五个工作日",),
        direct_question="退款多久到账？",
        paraphrase_question="钱退回来要等几天？",
        keyword_question="退款到账时间是多少？",
    ),
    Fact(
        chunk_id="return-shipping",
        content="退货运费：质量问题由商家承担运费，非质量问题由用户承担。",
        topic="退货运费",
        answer_terms=("质量问题", "商家承担"),
        direct_question="退货运费谁承担？",
        paraphrase_question="退货邮费怎么算？",
        keyword_question="质量问题退货运费谁出？",
    ),
    Fact(
        chunk_id="shipping-time",
        content="发货时间：订单通常在下单后两个工作日内发出。",
        topic="发货时间",
        answer_terms=("两个工作日",),
        direct_question="发货需要多久？",
        paraphrase_question="下单后什么时候发货？",
        keyword_question="两个工作日内发货是什么意思？",
    ),
    Fact(
        chunk_id="shipping-fee",
        content="运费规则：单笔订单满九十九元包邮，不满则收取十元运费。",
        topic="运费规则",
        answer_terms=("满九十九元包邮", "十元运费"),
        direct_question="运费怎么计算？",
        paraphrase_question="多少钱可以包邮？",
        keyword_question="满九十九元包邮的规则是什么？",
    ),
    Fact(
        chunk_id="courier",
        content="合作快递：默认使用顺丰或中通，具体以发货通知为准。",
        topic="合作快递",
        answer_terms=("顺丰", "中通"),
        direct_question="你们用什么快递发货？",
        paraphrase_question="订单会走哪家快递？",
        keyword_question="默认快递是哪两家？",
    ),
    Fact(
        chunk_id="order-tracking",
        content="物流查询：订单发货后可以在订单详情页查看物流单号和轨迹。",
        topic="物流查询",
        answer_terms=("订单详情页", "物流单号"),
        direct_question="怎么查看物流信息？",
        paraphrase_question="我的包裹到哪了？",
        keyword_question="物流单号在哪里看？",
    ),
    Fact(
        chunk_id="payment-methods",
        content="支付方式：支持微信支付、支付宝和银行卡支付。",
        topic="支付方式",
        answer_terms=("微信支付", "支付宝", "银行卡"),
        direct_question="支持哪些支付方式？",
        paraphrase_question="可以用支付宝付款吗？",
        keyword_question="微信支付和银行卡都支持吗？",
    ),
    Fact(
        chunk_id="invoice",
        content="发票规则：确认收货后可以在订单页面申请电子发票。",
        topic="发票规则",
        answer_terms=("确认收货后", "电子发票"),
        direct_question="怎么开发票？",
        paraphrase_question="可以给我一张发票吗？",
        keyword_question="电子发票在哪里申请？",
    ),
    Fact(
        chunk_id="coupon",
        content="优惠券规则：单笔订单限用一张优惠券，不能叠加使用。",
        topic="优惠券规则",
        answer_terms=("限用一张", "不能叠加"),
        direct_question="优惠券可以叠加使用吗？",
        paraphrase_question="两个券能一起用吗？",
        keyword_question="单笔订单能用几张优惠券？",
    ),
    Fact(
        chunk_id="membership-points",
        content="会员积分：每消费一百元获得一个积分，积分可以兑换优惠券。",
        topic="会员积分",
        answer_terms=("一百元", "一个积分"),
        direct_question="会员积分怎么获得？",
        paraphrase_question="消费多少钱有一个积分？",
        keyword_question="积分可以兑换什么？",
    ),
    Fact(
        chunk_id="contact-email",
        content="客服邮箱：support@example.com，工作时间内会尽快回复。",
        topic="客服邮箱",
        answer_terms=("support@example.com",),
        direct_question="客服邮箱是多少？",
        paraphrase_question="怎么联系你们？",
        keyword_question="support@example.com 是客服邮箱吗？",
    ),
    Fact(
        chunk_id="support-hours",
        content="客服时间：工作日九点到十八点在线，节假日可能延迟回复。",
        topic="客服时间",
        answer_terms=("工作日九点到十八点",),
        direct_question="客服工作时间是什么时候？",
        paraphrase_question="周末有人工客服吗？",
        keyword_question="客服几点上班？",
    ),
    Fact(
        chunk_id="warranty",
        content="保修政策：电子产品自签收之日起提供一年保修。",
        topic="保修政策",
        answer_terms=("一年保修",),
        direct_question="电子产品保修多久？",
        paraphrase_question="坏了可以修吗？",
        keyword_question="一年保修的起算时间是什么？",
    ),
    Fact(
        chunk_id="cancellation",
        content="取消订单：订单未发货前可以免费取消。",
        topic="取消订单",
        answer_terms=("未发货前", "免费取消"),
        direct_question="订单可以取消吗？",
        paraphrase_question="不想买了能取消吗？",
        keyword_question="发货前取消订单收费吗？",
    ),
    Fact(
        chunk_id="address-change",
        content="修改地址：订单发货前可以联系客服修改收货地址。",
        topic="修改地址",
        answer_terms=("发货前", "联系客服"),
        direct_question="可以修改收货地址吗？",
        paraphrase_question="地址填错了怎么办？",
        keyword_question="发货前能改地址吗？",
    ),
    Fact(
        chunk_id="privacy",
        content="隐私政策：我们不会向任何第三方出售用户个人信息。",
        topic="隐私政策",
        answer_terms=("不会", "出售用户个人信息"),
        direct_question="你们会把我的信息卖给第三方吗？",
        paraphrase_question="我的个人数据安全吗？",
        keyword_question="用户信息会被出售吗？",
    ),
    Fact(
        chunk_id="password-reset",
        content="找回密码：在登录页点击忘记密码，通过邮箱验证后可以重置。",
        topic="找回密码",
        answer_terms=("忘记密码", "邮箱验证"),
        direct_question="忘记密码怎么办？",
        paraphrase_question="登录密码不记得了怎么重置？",
        keyword_question="怎么通过邮箱重置密码？",
    ),
    Fact(
        chunk_id="account-delete",
        content="注销账号：联系客服提交注销申请，注销后数据不可恢复。",
        topic="注销账号",
        answer_terms=("联系客服", "不可恢复"),
        direct_question="怎么注销账号？",
        paraphrase_question="我不想用了，账号能删除吗？",
        keyword_question="注销后数据还能恢复吗？",
    ),
)


def build_fact_chunks() -> tuple[Chunk, ...]:
    return tuple(
        Chunk(
            id=fact.chunk_id,
            document_id=f"doc-{fact.chunk_id}::v1",
            source=f"docs/{fact.chunk_id}.md",
            content=fact.content,
            metadata={"lang": "zh", "topic": fact.topic},
            start=0,
            end=len(fact.content),
        )
        for fact in FACTS
    )


FACT_CHUNKS: tuple[Chunk, ...] = build_fact_chunks()
