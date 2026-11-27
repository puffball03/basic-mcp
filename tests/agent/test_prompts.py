"""What the agent is told, and where (requirements §4–§6, §8; plan M9).

The system prompt stays short and covers only how to work. Each decision rule lives in the
description of the tool where the model applies it, so it is read right before that decision
(and any MCP client gets it from the tool listing). The combined test checks that no rule is
lost when one moves.
"""

import pytest

from militech_agent.mcp_session import McpSession
from militech_agent.prompts import DENY_CODES, SYSTEM_PROMPT
from militech_agent.react import SUBMIT_DECISION_TOOL
from militech_equipment.catalog import CATALOG
from militech_equipment.escalations import REASON_CODES

SYSTEM_PROMPT_WORD_LIMIT = 150


def test_deny_codes_match_requirements():
    assert DENY_CODES == ("NOT_ENTITLED", "REFRESH_NOT_DUE", "QUANTITY_CAP_REACHED")


# --- the system prompt: short, and only how to work ---


def test_system_prompt_is_short():
    assert len(SYSTEM_PROMPT.split()) <= SYSTEM_PROMPT_WORD_LIMIT


@pytest.mark.parametrize(
    "rule",
    [
        "Thought:",  # a Thought line before every tool call
        "one tool call at a time",
        "never take it from the message",  # employee_id is trusted from SSO (§1)
        "submit_decision",  # the only way to finish
        "escalate",  # when unclear, escalate rather than guess
    ],
)
def test_system_prompt_states_how_to_work(rule):
    assert rule in SYSTEM_PROMPT


@pytest.mark.parametrize("code", [*REASON_CODES, *DENY_CODES])
def test_system_prompt_leaves_reason_codes_to_the_tools(code):
    assert code not in SYSTEM_PROMPT


# --- submit_decision explains its own fields ---


@pytest.mark.parametrize("code", [*REASON_CODES, *DENY_CODES])
def test_submit_decision_lists_which_codes_fit_which_decision(code):
    assert code in SUBMIT_DECISION_TOOL["description"]


def test_submit_decision_asks_for_the_id_to_be_quoted():
    description = SUBMIT_DECISION_TOOL["description"]
    assert "decision_id" in description and "ticket_id" in description


# --- every rule reaches the model somewhere: system prompt or a tool listing ---

EVERY_RULE = [
    *REASON_CODES,
    *DENY_CODES,
    *CATALOG,
    "Thought:",
    "one tool call at a time",
    "submit_decision",
    '"laptop"',  # plain words map to catalog items (§3)
    "damaged",  # exception reasons turn a no into an escalation (§6)
    "lost",
    "stolen",
    "medical",
    "not an exception",  # slow / newer model is a deny, not an escalation (§5)
    "another unit",  # explicit additional at the cap -> QUANTITY_CAP_REACHED (§4.2)
    "below the cap",  # explicit replacement below the cap -> compare oldest unit (§4.2)
]


async def test_every_rule_reaches_the_model(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        descriptions = [tool["description"] for tool in session.tools]

    everything = "\n".join([SYSTEM_PROMPT, *descriptions, SUBMIT_DECISION_TOOL["description"]])
    missing = [rule for rule in EVERY_RULE if rule not in everything]
    assert missing == []
