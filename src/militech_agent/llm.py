"""The agent's LLM layer (plan M8).

LLMClient is the one method the agent needs. FoundryLLM is Claude on Azure AI Foundry, for real
runs; ScriptedLLM replays canned responses so tests never call a model or need a key.
"""

import copy
import itertools
import os
from collections.abc import Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from anthropic import AnthropicFoundry
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

DEFAULT_MODEL = "claude-sonnet-5-5"
ENDPOINT_VAR = "AZURE_FOUNDRY_ENDPOINT"
API_KEY_VAR = "AZURE_FOUNDRY_API_KEY"
MODEL_VAR = "MILITECH_MODEL"

# At most one tool call per reply: side-effecting tools (record_decision,
# flag_for_human_review) run in order, one Thought -> Action -> Observation per step.
# "auto" leaves the model free to answer without a tool; forced "any"/"tool" are
# rejected by claude-sonnet-5-5.
ONE_TOOL_AT_A_TIME: dict[str, Any] = {"type": "auto", "disable_parallel_tool_use": True}


@runtime_checkable
class LLMClient(Protocol):
    """Anything that answers one Messages API request with an SDK `Message`."""

    def create(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> Message: ...


class MissingCredentialsError(ValueError):
    """Foundry credentials aren't configured. The message names variables, never values."""


class FoundryLLM:
    """Claude on Azure AI Foundry via `AnthropicFoundry`.

    Credentials come from the arguments or the environment (AZURE_FOUNDRY_ENDPOINT,
    AZURE_FOUNDRY_API_KEY, optional MILITECH_MODEL). This class doesn't read `.env`; the entry
    point that runs the agent loads it, so tests can't pick up a real key by accident.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        max_tokens: int = 16000,
        client: Any | None = None,
    ) -> None:
        endpoint = endpoint or os.environ.get(ENDPOINT_VAR)
        api_key = api_key or os.environ.get(API_KEY_VAR)
        missing = [name for name, value in ((ENDPOINT_VAR, endpoint), (API_KEY_VAR, api_key))
                   if not value]  # fmt: skip
        if missing:
            raise MissingCredentialsError(
                f"Missing {', '.join(missing)}. Set them in the environment or in .env "
                "(see .env.example)."
            )
        self.model = model or os.environ.get(MODEL_VAR) or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.client = client or AnthropicFoundry(api_key=api_key, base_url=endpoint)

    def create(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> Message:
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=ONE_TOOL_AT_A_TIME,
        )


# --- scripted responses for tests ---

_ids = itertools.count(1)


def text(value: str) -> TextBlock:
    """A text content block, e.g. text("Thought: ...")."""
    return TextBlock(type="text", text=value)


def tool_use(name: str, arguments: dict[str, Any], *, id: str | None = None) -> ToolUseBlock:
    """A tool_use content block with a unique ID."""
    return ToolUseBlock(
        type="tool_use", id=id or f"toolu_scripted_{next(_ids):04d}", name=name, input=arguments
    )


def reply(*blocks: TextBlock | ToolUseBlock, stop_reason: str | None = None) -> Message:
    """An assistant Message. stop_reason defaults to tool_use if any block is a tool call."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return Message(
        id=f"msg_scripted_{next(_ids):04d}",
        type="message",
        role="assistant",
        model="scripted",
        content=list(blocks),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=0, output_tokens=0),
    )


class ScriptExhaustedError(AssertionError):
    """The code under test asked the ScriptedLLM for more responses than were scripted."""


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class ScriptedLLM:
    """Replays `responses` in order and records a snapshot of every request in `calls`."""

    def __init__(self, responses: Iterable[Message]) -> None:
        self.responses: Sequence[Message] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> Message:
        self.calls.append(copy.deepcopy({"messages": messages, "tools": tools, "system": system}))
        n = len(self.calls)
        if n > len(self.responses):
            raise ScriptExhaustedError(
                f"ScriptedLLM has no response for the {_ordinal(n)} call "
                f"({len(self.responses)} scripted)"
            )
        return self.responses[n - 1]
