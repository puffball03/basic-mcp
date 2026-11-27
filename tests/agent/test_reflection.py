"""Reflection (requirements §8, plan M11): a reviewer checks the draft before it ships.

The reviewer (a separate model call, no tools) sees the draft, the decision and the raw tool
observations, and answers {"verdict": "confirmed"|"revise", "issues": [...]} as JSON. On
revise, the agent's model rewrites the text only (the decision is already recorded) and the
reviewer looks again. At most 2 reviews and 1 rewrite; a draft still marked revise after
round 2 ships with its issues noted. Every round is kept in AgentResult.reflection.
"""

import json

from militech_agent.llm import ScriptedLLM, reply, text, tool_use
from militech_agent.mcp_session import McpSession
from militech_agent.react import MAX_STEPS, run_agent
from militech_agent.reflection import REVIEWER_PROMPT, REWRITE_PROMPT, reflect
from militech_agent.result import trace_to_markdown

DEC = "DEC-20261123-0001"
FACTS = {"decision": "approve", "reason_code": None, "item": "terminal",
         "request_type": "replacement", "decision_id": DEC, "ticket_id": None}  # fmt: skip
TRACE = [
    {"step": 1, "thought": "t", "action": "check_request_eligibility",
     "args": {"employee_id": "E1005", "item": "terminal"},
     "observation": {"status": "within_policy", "next_eligible_date": "2026-05-20"}},
    {"step": 2, "thought": "t", "action": "record_decision",
     "args": {"employee_id": "E1005", "item": "terminal", "decision": "approve",
              "request_type": "replacement", "reason": "Past refresh"},
     "observation": {"decision_id": DEC, "created_at": "2026-11-23T12:00:00+00:00"}},
    {"step": 3, "thought": "t", "action": "submit_decision", "args": {},
     "observation": {"status": "submitted"}},
]  # fmt: skip

SHIPS_FRIDAY = f"Approved ({DEC}). Your new terminal will ship Friday."
CLEAN = f"Approved ({DEC}): your terminal replacement is within policy."


def verdict(value, *issues):
    return reply(text(json.dumps({"verdict": value, "issues": list(issues)})))


def run_reflect(draft, reviewer_replies, writer_replies=()):
    reviewer, writer = ScriptedLLM(reviewer_replies), ScriptedLLM(writer_replies)
    final, rounds = reflect(draft, FACTS, TRACE, reviewer=reviewer, writer=writer)
    return final, rounds, reviewer, writer


# --- the two paths in the plan ---


def test_clean_draft_is_confirmed_in_round_1():
    final, rounds, reviewer, writer = run_reflect(CLEAN, [verdict("confirmed")])

    assert final == CLEAN
    assert rounds == [{"round": 1, "draft": CLEAN, "verdict": "confirmed", "issues": []}]
    assert len(reviewer.calls) == 1
    assert writer.calls == []  # nothing to rewrite


def test_unconfirmed_promise_is_revised_then_confirmed():
    final, rounds, _, writer = run_reflect(
        SHIPS_FRIDAY,
        [verdict("revise", "Promises shipping on Friday; no tool confirmed a date."),
         verdict("confirmed")],
        [reply(text(CLEAN))],
    )  # fmt: skip

    assert final == CLEAN
    assert [r["verdict"] for r in rounds] == ["revise", "confirmed"]
    assert rounds[0]["draft"] == SHIPS_FRIDAY
    assert rounds[0]["issues"] == ["Promises shipping on Friday; no tool confirmed a date."]
    assert rounds[1]["draft"] == CLEAN
    assert len(writer.calls) == 1


def test_still_revise_after_round_2_ships_with_issues_noted():
    final, rounds, reviewer, writer = run_reflect(
        SHIPS_FRIDAY,
        [verdict("revise", "Promises shipping."), verdict("revise", "Still vague.")],
        [reply(text(f"Approved ({DEC}). It should arrive soon."))],
    )  # fmt: skip

    assert final == f"Approved ({DEC}). It should arrive soon."  # the reviewed round-2 draft
    assert len(rounds) == 2 and rounds[1]["issues"] == ["Still vague."]
    assert len(reviewer.calls) == 2
    assert len(writer.calls) == 1  # no unreviewed third draft


# --- what the reviewer and the writer are given ---


def test_reviewer_sees_draft_decision_and_observations_without_tools():
    _, _, reviewer, _ = run_reflect(CLEAN, [verdict("confirmed")])

    (call,) = reviewer.calls
    assert call["system"] == REVIEWER_PROMPT
    assert call["tools"] == []
    payload = json.loads(call["messages"][0]["content"])
    assert payload["draft"] == CLEAN
    assert payload["decision"] == FACTS
    actions = [o["action"] for o in payload["observations"]]
    assert actions == ["check_request_eligibility", "record_decision"]  # submit_decision left out
    assert payload["observations"][1]["observation"]["decision_id"] == DEC


