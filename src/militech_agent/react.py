"""The ReAct loop (requirements §8, plan M9).

Each step is one model reply. A reply's tool call is run only if a Thought (text) comes first;
the first miss is answered with a request to add one, a second miss in a row runs the call
with thought "(none)". The loop ends when the model calls the local submit_decision tool with
valid input, or after MAX_STEPS replies, when the agent escalates with UNCLEAR_REQUEST itself.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from militech_agent.llm import LLMClient
from militech_agent.prompts import DENY_CODES, SYSTEM_PROMPT
from militech_agent.result import AgentResult, write_trace
from militech_agent.single_call import SKIPPED, ToolSession, format_request, tool_result
from militech_equipment.escalations import REASON_CODES

MAX_STEPS = 8
DECISIONS = ("approve", "deny", "escalate")
REQUEST_TYPES = ("additional", "replacement")

SUBMIT_DECISION_TOOL: dict[str, Any] = {
    "name": "submit_decision",
    "description": (
        "Finish the request. Call exactly once, after record_decision (approve/deny) or "
        "flag_for_human_review (escalate) has succeeded."
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
) -> AgentResult:
    """Investigate `request` ({request_id, employee_id, message}) and reach a decision."""
    tools = [*session.tools, SUBMIT_DECISION_TOOL]
    messages: list[dict[str, Any]] = [{"role": "user", "content": format_request(request)}]
    trace: list[dict[str, Any]] = []
    ids: dict[str, str | None] = {"ticket_id": None, "decision_id": None}
    reprompted = False

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
                if not problems:
                    record(step, thought, call.name, args, {"status": "submitted"})
                    return finish(
                        decision=args["decision"],
                        reason_code=args.get("reason_code") or None,
                        item=args.get("item"),
                        request_type=args.get("request_type") or None,
                        response_text=args["draft_response"].strip(),
                        steps=step,
                    )
                observation = {"error": "invalid_submission", "message": "; ".join(problems)}
            else:
                observation = await session.call_tool(call.name, args)
                for key in ids:
                    if key in observation:
                        ids[key] = observation[key]

        record(step, thought or "(none)", call.name, args, observation)
        results = [tool_result(call.id, observation)]
        # Fallback: one call per reply is requested, but every tool_use needs a tool_result.
        results += [tool_result(response.content[i].id, SKIPPED) for i in calls[1:]]
        messages.append({"role": "user", "content": results})

    # Step cap: escalate rather than guess.
    flag_args = {
        "employee_id": request["employee_id"],
        "request": request["message"],
        "reason": f"The agent reached its {max_steps}-step limit without a decision.",
        "reason_code": "UNCLEAR_REQUEST",
    }
    observation = await session.call_tool("flag_for_human_review", flag_args)
    ids["ticket_id"] = observation.get("ticket_id")
    record(max_steps + 1, f"(step cap of {max_steps} reached: escalating)",
           "flag_for_human_review", flag_args, observation)  # fmt: skip
    ticket = ids["ticket_id"] or "pending"
    return finish(
        decision="escalate",
        reason_code="UNCLEAR_REQUEST",
        item=None,
        request_type=None,
        response_text=(
            f"Your request {request['request_id']} needs a closer look, so it has been passed "
            f"to a Militech Asset Control reviewer (ticket {ticket}). You'll hear back once a "
            "human has reviewed it."
        ),
        steps=max_steps,
        stopped_by_cap=True,
    )
