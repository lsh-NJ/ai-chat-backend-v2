from collections.abc import AsyncIterator, Sequence

import pytest

from app.llm.contracts import LLMMessage, LLMRole
from app.llm.observability import (
    LLMCallRecord,
    RecordingLLMProvider,
    TokenUsage,
)


class FakeProvider:
    def __init__(
        self,
        complete_results=(),
        stream_sequences=(),
    ) -> None:
        self._complete_results = list(complete_results)
        self._stream_sequences = list(stream_sequences)

    async def complete(self, messages: Sequence[LLMMessage]) -> str:
        item = self._complete_results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(self, messages: Sequence[LLMMessage]) -> AsyncIterator[str]:
        sequence = self._stream_sequences.pop(0)

        async def generate() -> AsyncIterator[str]:
            for item in sequence:
                if isinstance(item, Exception):
                    raise item
                yield item

        return generate()


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    def record(self, record: LLMCallRecord) -> None:
        self.records.append(record)


class FakeCostCalculator:
    def calculate(self, usage: TokenUsage, model: str) -> float:
        return usage.input_tokens * 0.001 + usage.output_tokens * 0.002


def _token_counter(messages: Sequence[LLMMessage], output: str) -> TokenUsage:
    return TokenUsage(input_tokens=len(messages), output_tokens=len(output))


def _provider(
    *,
    complete_results=(),
    stream_sequences=(),
    trace_id: str = "trace-1",
) -> tuple[RecordingLLMProvider, FakeProvider, ListRecorder]:
    provider = FakeProvider(
        complete_results=complete_results,
        stream_sequences=stream_sequences,
    )
    recorder = ListRecorder()
    recording = RecordingLLMProvider(
        provider=provider,
        model="deepseek-v4-flash",
        prompt_version="v1",
        token_counter=_token_counter,
        cost_calculator=FakeCostCalculator(),
        recorder=recorder,
        trace_id_provider=lambda: trace_id,
    )
    return recording, provider, recorder


def test_token_usage_validation() -> None:
    usage = TokenUsage(input_tokens=1, output_tokens=2)
    assert usage.total_tokens == 3

    with pytest.raises(TypeError):
        TokenUsage(input_tokens=True, output_tokens=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=-1, output_tokens=1)


def test_llm_call_record_validation() -> None:
    with pytest.raises(ValueError, match="trace_id"):
        LLMCallRecord(
            trace_id="",
            model="m",
            prompt_version="v1",
            latency_seconds=0.1,
            success=True,
        )
    with pytest.raises(ValueError, match="latency_seconds"):
        LLMCallRecord(
            trace_id="t",
            model="m",
            prompt_version="v1",
            latency_seconds=-1,
            success=True,
        )


async def test_complete_records_success() -> None:
    recording, _, recorder = _provider(complete_results=["ok"])
    messages = [LLMMessage(role=LLMRole.USER, content="你好")]

    result = await recording.complete(messages)

    assert result == "ok"
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.trace_id == "trace-1"
    assert record.model == "deepseek-v4-flash"
    assert record.prompt_version == "v1"
    assert record.success is True
    assert record.input_tokens == 1
    assert record.output_tokens == 2
    assert record.cost == 0.001 + 0.004
    assert record.error_type is None
    assert record.error_message is None


async def test_complete_records_error() -> None:
    recording, _, recorder = _provider(
        complete_results=[RuntimeError("boom")]
    )

    with pytest.raises(RuntimeError, match="boom"):
        await recording.complete(
            [LLMMessage(role=LLMRole.USER, content="你好")]
        )

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.success is False
    assert record.error_type == "RuntimeError"
    assert record.error_message == "boom"
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.cost is None


async def test_stream_records_success_after_full_output() -> None:
    recording, _, recorder = _provider(stream_sequences=[["a", "b"]])
    messages = [LLMMessage(role=LLMRole.USER, content="你好")]

    chunks = [chunk async for chunk in recording.stream(messages)]

    assert chunks == ["a", "b"]
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.success is True
    assert record.output_tokens == 2


async def test_stream_records_error() -> None:
    recording, _, recorder = _provider(
        stream_sequences=[[RuntimeError("stream boom")]]
    )

    with pytest.raises(RuntimeError, match="stream boom"):
        async for _ in recording.stream(
            [LLMMessage(role=LLMRole.USER, content="你好")]
        ):
            pass

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.success is False
    assert record.error_type == "RuntimeError"
    assert record.error_message == "stream boom"


async def test_record_does_not_contain_message_or_output_content() -> None:
    secret = "这是不应该进日志的隐私内容"
    recording, _, recorder = _provider(complete_results=[f"回答 {secret}"])
    messages = [LLMMessage(role=LLMRole.USER, content=f"问题 {secret}")]

    await recording.complete(messages)

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert secret not in repr(record)


def test_recording_provider_rejects_non_provider() -> None:
    with pytest.raises(TypeError, match="LLMProvider"):
        RecordingLLMProvider(
            provider=object(),  # type: ignore[arg-type]
            model="m",
            prompt_version="v1",
            token_counter=_token_counter,
            cost_calculator=FakeCostCalculator(),
            recorder=ListRecorder(),
        )
