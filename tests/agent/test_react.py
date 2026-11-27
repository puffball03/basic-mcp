"""The ReAct loop (requirements §8, plan M9), with a scripted model and a real MCP server.

Rules these tests pin down:
- A step is one model reply. At most 8 per request; the 9th is never asked for.
- Every tool call needs a Thought (text before the tool_use). Missing it once: the call is
  not run and the model is asked to add one. Missing it again on the next try: the call runs
  and the trace records thought "(none)".
- The only way to finish is the local `submit_decision` tool; invalid input re-prompts.
- Hitting the cap: the agent itself escalates with UNCLEAR_REQUEST.
- Trace entries are {step, thought, action, args, observation}, sent to `on_step` live and
  written to traces/<request_id>.md when a traces dir is given.
"""

import json
from dataclasses import fields

from militech_agent.llm import ScriptedLLM, reply, text, tool_use
from militech_agent.mcp_session import McpSession
from militech_agent.prompts import SYSTEM_PROMPT
from militech_agent.react import MAX_STEPS, SUBMIT_DECISION_TOOL, run_agent
from militech_agent.result import AgentResult, trace_to_markdown

DEMO_1 = {
    "request_id": "REQ-0001",
    "employee_id": "E1001",
    "message": "Need a second holo-display. Debugging implant firmware on one screen is "
    "killing me.",
}
DEMO_5 = {"request_id": "REQ-0005", "employee_id": "E9999",
          "message": "Need a neural headset, choom."}  # fmt: skip


def thought(value):
    return text(f"Thought: {value}")


APPROVED = "Approved: a second holo-display is on its way."


def submit(decision="approve", reason_code=None, item="holo_display",
           request_type="additional", draft=APPROVED):  # fmt: skip
    return tool_use("submit_decision", {
        "decision": decision, "reason_code": reason_code, "item": item,
        "request_type": request_type, "draft_response": draft,
    })  # fmt: skip


class CountingSession:
    """A real session that records every MCP call made through it."""

    def __init__(self, session):
        self.session = session
        self.tools = session.tools
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return await self.session.call_tool(name, arguments)


def approve_script():
    return ScriptedLLM([
        reply(thought("Look up the employee's role and equipment."),
              tool_use("get_employee_info", {"employee_id": "E1001"})),
        reply(thought("Check whether a holo_display is within policy."),
              tool_use("check_request_eligibility",
                       {"employee_id": "E1001", "item": "holo_display"})),
        reply(thought("Within policy as an additional unit: record the approval."),
              tool_use("record_decision", {"employee_id": "E1001", "item": "holo_display",
                                           "decision": "approve", "request_type": "additional",
                                           "reason": "Second display within the cap of 2"})),
        reply(thought("Approval recorded; submit the decision."), submit()),
    ])  # fmt: skip


async def run(llm, data_dir, request=DEMO_1, **kwargs):
    async with McpSession.stdio(data_dir=data_dir) as real:
        session = CountingSession(real)
        result = await run_agent(llm, session, request, **kwargs)
    return result, session


# --- the approve path: Thought -> Action -> Observation, in order ---


async def test_approve_path_produces_ordered_trace(data_dir):
    result, session = await run(approve_script(), data_dir)

    assert [e["action"] for e in result.trace] == [
        "get_employee_info", "check_request_eligibility", "record_decision", "submit_decision",
    ]  # fmt: skip
    assert [e["step"] for e in result.trace] == [1, 2, 3, 4]
    assert result.trace[0]["thought"] == "Look up the employee's role and equipment."
    assert result.trace[1]["observation"]["status"] == "within_policy"
    assert result.trace[2]["observation"]["decision_id"].startswith("DEC-")
    assert [name for name, _ in session.calls] == [
        "get_employee_info", "check_request_eligibility", "record_decision",
    ]  # submit_decision is local: it never reaches the server  # fmt: skip


async def test_trace_entries_have_exactly_the_documented_keys(data_dir):
    result, _ = await run(approve_script(), data_dir)

    for entry in result.trace:
        assert set(entry) == {"step", "thought", "action", "args", "observation"}


async def test_result_carries_the_decision_and_ids(data_dir):
    result, _ = await run(approve_script(), data_dir)

    assert isinstance(result, AgentResult)
    assert (result.request_id, result.employee_id) == ("REQ-0001", "E1001")
    assert (result.decision, result.reason_code) == ("approve", None)
    assert (result.item, result.request_type) == ("holo_display", "additional")
    assert result.response_text == APPROVED
    assert result.decision_id.startswith("DEC-")
    assert result.ticket_id is None
    assert result.steps == 4
    assert result.stopped_by_cap is False


