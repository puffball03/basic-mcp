# Militech Equipment Request Handler — Requirements & Design (Part 1)

Militech Asset Control handles internal equipment requests from employees. Requests are
investigated by an AI agent that calls tools on an MCP server. The agent approves or denies
clear-cut requests itself, and escalates anything ambiguous to a human reviewer instead of
guessing.

## 1. Request data

A request arrives as a structured envelope. The `employee_id` comes from the authenticated
submitter (SSO) and is trusted; it is never read from the message text.

| Field | Type | Source | Notes |
|---|---|---|---|
| `request_id` | string | system | Unique per request, e.g. `REQ-0001` |
| `employee_id` | string | envelope (trusted) | Format `E####` |
| `message` | string | envelope | Free text from the employee |
| `item` | catalog item \| off-catalog text | extracted by the agent | See §3 |
| `request_type` | `additional` \| `replacement` | extracted by the agent | See §4.2 |
| `reason` | string | extracted by the agent | Stated justification, may be empty |

Looked up through tools, never taken from the message: `role`, `hire_date` / tenure, and
current equipment (item and `issued_date` per unit).

## 2. Roles

| Role | Description |
|---|---|
| `corpo_staff` | Desk workers in the Militech arcology |
| `cyberware_tech` | Build and maintain implants and hardware |
| `executive` | Corporate executives |
| `netrunner` | Netspace operations and intelligence |

## 3. Catalog

Closed catalog of six items: `terminal`, `holo_display`, `haptic_keyboard`, `gesture_band`,
`neural_headset`, `cyberdeck`. Any other item is **off-catalog**. The agent maps plain-language
words to catalog items, e.g. "laptop" → `terminal` and "monitor" → `holo_display`.

## 4. Policy rules

### 4.1 Policy table

Each cell is **max quantity held at once / refresh interval in months**. `0` means the role is
never entitled to the item.

| Role | terminal | holo_display | haptic_keyboard | gesture_band | neural_headset | cyberdeck |
|---|---|---|---|---|---|---|
| corpo_staff | 1 / 36 | 1 / 36 | 1 / 24 | 1 / 24 | 1 / 24 | 0 |
| cyberware_tech | 1 / 24 | 2 / 36 | 1 / 12 | 1 / 12 | 1 / 24 | 0 |
| executive | 1 / 18 | 2 / 36 | 1 / 12 | 1 / 12 | 1 / 12 | 0 |
| netrunner | 1 / 24 | 3 / 24 | 2 / 12 | 1 / 12 | 1 / 12 | 1 / 18 |

### 4.2 Eligibility rule (`check_request_eligibility`)

Evaluated as of a date `as_of`. It defaults to today and is pinned in tests.

1. Unknown employee → `employee_not_found`.
2. Item not in catalog → `off_catalog`.
3. Unknown role, an `issued_date` after `as_of`, or more units held than the cap allows → `data_conflict`.
4. `max_quantity == 0` → `not_entitled`.
5. **Held count** = units on file + pending approved *additional* units from `decisions.jsonl`.
   An approved *replacement* resets the oldest unit's date to the decision date.
6. Held < max → `within_policy` (treated as an **additional** unit).
7. Held == max → treated as a **replacement of the oldest unit**:
   - the oldest unit is at least `refresh_months` old → `within_policy`
   - otherwise → `refresh_not_due`, with `next_eligible_date`

The tool reports `request_type_assumed` (`additional` or `replacement`) so the agent can see
which reading it applied.

**Explicit additional unit at the cap.** When the employee clearly asks for *another* unit
("a third display") while already at the cap, the agent denies with `QUANTITY_CAP_REACHED`,
or escalates if an exception reason applies. It does this even if the tool's replacement
reading came back `within_policy`.

**Explicit replacement below the cap.** The tool signature stays `(employee_id, item)`. When the
employee explicitly asks for a *replacement* while below the cap, the **agent** decides. It
compares the oldest unit's `issued_date` from `get_employee_info` with `refresh_months` from
`get_policy_limits`. If the oldest unit is old enough, the agent approves; otherwise it denies
with `REFRESH_NOT_DUE`, or escalates if an exception reason applies.

Month arithmetic: a unit issued on `2024-11-23` is 24 months old on `2026-11-23`. If the day
doesn't exist in the target month, it rolls forward to the last day of that month.

### 4.3 Tenure

Tenure in months is computed from `hire_date` and returned for information only. It does not
affect any decision.

## 5. Outcomes

| Outcome | When | Side effect |
|---|---|---|
| **Approve** | Eligibility is `within_policy`, and for an explicit replacement below the cap, the oldest unit is past its refresh | `record_decision(approve)` → `decision_id` |
| **Deny** | `not_entitled`; or `refresh_not_due` / `quantity_cap_reached` **with no exception reason** ("slow", "want the new model" and similar are not exceptions) | `record_decision(deny)` → `decision_id` |
| **Escalate** | Any trigger in §6 | `flag_for_human_review` → `ticket_id` |

The AI's approve and deny decisions are **final** and recorded. Deny reason codes:
`NOT_ENTITLED`, `REFRESH_NOT_DUE`, `QUANTITY_CAP_REACHED`.

## 6. Ambiguity — escalation triggers

