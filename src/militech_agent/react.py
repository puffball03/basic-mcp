"""The ReAct loop (requirements §8, plan M9).

Each step is one model reply. A reply's tool call is run only if a Thought (text) comes first;
the first miss is answered with a request to add one, a second miss in a row runs the call
with thought "(none)". The loop ends when the model calls the local submit_decision tool with
valid input, or after MAX_STEPS replies, when the agent escalates with UNCLEAR_REQUEST itself.

A valid submission must also pass the guardrail (M10). A violation gets one correction; a
second ends in an UNCLEAR_REQUEST escalation. Each request gets one outcome: once a decision
is recorded or a ticket opened, further record_decision / flag_for_human_review calls are not
run, so the audit logs can't contradict each other.

With a `reviewer`, the submitted draft is reflected on (M11) before it is returned. Templated
replies from the agent's own escalations are not reviewed: no model wrote them.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from militech_agent.guardrail import Violation, validate
from militech_agent.llm import LLMClient
from militech_agent.prompts import DENY_CODES, SYSTEM_PROMPT
from militech_agent.reflection import reflect
from militech_agent.result import AgentResult, write_trace
from militech_agent.single_call import SKIPPED, ToolSession, format_request, tool_result
from militech_equipment import dates
from militech_equipment.escalations import REASON_CODES

MAX_STEPS = 8
DECISIONS = ("approve", "deny", "escalate")
REQUEST_TYPES = ("additional", "replacement")

SUBMIT_DECISION_TOOL: dict[str, Any] = {
    "name": "submit_decision",
    "description": (
        "Finish the request. Call exactly once, after record_decision (approve or deny) or "
        "flag_for_human_review (escalate) has succeeded.\n"
        "reason_code: null for approve; for deny, the code used in record_decision "
        f"({', '.join(DENY_CODES)}); for escalate, the code used in flag_for_human_review "
        f"({', '.join(REASON_CODES)}).\n"
        "request_type: additional or replacement (may be null when escalating).\n"
        "draft_response: a short, professional reply to the employee with light Militech "
        "branding that quotes the decision_id or ticket_id you received."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": list(DECISIONS)},
            "reason_code": {
                "type": ["string", "null"],
                "description": "null for approve; a deny code for deny; an escalation code "
                "for escalate.",
            },
            "item": {"type": ["string", "null"], "description": "The item requested."},
            "request_type": {"type": ["string", "null"], "enum": [*REQUEST_TYPES, None]},
            "draft_response": {
                "type": "string",
                "description": "The reply to the employee, quoting the decision or ticket id.",
            },
        },
        "required": ["decision", "reason_code", "item", "request_type", "draft_response"],
    },
}

MISSING_THOUGHT: dict[str, str] = {
    "error": "missing_thought",
    "message": "Not executed: write a line starting with 'Thought:' explaining this call, then "
    "make the call again.",
}
CONTINUE = "Continue: call one tool, or finish by calling submit_decision."
OUTCOME_TOOLS = ("record_decision", "flag_for_human_review")


def _violation_feedback(violations: list[Violation]) -> dict[str, Any]:
    return {
        "error": "guardrail_violation",
        "violations": [v.to_dict() for v in violations],
        "message": "The decision doesn't match the tool results. Fix these, then call "
        "submit_decision again.",
    }


def _already_recorded(outcome: dict[str, str]) -> dict[str, Any]:
    ((key, value),) = outcome.items()
    return {
        "error": "outcome_already_recorded",
        key: value,
        "message": f"This request already has an outcome ({value}). Don't record another; "
        f"call submit_decision now, quoting {value}.",
    }


def _thought_of(blocks: list[Any]) -> str | None:
    """The text before a tool call, without a leading 'Thought:' label; None if there's none."""
    joined = "\n".join(b.text for b in blocks if b.type == "text").strip()
    if joined.lower().startswith("thought:"):
        joined = joined[len("thought:") :].strip()
    return joined or None


def validate_submission(args: dict[str, Any]) -> list[str]:
    """Problems with a submit_decision call (empty list = valid)."""
    problems = []
    decision = args.get("decision")
    code = args.get("reason_code") or None
    request_type = args.get("request_type") or None
    if decision not in DECISIONS:
        problems.append(f"decision must be one of: {', '.join(DECISIONS)}")
    elif decision == "approve" and code is not None:
        problems.append("reason_code must be null when approving")
    elif decision == "deny" and code not in DENY_CODES:
        problems.append(f"deny needs a reason_code from: {', '.join(DENY_CODES)}")
    elif decision == "escalate" and code not in REASON_CODES:
        problems.append(f"escalate needs a reason_code from: {', '.join(REASON_CODES)}")
    if request_type is not None and request_type not in REQUEST_TYPES:
        problems.append(f"request_type must be one of: {', '.join(REQUEST_TYPES)}, or null")
    elif decision in ("approve", "deny") and request_type is None:
        problems.append("request_type is required when approving or denying")
    draft = args.get("draft_response")
    if not (isinstance(draft, str) and draft.strip()):
        problems.append("draft_response must be a non-empty reply to the employee")
    return problems


