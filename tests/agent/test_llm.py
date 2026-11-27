"""The LLM layer: FoundryLLM for real runs, ScriptedLLM for tests (plan M8).

No test here talks to Foundry. FoundryLLM is checked with a fake client and with dummy
credentials that never leave the process.
"""

import pytest
from anthropic import AnthropicFoundry
from anthropic.types import Message, TextBlock, ToolUseBlock

from militech_agent.llm import (
    DEFAULT_MODEL,
    ONE_TOOL_AT_A_TIME,
    FoundryLLM,
    LLMClient,
    MissingCredentialsError,
    ScriptedLLM,
    ScriptExhaustedError,
    reply,
    text,
    tool_use,
)

ENDPOINT = "https://example-resource.services.ai.azure.com/anthropic"


@pytest.fixture
def no_foundry_env(monkeypatch):
    for name in ("AZURE_FOUNDRY_ENDPOINT", "AZURE_FOUNDRY_API_KEY", "MILITECH_MODEL"):
        monkeypatch.delenv(name, raising=False)


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


# --- ScriptedLLM: canned responses, replayed in order ---


def test_reply_builds_a_real_sdk_message():
    message = reply(
        text("Thought: look them up."), tool_use("get_employee_info", {"employee_id": "E1001"})
    )

    assert isinstance(message, Message)
    assert isinstance(message.content[0], TextBlock)
    assert isinstance(message.content[1], ToolUseBlock)
    assert message.content[1].input == {"employee_id": "E1001"}
    assert message.stop_reason == "tool_use"  # inferred: a tool_use block is present


def test_reply_without_tools_ends_the_turn():
    assert reply(text("All done.")).stop_reason == "end_turn"


def test_tool_use_ids_are_unique():
    first, second = tool_use("a", {}), tool_use("b", {})

    assert first.id != second.id
    assert first.id.startswith("toolu_")


def test_scripted_llm_replays_in_order():
    first, second = reply(text("one")), reply(text("two"))
    llm = ScriptedLLM([first, second])

    assert llm.create(messages=[], tools=[], system="s") is first
    assert llm.create(messages=[], tools=[], system="s") is second


def test_scripted_llm_records_each_request():
    llm = ScriptedLLM([reply(text("ok"))])
    messages = [{"role": "user", "content": "Need a terminal"}]
    tools = [{"name": "get_employee_info", "description": "d", "input_schema": {"type": "object"}}]

    llm.create(messages=messages, tools=tools, system="You are Militech Asset Control.")
    messages.append({"role": "assistant", "content": "mutated later"})

    (call,) = llm.calls
    assert call["system"] == "You are Militech Asset Control."
    assert call["tools"] == tools
    assert call["messages"] == [{"role": "user", "content": "Need a terminal"}]  # a snapshot


def test_scripted_llm_fails_loudly_when_the_script_runs_out():
    llm = ScriptedLLM([reply(text("only one"))])
    llm.create(messages=[], tools=[], system="s")

    with pytest.raises(ScriptExhaustedError, match="2nd call"):
        llm.create(messages=[], tools=[], system="s")


def test_both_clients_satisfy_the_protocol():
    assert isinstance(ScriptedLLM([]), LLMClient)
    assert isinstance(FoundryLLM(endpoint=ENDPOINT, api_key="test-key"), LLMClient)


# --- FoundryLLM: Claude on Azure Foundry, credentials from the environment ---


def test_foundry_llm_builds_an_anthropic_foundry_client(no_foundry_env):
    llm = FoundryLLM(endpoint=ENDPOINT, api_key="test-key")

    assert isinstance(llm.client, AnthropicFoundry)
    assert str(llm.client.base_url).rstrip("/") == ENDPOINT
    assert llm.model == DEFAULT_MODEL == "claude-sonnet-5-5"


def test_foundry_llm_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "test-key")
    monkeypatch.setenv("MILITECH_MODEL", "claude-opus-5-5")

    llm = FoundryLLM()

    assert str(llm.client.base_url).rstrip("/") == ENDPOINT
    assert llm.model == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({}, "AZURE_FOUNDRY_ENDPOINT, AZURE_FOUNDRY_API_KEY"),
        ({"AZURE_FOUNDRY_ENDPOINT": ENDPOINT}, "AZURE_FOUNDRY_API_KEY"),
        ({"AZURE_FOUNDRY_API_KEY": "secret-value"}, "AZURE_FOUNDRY_ENDPOINT"),
    ],
)
def test_missing_credentials_name_the_variables_not_the_values(
    monkeypatch, no_foundry_env, env, missing
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(MissingCredentialsError) as excinfo:
        FoundryLLM()

    assert missing in str(excinfo.value)
    assert ".env" in str(excinfo.value)  # tells you where to put them
    assert "secret-value" not in str(excinfo.value)


def test_foundry_llm_create_sends_model_system_messages_and_tools(no_foundry_env):
    canned = reply(text("Hello"))
    fake = FakeClient(canned)
    llm = FoundryLLM(endpoint=ENDPOINT, api_key="test-key", client=fake, max_tokens=4096)
    messages = [{"role": "user", "content": "hi"}]
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]

    assert llm.create(messages=messages, tools=tools, system="sys") is canned
    assert fake.messages.calls == [
        {"model": "claude-sonnet-5-5", "max_tokens": 4096, "system": "sys",
         "messages": messages, "tools": tools, "tool_choice": ONE_TOOL_AT_A_TIME}
    ]  # fmt: skip


def test_foundry_llm_asks_for_one_tool_call_at_a_time():
    # Side-effecting tools (record_decision, flag_for_human_review) must run in order, one
    # Thought -> Action -> Observation per step. "auto" keeps the model free to answer
    # without a tool; forcing "any"/"tool" is rejected by claude-sonnet-5-5.
    assert ONE_TOOL_AT_A_TIME == {"type": "auto", "disable_parallel_tool_use": True}


def test_scripted_llm_accepts_a_reply_function():
    # A scripted reply can depend on the conversation, e.g. to quote the decision_id the
    # server just returned, the way the real model does.
    def quote_last(messages):
        return reply(text(f"Got {len(messages)} messages."))

    llm = ScriptedLLM([reply(text("first")), quote_last])
    llm.create(messages=[{"role": "user", "content": "hi"}], tools=[], system="s")
    second = llm.create(
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x"}],
        tools=[],
        system="s",
    )

    assert second.content[0].text == "Got 2 messages."


def test_foundry_llm_sends_no_tool_fields_without_tools(no_foundry_env):
    # The reflection reviewer calls the model with no tools; the API rejects tool_choice when
    # no tools are given, so neither field is sent.
    fake = FakeClient(reply(text('{"verdict": "confirmed", "issues": []}')))
    llm = FoundryLLM(endpoint=ENDPOINT, api_key="test-key", client=fake)

    llm.create(messages=[{"role": "user", "content": "review"}], tools=[], system="reviewer")

    (sent,) = fake.messages.calls
    assert "tools" not in sent and "tool_choice" not in sent