| Reason code | Trigger |
|---|---|
| `OFF_CATALOG` | Requested item is not one of the six catalog items |
| `EXCEPTION_CLAIMED` | Policy says no (refresh not due or cap reached), but the stated reason is damage/broken, lost/stolen, or medical/accessibility |
| `UNCLEAR_REQUEST` | Item can't be determined, several items are bundled together, or the agent hits its 8-step limit |
| `EMPLOYEE_NOT_FOUND` | `employee_id` is not on file |
| `THIRD_PARTY_REQUEST` | Request is on behalf of someone else |
| `DATA_CONFLICT` | Records are inconsistent: unknown role, future `issued_date`, or held count above the cap |

## 7. Tools (MCP server)

Every tool returns a JSON object. Expected failures come back as
`{"error": "<code>", ...}` rather than raising.

| Tool | Returns | Side effect |
|---|---|---|
| `get_employee_info(employee_id)` | `employee_id, name, role, hire_date, tenure_months, equipment[{asset_id, item, issued_date}]` | none |
| `get_policy_limits(role)` | `role, limits{item: {max_quantity, refresh_months}}` | none |
| `check_request_eligibility(employee_id, item)` | `eligible, status, item, held, max_quantity, refresh_months, oldest_issued, next_eligible_date, request_type_assumed` | none |
| `flag_for_human_review(employee_id, request, reason, reason_code)` | `ticket_id, status: "open", created_at` | appends to `escalations.jsonl` |
| `record_decision(employee_id, item, decision, request_type, reason)` | `decision_id, created_at` | appends to `decisions.jsonl` |

`record_decision` refuses an `approve` (`error: approval_not_permitted`) unless eligibility
currently returns `eligible: true`. This guardrail is enforced on the server.

## 8. Agent behaviour

- **ReAct loop** using native Claude tool use (`claude-sonnet-5-5` via Azure Foundry). The
  agent must write a `Thought:` before every tool call. Each step is printed as
  Thought → Action → Observation and saved to `traces/<request_id>.md`.
- **Step cap:** 8 steps. Hitting the cap means escalating with `UNCLEAR_REQUEST`.
- **Finish:** the agent calls a local `submit_decision` tool with
  `{decision, reason_code, item, request_type, draft_response}`.
- **Guardrail (deterministic):** checks the final decision against the observations:
  - approve requires a successful `record_decision(approve)`
  - deny requires `record_decision(deny)` and a deny reason code
  - escalate requires a `ticket_id`
  - the draft must quote the `ticket_id` or `decision_id`
- **Reflection:** a separate reviewer LLM call checks two things:
  - does the draft accurately reflect the tool output?
  - does it avoid promising anything the data doesn't confirm (dates, models, shipping)?

  It returns `confirmed` or `revise` plus a list of issues. At most 2 rounds.
- **Tone:** professional, with light Militech branding.
- **Output:** an `AgentResult` with `request_id, employee_id, decision, reason_code, item,
  request_type, response_text, ticket_id | decision_id, reflection[], trace[]`.

## 9. Demo requests (acceptance scenarios)

| # | Employee | Message | Expected |
|---|---|---|---|
| 1 | cyberware_tech, 1 holo_display | "Need a second holo-display. Debugging implant firmware on one screen is killing me." | approve |
| 2 | corpo_staff | "Requesting a cyberdeck so I can jack in on my lunch break." | deny `NOT_ENTITLED` |
| 3 | corpo_staff, terminal 14 months old | "An EMP surge on the maglev fried my terminal. Won't boot. Need a replacement." | escalate `EXCEPTION_CLAIMED` |
| 4 | netrunner | "Requisitioning a military-grade combat drone for a run next week." | escalate `OFF_CATALOG` |
| 5 | E9999 (unknown) | "Need a neural headset, choom." | escalate `EMPLOYEE_NOT_FOUND` |
| 6 | executive, terminal 24 months old | "My terminal lags during board calls. Replace it." | approve (replacement past the 18-month refresh) |

## 10. Packaging & runtime (Docker)

The project is containerized from the first milestone.

- **One image** (`militech-mcp`) contains both the MCP server and the agent. The server talks
  over stdio, so the agent starts it as a child process inside the same container. The image
  has three commands:
  - `server`: runs the MCP server over stdio on its own. Any MCP client on your machine can
    connect to it with `docker run -i militech-mcp server`.
  - `demo`: runs the 6 scenarios in §9.
  - `test`: runs ruff and pytest.
- **Build:**
  - base image `python:3.12-slim`, with `uv` copied from `ghcr.io/astral-sh/uv`
  - dependencies installed from `uv.lock` with `uv sync --frozen`
  - multi-stage build with a dependency layer first, so rebuilds are cached
  - runs as a non-root user
- **`.dockerignore`** excludes `.env`, `.git`, `traces/`, runtime JSONL files
  (`escalations.jsonl`, `decisions.jsonl`), and the course brief PDF.
- **Secrets** are never baked into the image. Foundry credentials reach the container at runtime
  through `env_file: .env`.
- **Data:** seed JSONL files are copied into the image. The runtime files in `data/`
  (escalations and decisions) and `traces/` are mounted from your machine, so you can inspect
  results after a run.
- **docker compose** (`compose.yaml`) defines three services:

  | Service | Runs | Needs secrets |
  |---|---|---|
  | `test` | ruff + pytest | no |
  | `demo` | the 6 scenarios against Foundry | yes (`.env`) |
  | `server` | the MCP server over stdio | no |

- **CI** builds the image and runs ruff and pytest **inside the container** on every push. A
  green pipeline run therefore also proves the container works.
