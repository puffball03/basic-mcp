"""The agent foundation: one hardcoded request, one real MCP tool call (plan M8).

The model is scripted, the server is real (stdio, temporary data dir). This is the agent-side
twin of the M1 hello client: proof the plumbing works before the ReAct loop arrives in M9.
"""

import json

import anthropic
import httpx2

from militech_agent import single_call
from militech_agent.llm import ScriptedLLM, reply, text, tool_use
from militech_agent.mcp_session import McpSession
from militech_agent.single_call import HARDCODED_REQUEST, SYSTEM_PROMPT, run_single_tool_call


class CountingSession:
    """Wraps a real session and records every tool call made through it."""

    def __init__(self, session):
        self.session = session
        self.tools = session.tools
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return await self.session.call_tool(name, arguments)


def _script(tool_name="get_employee_info", args=None):
    lookup = tool_use(tool_name, args or {"employee_id": "E1001"})
    return lookup, ScriptedLLM([
        reply(text("Thought: I need the employee's record first."), lookup),
        reply(text("Reyes Okafor is a cyberware_tech with one holo_display on file.")),
    ])  # fmt: skip


def test_the_hardcoded_request_is_demo_1():
    assert HARDCODED_REQUEST["employee_id"] == "E1001"
    assert "holo-display" in HARDCODED_REQUEST["message"]


async def test_makes_exactly_one_get_employee_info_call(data_dir):
    _, llm = _script()
    async with McpSession.stdio(data_dir=data_dir) as real:
        session = CountingSession(real)
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    assert session.calls == [("get_employee_info", {"employee_id": "E1001"})]
    assert result["tool"] == "get_employee_info"
    assert result["args"] == {"employee_id": "E1001"}
    assert result["observation"]["name"] == "Reyes Okafor"


async def test_returns_the_thought_and_the_model_answer(data_dir):
    _, llm = _script()
    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    assert result["thought"] == "Thought: I need the employee's record first."
    assert result["answer"] == "Reyes Okafor is a cyberware_tech with one holo_display on file."


async def test_first_request_carries_the_system_prompt_tools_and_request(data_dir):
    _, llm = _script()
    async with McpSession.stdio(data_dir=data_dir) as session:
        await run_single_tool_call(llm, session, HARDCODED_REQUEST)
        offered = session.tools

    first = llm.calls[0]
    assert first["system"] == SYSTEM_PROMPT
    assert first["tools"] == offered  # every server tool, as Claude definitions
    (user,) = first["messages"]
    assert user["role"] == "user"
    assert "E1001" in user["content"] and HARDCODED_REQUEST["message"] in user["content"]


async def test_observation_goes_back_to_the_model_as_a_tool_result(data_dir):
    lookup, llm = _script()
    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    _, assistant, tool_turn = llm.calls[1]["messages"]
    assert assistant == {"role": "assistant", "content": llm.responses[0].content}
    (block,) = tool_turn["content"]
    assert tool_turn["role"] == "user"
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == lookup.id
    assert block["is_error"] is False
    assert json.loads(block["content"]) == result["observation"]


async def test_tool_errors_are_flagged_to_the_model(data_dir):
    _, llm = _script(args={"employee_id": "E9999"})
    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    block = llm.calls[1]["messages"][2]["content"][0]
    assert result["observation"]["error"] == "employee_not_found"
    assert block["is_error"] is True


async def test_no_tool_call_is_reported_not_raised(data_dir):
    llm = ScriptedLLM([reply(text("I can answer without tools."))])
    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    assert result["tool"] is None
    assert result["observation"] is None
    assert result["answer"] == "I can answer without tools."
    assert len(llm.calls) == 1


async def test_every_tool_call_in_the_reply_gets_a_result(data_dir):
    # Fallback: FoundryLLM asks for one call at a time, but if a reply ever holds several
    # (seen live against Foundry before that setting), the API still needs a tool_result for
    # every tool_use in the next message. Run only the first; answer the rest as skipped so
    # nothing unexpected is written and the model is told to ask again one at a time.
    lookup = tool_use("get_employee_info", {"employee_id": "E1001"})
    limits = tool_use("get_policy_limits", {"role": "cyberware_tech"})
    llm = ScriptedLLM([
        reply(text("Thought: I need the record and the policy."), lookup, limits),
        reply(text("Reyes Okafor is a cyberware_tech.")),
    ])  # fmt: skip
    async with McpSession.stdio(data_dir=data_dir) as real:
        session = CountingSession(real)
        result = await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    assert session.calls == [("get_employee_info", {"employee_id": "E1001"})]  # still one call
    assert result["tool"] == "get_employee_info"

    executed, skipped = llm.calls[1]["messages"][2]["content"]
    assert (executed["tool_use_id"], executed["is_error"]) == (lookup.id, False)
    assert skipped["type"] == "tool_result"
    assert (skipped["tool_use_id"], skipped["is_error"]) == (limits.id, True)
    skip_note = json.loads(skipped["content"])
    assert skip_note["error"] == "tool_call_skipped"
    assert "one tool call at a time" in skip_note["message"]


# --- the manual entry point fails cleanly ---


def test_main_reports_api_errors_in_one_line(monkeypatch, capsys, tmp_path, data_dir):
    # A live run used to end in a long exception-group traceback. An API failure should print
    # one line with the error (and request id when there is one) and exit non-zero.
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", "https://example.services.ai.azure.com/anthropic")
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "test-key")
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    async def fail(*args, **kwargs):
        request = httpx2.Request("POST", "https://example.services.ai.azure.com/anthropic")
        raise anthropic.APIConnectionError(message="Connection error.", request=request)

    monkeypatch.setattr(single_call, "run_single_tool_call", fail)

    exit_code = single_call.main(env_file=tmp_path / "missing.env")

    err = capsys.readouterr().err
    assert exit_code == 1
    assert err.startswith("error: Foundry request failed")
    assert "Connection error." in err
    assert "Traceback" not in err
    assert "test-key" not in err