async def run_agent(
    llm: LLMClient,
    session: ToolSession,
    request: dict[str, str],
    *,
    max_steps: int = MAX_STEPS,
    traces_dir: Path | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    reviewer: LLMClient | None = None,
) -> AgentResult:
    """Investigate `request` ({request_id, employee_id, message}) and reach a decision."""
    tools = [*session.tools, SUBMIT_DECISION_TOOL]
    messages: list[dict[str, Any]] = [{"role": "user", "content": format_request(request)}]
    trace: list[dict[str, Any]] = []
    ids: dict[str, str | None] = {"ticket_id": None, "decision_id": None}
    reprompted = False
    corrected = False  # the guardrail gives one correction
    outcome: dict[str, str] = {}  # the decision_id or ticket_id once one is recorded

    def record(step: int, thought: str, action: str, args: dict, observation: dict) -> None:
        entry = {"step": step, "thought": thought, "action": action, "args": args,
                 "observation": observation}  # fmt: skip
        trace.append(entry)
        if on_step is not None:
            on_step(entry)

    def finish(**outcome: Any) -> AgentResult:
        result = AgentResult(
            request_id=request["request_id"],
            employee_id=request["employee_id"],
            message=request["message"],
            trace=trace,
            **ids,
            **outcome,
        )
        if traces_dir is not None:
            write_trace(result, traces_dir)
        return result

    async def escalate_unclear(step: int, thought: str, reason: str, **extra: Any) -> AgentResult:
        """The agent's own UNCLEAR_REQUEST escalation (step cap, or a repeated violation)."""
        if ids["decision_id"]:
            reason += (
                f" This conflicts with recorded decision {ids['decision_id']}; a reviewer must "
                "resolve it."
            )
        if ids["ticket_id"] is None:  # never open a second ticket for one request
            flag_args = {"employee_id": request["employee_id"], "request": request["message"],
                         "reason": reason, "reason_code": "UNCLEAR_REQUEST"}  # fmt: skip
            observation = await session.call_tool("flag_for_human_review", flag_args)
            ids["ticket_id"] = observation.get("ticket_id")
            record(step, thought, "flag_for_human_review", flag_args, observation)
        ticket = ids["ticket_id"] or "pending"
        return finish(
            decision="escalate",
            reason_code="UNCLEAR_REQUEST",
            item=None,
            request_type=None,
            response_text=(
                f"Your request {request['request_id']} needs a closer look, so it has been "
                f"passed to a Militech Asset Control reviewer (ticket {ticket}). You'll hear "
                "back once a human has reviewed it."
            ),
            **extra,
        )

    for step in range(1, max_steps + 1):
        response = llm.create(messages=messages, tools=tools, system=SYSTEM_PROMPT)
        messages.append({"role": "assistant", "content": response.content})
        calls = [i for i, block in enumerate(response.content) if block.type == "tool_use"]
        if not calls:
            messages.append({"role": "user", "content": CONTINUE})
            continue

        call = response.content[calls[0]]
        args = dict(call.input)
        thought = _thought_of(response.content[: calls[0]])
        if thought is None and not reprompted:
            reprompted = True
            observation = MISSING_THOUGHT
        else:
            reprompted = False
            thought = thought or "(none)"
            if call.name == SUBMIT_DECISION_TOOL["name"]:
                problems = validate_submission(args)
                violations = [] if problems else validate(args, trace, as_of=dates.today())
                if problems:
                    observation = {"error": "invalid_submission", "message": "; ".join(problems)}
                elif violations and corrected:
                    record(step, thought, call.name, args, _violation_feedback(violations))
                    codes = ", ".join(v.code for v in violations)
                    return await escalate_unclear(
                        step,
                        "(guardrail violated twice: escalating)",
                        f"The agent's decision failed the guardrail twice ({codes}).",
                        violations=[v.to_dict() for v in violations],
                        steps=step,
                    )
                elif violations:
                    corrected = True
                    observation = _violation_feedback(violations)
                else:
                    record(step, thought, call.name, args, {"status": "submitted"})
                    decided = {
                        "decision": args["decision"],
                        "reason_code": args.get("reason_code") or None,
                        "item": args.get("item"),
                        "request_type": args.get("request_type") or None,
                    }
                    draft, rounds = args["draft_response"].strip(), []
                    if reviewer is not None:  # reflection (M11): review the model's draft
                        draft, rounds = reflect(
                            draft, {**decided, **ids}, trace, reviewer=reviewer, writer=llm
                        )
                    return finish(**decided, response_text=draft, reflection=rounds, steps=step)
            elif call.name in OUTCOME_TOOLS and outcome:
                # One outcome per request: a second decision or ticket would contradict the
                # first in the audit logs, so it is not run.
                observation = _already_recorded(outcome)
            else:
                observation = await session.call_tool(call.name, args)
                for key in ids:
                    if key in observation:
                        ids[key] = observation[key]
                        if call.name in OUTCOME_TOOLS:
                            outcome = {key: observation[key]}

        record(step, thought or "(none)", call.name, args, observation)
        results = [tool_result(call.id, observation)]
        # Fallback: one call per reply is requested, but every tool_use needs a tool_result.
        results += [tool_result(response.content[i].id, SKIPPED) for i in calls[1:]]
        messages.append({"role": "user", "content": results})

    # Step cap: escalate rather than guess.
    return await escalate_unclear(
        max_steps + 1,
        f"(step cap of {max_steps} reached: escalating)",
        f"The agent reached its {max_steps}-step limit without a decision.",
        steps=max_steps,
        stopped_by_cap=True,
    )
