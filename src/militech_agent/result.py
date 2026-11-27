"""What an agent run produces (requirements §8): the decision, the ids, and the full trace."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentResult:
    """One request's outcome. `trace` holds {step, thought, action, args, observation} entries."""

    request_id: str
    employee_id: str
    message: str
    decision: str
    reason_code: str | None
    item: str | None
    request_type: str | None
    response_text: str
    ticket_id: str | None = None
    decision_id: str | None = None
    reflection: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, str]] = field(default_factory=list)  # guardrail, if it gave up
    trace: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    stopped_by_cap: bool = False


def _or_dash(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def trace_to_markdown(result: AgentResult) -> str:
    """A readable Thought -> Action -> Observation record of the run."""
    lines = [
        f"# Trace {result.request_id}",
        "",
        f"- **Employee:** {result.employee_id}",
        f"- **Request:** {result.message}",
        f"- **Steps:** {result.steps}" + (" (step cap reached)" if result.stopped_by_cap else ""),
        "",
    ]
    for entry in result.trace:
        lines += [
            f"## Step {entry['step']}",
            "",
            f"**Thought:** {entry['thought']}",
            "",
            f"**Action:** `{entry['action']}` `{json.dumps(entry['args'])}`",
            "",
            "**Observation:**",
            "",
            "```json",
            json.dumps(entry["observation"], indent=2),
            "```",
            "",
        ]
    if result.violations:
        lines += [
            "## Guardrail",
            "",
            "The decision failed the guardrail twice and was escalated:",
            "",
        ]
        lines += [f"- `{v['code']}`: {v['message']}" for v in result.violations]
        lines.append("")
    if result.reflection:
        lines += ["## Reflection", ""]
        for review in result.reflection:
            issues = "; ".join(review["issues"]) or "no issues"
            lines += [f"**Round {review['round']}: {review['verdict']}** ({issues})", ""]
            lines += [f"> {line}" for line in review["draft"].splitlines() or [""]]
            lines.append("")
    lines += [
        "## Decision",
        "",
        f"- **Decision:** {result.decision}",
        f"- **Reason code:** {_or_dash(result.reason_code)}",
        f"- **Item:** {_or_dash(result.item)}",
        f"- **Request type:** {_or_dash(result.request_type)}",
        f"- **Decision ID:** {_or_dash(result.decision_id)}",
        f"- **Ticket ID:** {_or_dash(result.ticket_id)}",
        "",
        "**Response:**",
        "",
        *[f"> {line}" for line in result.response_text.splitlines() or [""]],
        "",
    ]
    return "\n".join(lines)


def write_trace(result: AgentResult, traces_dir: Path) -> Path:
    """Write traces_dir/<request_id>.md and return its path."""
    traces_dir = Path(traces_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{result.request_id}.md"
    path.write_text(trace_to_markdown(result), encoding="utf-8")
    return path
