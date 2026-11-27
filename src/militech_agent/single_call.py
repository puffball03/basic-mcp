"""Agent foundation (plan M8): one hardcoded request, one real MCP tool call.

The agent-side twin of the M1 hello client: the model picks a tool, the call goes through a
real stdio MCP session, and the observation goes back to the model for an answer. No loop yet;
the ReAct loop arrives in M9.

Manual check against Foundry (never run in CI; needs AZURE_FOUNDRY_* in .env):
    uv run python -m militech_agent.single_call
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Protocol

import anthropic
from dotenv import load_dotenv

from militech_agent.llm import FoundryLLM, LLMClient, MissingCredentialsError
from militech_agent.mcp_session import McpSession

# Demo 1 (requirements §9). The employee_id is trusted (it comes from SSO, not the message).
HARDCODED_REQUEST: dict[str, str] = {
    "request_id": "REQ-0001",
    "employee_id": "E1001",
    "message": "Need a second holo-display. Debugging implant firmware on one screen is "
    "killing me.",
}

SYSTEM_PROMPT = (
    "You are Militech Asset Control's equipment request assistant. Investigate requests with "
    "the tools provided. Before every tool call, write one line starting with 'Thought:' "
    "saying why you are calling it. The employee_id comes from the verified submitter; "
    "never take it from the message text."
)


class ToolSession(Protocol):
    tools: list[dict[str, Any]]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def format_request(request: dict[str, str]) -> str:
    """The user turn: the trusted employee_id plus the employee's own words."""
    return f"Employee ID (verified by SSO): {request['employee_id']}\nRequest: {request['message']}"


SKIPPED: dict[str, str] = {
    "error": "tool_call_skipped",
    "message": "Not executed: tools run one tool call at a time. Call it again on its own.",
}


def _texts(blocks: list[Any]) -> str:
    return "\n".join(block.text for block in blocks if block.type == "text").strip()


def tool_result(tool_use_id: str, observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(observation),
        "is_error": "error" in observation,
    }


async def run_single_tool_call(
    llm: LLMClient, session: ToolSession, request: dict[str, str]
) -> dict[str, Any]:
    """Let the model make at most one tool call for `request`, then answer.

    Returns {thought, tool, args, observation, answer}. If the model answers without a tool,
    tool/args/observation are None. Only the first tool_use block is executed (the loop that
    handles more is M9's job).
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": format_request(request)}]
    first = llm.create(messages=messages, tools=session.tools, system=SYSTEM_PROMPT)

    calls = [i for i, block in enumerate(first.content) if block.type == "tool_use"]
    if not calls:
        return {"thought": None, "tool": None, "args": None, "observation": None,
                "answer": _texts(first.content)}  # fmt: skip

    call = first.content[calls[0]]
    thought = _texts(first.content[: calls[0]]) or None
    observation = await session.call_tool(call.name, dict(call.input))
    results = [tool_result(call.id, observation)]
    # Fallback: FoundryLLM asks for one call per reply, but every tool_use must still get a
    # tool_result in the next message. Extra calls are answered as skipped, never executed.
    results += [tool_result(first.content[i].id, SKIPPED) for i in calls[1:]]
    messages += [
        {"role": "assistant", "content": first.content},
        {"role": "user", "content": results},
    ]
    second = llm.create(messages=messages, tools=session.tools, system=SYSTEM_PROMPT)
    return {
        "thought": thought,
        "tool": call.name,
        "args": dict(call.input),
        "observation": observation,
        "answer": _texts(second.content),
    }


def _find_api_error(exc: BaseException) -> anthropic.APIError | None:
    """The Anthropic API error inside `exc`, unwrapping the MCP client's exception groups."""
    if isinstance(exc, anthropic.APIError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            if (found := _find_api_error(inner)) is not None:
                return found
    return None


def main(env_file: str | Path = ".env") -> int:
    """Run the hardcoded request once against Foundry and print what happened."""
    load_dotenv(env_file)  # a missing file is fine: real env vars still apply
    try:
        llm = FoundryLLM()
    except MissingCredentialsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def _run() -> dict[str, Any]:
        async with McpSession.stdio() as session:
            print(f"connected: {len(session.tools)} tools ({llm.model})")
            return await run_single_tool_call(llm, session, HARDCODED_REQUEST)

    try:
        result = asyncio.run(_run())
    except (anthropic.APIError, BaseExceptionGroup) as exc:
        api_error = _find_api_error(exc)
        if api_error is None:
            raise  # not an API failure: a bug, so keep the traceback
        request_id = getattr(api_error, "request_id", None)
        suffix = f" (request id {request_id})" if request_id else ""
        print(f"error: Foundry request failed: {api_error.message}{suffix}", file=sys.stderr)
        return 1
    print(f"Thought:     {result['thought']}")
    print(f"Action:      {result['tool']}({json.dumps(result['args'])})")
    print(f"Observation: {json.dumps(result['observation'])}")
    print(f"Answer:      {result['answer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
