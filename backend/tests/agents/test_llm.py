from types import SimpleNamespace

import pytest

from app.agents.contracts import AgentIntent, SupervisorPlan
from app.agents.llm import (
    AgentModelExecutionError,
    AgentModelResponseError,
    DeepSeekJsonModel,
)


class FakeCompletions:
    def __init__(self, content: str | tuple[str, ...]) -> None:
        self.contents = (content,) if isinstance(content, str) else content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_deepseek_adapter_requests_json_and_validates_schema() -> None:
    completions = FakeCompletions(
        '{"intent":"KNOWLEDGE_QUESTION","scenario_key":null,'
        '"knowledge_space":"access_and_security",'
        '"rewritten_query":"VPN policy","scenario_payload":{}}'
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    model = DeepSeekJsonModel("unused", client=client)

    result = model.generate(
        SupervisorPlan,
        system_prompt="Classify intent.",
        user_prompt="How does VPN approval work?",
    )

    assert result.intent is AgentIntent.KNOWLEDGE_QUESTION
    call = completions.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in call["messages"][0]["content"]


def test_deepseek_adapter_rejects_invalid_structured_output() -> None:
    completions = FakeCompletions("not-json")
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    with pytest.raises(AgentModelResponseError):
        DeepSeekJsonModel("unused", client=client).generate(
            SupervisorPlan,
            system_prompt="Classify intent.",
            user_prompt="hello",
        )
    assert len(completions.calls) == 2


def test_deepseek_adapter_retries_once_after_invalid_json_output() -> None:
    completions = FakeCompletions(
        (
            '{"intent":"OFFICE_PROCUREMENT_REQUEST","unexpected":true}',
            '{"intent":"OFFICE_PROCUREMENT_REQUEST","scenario_key":"procurement",'
            '"knowledge_space":"procurement","rewritten_query":"采购打印纸",'
            '"scenario_payload":{"items":[{"item_name":"A4打印纸",'
            '"item_category":"办公耗材","quantity":2}],'
            '"estimated_total_amount":260,"cost_center_code":"CC-ADMIN",'
            '"desired_date":"2026-08-05",'
            '"delivery_location_code":"上海总部行政前台",'
            '"business_reason":"新员工入职办公区补充"}}',
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = DeepSeekJsonModel("unused", client=client).generate(
        SupervisorPlan,
        system_prompt="Classify intent.",
        user_prompt="申请采购办公用品：A4打印纸2箱。",
    )

    assert result.intent is AgentIntent.OFFICE_PROCUREMENT_REQUEST
    assert result.scenario_payload["cost_center_code"] == "CC-ADMIN"
    assert len(completions.calls) == 2
    correction = completions.calls[1]["messages"][-1]["content"]
    assert "replacement" in correction
    assert completions.calls[1]["messages"][-2]["content"] == (
        '{"intent":"OFFICE_PROCUREMENT_REQUEST","unexpected":true}'
    )


def test_deepseek_adapter_sanitizes_remote_failures() -> None:
    class FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("provider detail must not escape")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FailingCompletions()),
    )
    with pytest.raises(AgentModelExecutionError):
        DeepSeekJsonModel("unused", client=client).generate(
            SupervisorPlan,
            system_prompt="Classify intent.",
            user_prompt="hello",
        )