def test_agent_result_has_the_fields_requirements_8_lists():
    names = {f.name for f in fields(AgentResult)}
    required = {"request_id", "employee_id", "decision", "reason_code", "item", "request_type",
                "response_text", "ticket_id", "decision_id", "reflection", "trace"}  # fmt: skip
    assert required <= names


async def test_each_observation_goes_back_as_a_tool_result(data_dir):
    llm = approve_script()
    await run(llm, data_dir)

    second_request = llm.calls[1]["messages"]
    tool_turn = second_request[-1]
    (block,) = tool_turn["content"]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == llm.responses[0].content[1].id
    assert json.loads(block["content"])["name"] == "Reyes Okafor"


async def test_requests_carry_the_system_prompt_and_all_tools(data_dir):
    llm = approve_script()
    await run(llm, data_dir)

    first = llm.calls[0]
    assert first["system"] == SYSTEM_PROMPT
    names = [tool["name"] for tool in first["tools"]]
    assert names[-1] == "submit_decision"
    assert set(names) == {"get_employee_info", "get_policy_limits",
                          "check_request_eligibility", "flag_for_human_review",
                          "record_decision", "submit_decision"}  # fmt: skip
    assert "E1001" in first["messages"][0]["content"]


def test_submit_decision_tool_constrains_its_enums():
    schema = SUBMIT_DECISION_TOOL["input_schema"]
    assert schema["properties"]["decision"]["enum"] == ["approve", "deny", "escalate"]
    assert set(schema["required"]) == {"decision", "reason_code", "item", "request_type",
                                       "draft_response"}  # fmt: skip


# --- a missing Thought ---


async def test_missing_thought_reprompts_before_running_the_tool(data_dir):
    first_try = tool_use("get_employee_info", {"employee_id": "E1001"})
    llm = ScriptedLLM([
        reply(first_try),  # no Thought
        reply(thought("Look up the employee."),
              tool_use("get_employee_info", {"employee_id": "E1001"})),
        reply(thought("Done looking; escalate for the test."),
              submit("escalate", "UNCLEAR_REQUEST", draft="Passed to a human.")),
    ])  # fmt: skip

    result, session = await run(llm, data_dir)

    assert session.calls == [("get_employee_info", {"employee_id": "E1001"})]  # once, not twice
    reprompt = llm.calls[1]["messages"][-1]["content"][0]
    assert (reprompt["tool_use_id"], reprompt["is_error"]) == (first_try.id, True)
    assert "Thought" in json.loads(reprompt["content"])["message"]
    assert result.trace[0]["observation"]["error"] == "missing_thought"
    assert result.trace[1]["thought"] == "Look up the employee."


async def test_missing_thought_twice_runs_the_tool_and_records_none(data_dir):
    llm = ScriptedLLM([
        reply(tool_use("get_employee_info", {"employee_id": "E1001"})),
        reply(tool_use("get_employee_info", {"employee_id": "E1001"})),  # still no Thought
        reply(thought("Escalate for the test."),
              submit("escalate", "UNCLEAR_REQUEST", draft="Passed to a human.")),
    ])  # fmt: skip

    result, session = await run(llm, data_dir)

    assert len(session.calls) == 1
    assert result.trace[1]["thought"] == "(none)"
    assert result.trace[1]["observation"]["name"] == "Reyes Okafor"


# --- the 8-step cap ---


async def test_step_cap_escalates_with_unclear_request(data_dir):
    looping = [
        reply(thought(f"Check the policy again ({n})."),
              tool_use("get_policy_limits", {"role": "netrunner"}))
        for n in range(MAX_STEPS)
    ]  # fmt: skip
    llm = ScriptedLLM(looping)  # exactly 8 replies: a 9th request would raise

    result, session = await run(llm, data_dir)

    assert MAX_STEPS == 8
    assert len(llm.calls) == 8
    assert session.calls[-1][0] == "flag_for_human_review"
    assert session.calls[-1][1]["reason_code"] == "UNCLEAR_REQUEST"
    assert session.calls[-1][1]["employee_id"] == "E1001"
    assert (result.decision, result.reason_code) == ("escalate", "UNCLEAR_REQUEST")
    assert result.ticket_id.startswith("ESC-")
    assert result.ticket_id in result.response_text
    assert result.stopped_by_cap is True
    assert result.trace[-1]["action"] == "flag_for_human_review"
    escalations = (data_dir / "escalations.jsonl").read_text().splitlines()
    assert json.loads(escalations[0])["reason_code"] == "UNCLEAR_REQUEST"


async def test_text_only_replies_count_as_steps(data_dir):
    llm = ScriptedLLM([reply(text("Hmm, let me think about this.")) for _ in range(MAX_STEPS)])

    result, _ = await run(llm, data_dir)

    assert len(llm.calls) == 8
    assert "submit_decision" in llm.calls[1]["messages"][-1]["content"]  # nudged to finish
    assert result.stopped_by_cap is True