def test_writer_gets_the_issues_and_keeps_the_decision():
    _, _, _, writer = run_reflect(
        SHIPS_FRIDAY, [verdict("revise", "Promises shipping."), verdict("confirmed")],
        [reply(text(CLEAN))],
    )  # fmt: skip

    (call,) = writer.calls
    assert call["system"] == REWRITE_PROMPT
    assert call["tools"] == []
    payload = json.loads(call["messages"][0]["content"])
    assert payload["draft"] == SHIPS_FRIDAY
    assert payload["issues"] == ["Promises shipping."]
    assert payload["decision"] == FACTS


def test_a_rewrite_that_drops_the_id_gets_it_back():
    final, rounds, _, _ = run_reflect(
        SHIPS_FRIDAY, [verdict("revise", "Promises shipping."), verdict("confirmed")],
        [reply(text("Approved: your terminal replacement is within policy."))],
    )  # fmt: skip

    assert DEC in final  # the guardrail requires the id; reflection never removes it
    assert rounds[1]["draft"] == final


# --- reviewer replies that aren't clean JSON ---


def test_malformed_reviewer_json_counts_as_revise_and_does_not_crash():
    final, rounds, _, _ = run_reflect(
        SHIPS_FRIDAY, [reply(text("Looks fine to me!")), verdict("confirmed")],
        [reply(text(CLEAN))],
    )  # fmt: skip

    assert rounds[0]["verdict"] == "revise"
    assert "JSON" in rounds[0]["issues"][0]
    assert final == CLEAN


def test_json_in_a_code_fence_is_accepted():
    fenced = '```json\n{"verdict": "confirmed", "issues": []}\n```'

    final, rounds, _, _ = run_reflect(CLEAN, [reply(text(fenced))])

    assert (final, rounds[0]["verdict"]) == (CLEAN, "confirmed")


def test_unknown_verdict_counts_as_revise():
    _, rounds, _, _ = run_reflect(
        CLEAN, [verdict("maybe"), verdict("confirmed")], [reply(text(CLEAN))]
    )

    assert rounds[0]["verdict"] == "revise"
    assert "maybe" in rounds[0]["issues"][0]


# --- the reviewer prompt stays short and on point ---


def test_reviewer_prompt_is_short_and_names_what_to_check():
    lowered = REVIEWER_PROMPT.lower()
    assert len(REVIEWER_PROMPT.split()) <= 150
    for check in ("dates", "models", "shipping", "json", "confirmed", "revise"):
        assert check in lowered, check


# --- wired into the agent loop ---

DEMO_1 = {"request_id": "REQ-0001", "employee_id": "E1001",
          "message": "Need a second holo-display for firmware debugging."}  # fmt: skip


def approve_then(draft_suffix, *extra):
    """The approve path, submitting a draft that quotes the real decision_id."""

    def submit(messages):
        decision_id = json.loads(messages[-1]["content"][0]["content"])["decision_id"]
        return reply(text("Thought: submit."), tool_use("submit_decision", {
            "decision": "approve", "reason_code": None, "item": "holo_display",
            "request_type": "additional",
            "draft_response": f"Approved ({decision_id}).{draft_suffix}",
        }))  # fmt: skip

    return ScriptedLLM([
        reply(text("Thought: check."),
              tool_use("check_request_eligibility",
                       {"employee_id": "E1001", "item": "holo_display"})),
        reply(text("Thought: record."),
              tool_use("record_decision", {"employee_id": "E1001", "item": "holo_display",
                                           "decision": "approve", "request_type": "additional",
                                           "reason": "Within the cap of 2"})),
        submit,
        *extra,
    ])  # fmt: skip


async def test_run_agent_reflects_before_returning(data_dir):
    def rewrite(messages):
        draft = json.loads(messages[0]["content"])["draft"]
        return reply(text(draft.replace(" It ships Friday.", "")))

    llm = approve_then(" It ships Friday.", rewrite)
    reviewer = ScriptedLLM([verdict("revise", "Promises a ship date."), verdict("confirmed")])

    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_agent(llm, session, DEMO_1, reviewer=reviewer)

    assert [r["verdict"] for r in result.reflection] == ["revise", "confirmed"]
    assert result.response_text == f"Approved ({result.decision_id})."
    assert "Friday" in result.reflection[0]["draft"]
    md = trace_to_markdown(result)
    assert "## Reflection" in md and "Promises a ship date." in md


async def test_no_reviewer_means_no_reflection(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_agent(approve_then(""), session, DEMO_1)

    assert result.reflection == []
    assert "## Reflection" not in trace_to_markdown(result)


async def test_the_agents_own_escalation_is_not_reflected(data_dir):
    # A templated fallback reply (step cap) was not drafted by the model: nothing to review.
    lookup = {"role": "netrunner"}
    looping = [reply(text("Thought: again."), tool_use("get_policy_limits", lookup))
               for _ in range(MAX_STEPS)]  # fmt: skip
    reviewer = ScriptedLLM([])  # would raise if called

    async with McpSession.stdio(data_dir=data_dir) as session:
        result = await run_agent(ScriptedLLM(looping), session, DEMO_1, reviewer=reviewer)

    assert result.stopped_by_cap is True
    assert result.reflection == []
    assert reviewer.calls == []
