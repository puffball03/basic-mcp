"""Reflection (requirements §8, plan M11): review the draft before it reaches the employee.

A reviewer (a separate model call with no tools) checks the draft against the recorded
decision and the raw tool observations and answers {"verdict", "issues"} as JSON. On revise,
the agent's model rewrites the text only; the decision is already recorded and doesn't change.
At most `max_rounds` reviews (one rewrite between each); a draft still marked revise after the
last round ships with its issues noted. A rewrite can never drop the decision or ticket id.
"""

import json
import re
from typing import Any

from militech_agent.llm import LLMClient

MAX_ROUNDS = 2
VERDICTS = ("confirmed", "revise")

REVIEWER_PROMPT = """\
You review a reply that Militech Asset Control's equipment request agent drafted for an \
employee, before it is sent. You get the draft, the recorded decision, and the raw tool \
observations. Check two things:
1. Accuracy: every fact in the draft (decision, item, reasons, dates, ids) matches the \
decision and the observations.
2. No unconfirmed promises: no dates, models, delivery or shipping that the observations \
don't confirm.
Answer with JSON only: {"verdict": "confirmed" or "revise", "issues": ["..."]}. Use revise \
with one short issue per problem if either check fails; otherwise confirmed with no issues.\
"""

REWRITE_PROMPT = """\
Rewrite this reply to a Militech employee so it fixes the listed issues. The decision is final \
and already recorded: don't change it. Keep the decision_id or ticket_id, state only what the \
decision and observations support, and keep it short and professional with light Militech \
branding. Reply with the new text only.\
"""

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _text(response: Any) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _observations(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"action": e["action"], "args": e["args"], "observation": e["observation"]}
        for e in trace
        if e["action"] != "submit_decision"
    ]


def parse_verdict(raw: str) -> tuple[str, list[str]]:
    """The reviewer's (verdict, issues). Anything unusable counts as revise, with the reason."""
    candidate = raw.strip()
    if match := _FENCE.match(candidate):
        candidate = match.group(1)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        try:
            parsed = json.loads(candidate[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, dict):
        return "revise", [f"The reviewer's reply was not valid JSON: {raw[:120]!r}"]
    verdict = parsed.get("verdict")
    issues = [str(issue) for issue in parsed.get("issues") or []]
    if verdict not in VERDICTS:
        return "revise", [f"The reviewer gave an unknown verdict {verdict!r}.", *issues]
    return verdict, issues


def reflect(
    draft: str,
    facts: dict[str, Any],
    trace: list[dict[str, Any]],
    *,
    reviewer: LLMClient,
    writer: LLMClient,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[str, list[dict[str, Any]]]:
    """Review (and if needed rewrite) `draft`. Returns (final draft, rounds)."""
    observations = _observations(trace)
    required_id = facts.get("decision_id") or facts.get("ticket_id")
    rounds: list[dict[str, Any]] = []
    for number in range(1, max_rounds + 1):
        review = reviewer.create(
            messages=[{"role": "user", "content": json.dumps(
                {"draft": draft, "decision": facts, "observations": observations})}],
            tools=[],
            system=REVIEWER_PROMPT,
        )  # fmt: skip
        verdict, issues = parse_verdict(_text(review))
        rounds.append({"round": number, "draft": draft, "verdict": verdict, "issues": issues})
        if verdict == "confirmed" or number == max_rounds:
            break
        rewrite = writer.create(
            messages=[{"role": "user", "content": json.dumps(
                {"draft": draft, "issues": issues, "decision": facts,
                 "observations": observations})}],
            tools=[],
            system=REWRITE_PROMPT,
        )  # fmt: skip
        draft = _text(rewrite) or draft
        if required_id and required_id not in draft:
            draft = f"{draft} (Reference: {required_id})"
    return rounds[-1]["draft"], rounds