# --- errors from the server are observations, not crashes ---


async def test_mcp_error_observation_is_passed_to_the_model(data_dir):
    llm = ScriptedLLM([
        reply(thought("Look up the employee."),
              tool_use("get_employee_info", {"employee_id": "E9999"})),
        reply(thought("Not on file: escalate."),
              tool_use("flag_for_human_review",
                       {"employee_id": "E9999", "request": DEMO_5["message"],
                        "reason": "Employee not on file", "reason_code": "EMPLOYEE_NOT_FOUND"})),
        reply(thought("Ticket opened; submit."),
              submit("escalate", "EMPLOYEE_NOT_FOUND", item="neural_headset",
                     draft="Your request has been passed to a human reviewer.")),
    ])  # fmt: skip

    result, _ = await run(llm, data_dir, request=DEMO_5)

    error_turn = llm.calls[1]["messages"][-1]["content"][0]
    assert error_turn["is_error"] is True
    assert json.loads(error_turn["content"])["error"] == "employee_not_found"
    assert (result.decision, result.reason_code) == ("escalate", "EMPLOYEE_NOT_FOUND")
    assert result.ticket_id.startswith("ESC-")


async def test_protocol_error_does_not_end_the_run(data_dir):
    llm = ScriptedLLM([
        reply(thought("Call with no arguments."), tool_use("get_employee_info", {})),
        reply(thought("That failed; escalate."),
              submit("escalate", "UNCLEAR_REQUEST", draft="Passed to a human.")),
    ])  # fmt: skip

    result, _ = await run(llm, data_dir)

    assert result.trace[0]["observation"]["error"] == "tool_error"
    assert result.decision == "escalate"


# --- submit_decision must be valid ---


async def test_invalid_submit_decision_reprompts(data_dir):
    bad = submit(decision="maybe")
    llm = ScriptedLLM([
        reply(thought("Submit."), bad),
        reply(thought("Fix the decision value."),
              submit("escalate", "UNCLEAR_REQUEST", draft="Passed to a human.")),
    ])  # fmt: skip

    result, _ = await run(llm, data_dir)

    reprompt = llm.calls[1]["messages"][-1]["content"][0]
    assert (reprompt["tool_use_id"], reprompt["is_error"]) == (bad.id, True)
    assert "approve, deny, escalate" in json.loads(reprompt["content"])["message"]
    assert result.decision == "escalate"
    assert result.trace[0]["observation"]["error"] == "invalid_submission"


async def test_reason_codes_must_fit_the_decision(data_dir):
    llm = ScriptedLLM([
        reply(thought("Deny with no code."), submit("deny", None, draft="Denied.")),
        reply(thought("Escalate with a deny code."),
              submit("escalate", "NOT_ENTITLED", draft="Escalated.")),
        reply(thought("Approve with a code."), submit("approve", "NOT_ENTITLED")),
        reply(thought("Empty draft."), submit("deny", "NOT_ENTITLED", draft="  ")),
        reply(thought("Valid."), submit("deny", "NOT_ENTITLED", draft="Denied: not entitled.")),
    ])  # fmt: skip

    result, _ = await run(llm, data_dir)

    assert [e["observation"].get("error") for e in result.trace[:4]] == ["invalid_submission"] * 4
    assert (result.decision, result.reason_code) == ("deny", "NOT_ENTITLED")
    assert result.steps == 5


# --- the trace: live, and on disk ---


async def test_on_step_receives_each_entry_as_it_happens(data_dir):
    seen = []

    result, _ = await run(approve_script(), data_dir, on_step=seen.append)

    assert seen == result.trace


async def test_trace_is_written_to_traces_dir(data_dir, tmp_path):
    traces = tmp_path / "traces"

    result, _ = await run(approve_script(), data_dir, traces_dir=traces)

    written = (traces / "REQ-0001.md").read_text()
    assert written == trace_to_markdown(result)


async def test_no_traces_dir_writes_nothing(data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    await run(approve_script(), data_dir)

    assert not (tmp_path / "traces").exists()


async def test_markdown_shows_thought_action_observation_per_step(data_dir):
    result, _ = await run(approve_script(), data_dir)

    md = trace_to_markdown(result)

    assert md.startswith("# Trace REQ-0001")
    assert "E1001" in md and DEMO_1["message"] in md
    for entry in result.trace:
        assert f"## Step {entry['step']}" in md
        assert f"**Thought:** {entry['thought']}" in md
        assert f"**Action:** `{entry['action']}`" in md
    positions = [md.index(f"## Step {n}") for n in range(1, 5)]
    assert positions == sorted(positions)
    assert "**Observation:**" in md
    assert "## Decision" in md and "approve" in md
    assert result.decision_id in md
