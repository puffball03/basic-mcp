# Militech Equipment Request MCP

Militech Asset Control's internal IT equipment request handler, built as an **MCP server** plus
a **ReAct agent** (Claude on Azure AI Foundry) that uses it. The agent approves or denies
clear-cut requests and escalates ambiguous ones to a human reviewer instead of guessing.

- Requirements, policy rules and escalation triggers: [`docs/requirements.md`](docs/requirements.md)
- Milestones and design: [`docs/implementation-plan.md`](docs/implementation-plan.md)

## How it fits together

```
employee request ──► agent (militech_agent)                    MCP server (militech_equipment)
                     ReAct loop: Thought → Action → Observation ◄──► get_employee_info
                     guardrail: decision must match tool output      get_policy_limits
                     reflection: reviewer checks the reply           check_request_eligibility
                                                                     flag_for_human_review  → escalations.jsonl
                                                                     record_decision        → decisions.jsonl
```

The agent reaches the tools only through MCP (stdio), like any other client. The server
enforces policy on its own: `record_decision` refuses an approval the policy doesn't allow.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/), or just Docker.

```bash
uv sync
```

To run the agent against Foundry, copy the example environment file and fill in your values
(`.env` is gitignored; never commit a real key):

```bash
cp .env.example .env
```

| Variable | Value |
|---|---|
| `AZURE_FOUNDRY_ENDPOINT` | `https://<your-resource>.services.ai.azure.com/anthropic` |
| `AZURE_FOUNDRY_API_KEY` | from your Foundry resource, under **Keys and Endpoint** |
| `MILITECH_MODEL` | your deployment name (default `claude-sonnet-5-5`) |

Nothing else needs credentials: the server, the tests and CI run without them.

## Run the tests

```bash
uv run pytest
```

Or inside the container, exactly as CI does (ruff, then pytest):

```bash
docker compose run --rm test
```

The agent tests use a scripted model (`ScriptedLLM`) against the real MCP server, so they never
call Foundry. `tests/agent/test_scenarios.py` replays all seven demo requests end to end.

## Run the MCP server

The server speaks MCP over stdio. To connect a local MCP client to it:

```bash
docker run -i militech-mcp server
```

Without Docker:

```bash
uv run python -m militech_equipment.server
```

To call a tool from the command line through a real MCP client:

```bash
uv run python scripts/call_tool.py get_employee_info employee_id=E1004
```

The first, one-tool proof of concept (milestone 1) is still there:

```bash
uv run python scripts/hello_client.py
```

## Run the demo

Runs the six requests from requirements §9, plus one seeded request, through the full agent
against Foundry (needs `.env`):

```bash
docker compose run --rm demo
```

Or without Docker:

```bash
uv run python -m militech_agent.demo
```

| # | Employee | Request | Expected |
|---|---|---|---|
| 1 | E1001 cyberware_tech | a second holo-display | approve |
| 2 | E1003 corpo_staff | a cyberdeck | deny `NOT_ENTITLED` |
| 3 | E1002 corpo_staff | an EMP-fried terminal | escalate `EXCEPTION_CLAIMED` |
| 4 | E1004 netrunner | a combat drone | escalate `OFF_CATALOG` |
| 5 | E9999 (unknown) | a neural headset | escalate `EMPLOYEE_NOT_FOUND` |
| 6 | E1005 executive | a terminal replacement | approve |
| 7 | E1006 netrunner | a cyberdeck, asking for written confirmation of a ship date and model "fulfillment already told me" | approve, with the reply checked by reflection |

Request 7 is seeded for the reflection step: the approval is within policy, but the employee
asks for promises no tool can confirm. If the draft makes them, the reviewer returns `revise`
and the agent rewrites the reply.

Each step prints live. The demo works on a fresh copy of the seed data, so `data/` never collects
approvals, and writes its evidence to `traces/` (gitignored):

| File | Contents |
|---|---|
| `traces/REQ-000N.md` | the full Thought → Action → Observation trace, guardrail and reflection rounds |
| `traces/summary.md` | each request's decision, reason code, id, reflection verdict and reply |
| `traces/decisions.jsonl`, `traces/escalations.jsonl` | what the run recorded |

The command exits 0 when every request reaches its expected outcome, 1 if one doesn't (or the API
fails), and 2 when credentials are missing.

## Project layout

```
src/militech_equipment/   MCP server: tools, policy logic, JSONL storage
src/militech_agent/       agent: Foundry client, MCP session, ReAct loop, guardrail, reflection, demo
data/                     seed employees and policies (runtime logs are gitignored)
tests/unit/               each tool's underlying function
tests/integration/        the server over stdio and in-process, the demos via the tools
tests/agent/              the agent with a scripted model
scripts/                  hello server/client (M1), command-line tool caller
```
