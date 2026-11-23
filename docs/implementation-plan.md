# Militech Equipment Request Handler — Implementation Plan

Spec: [requirements.md](requirements.md). Every milestone is built **test-first** (red → green →
refactor). Pure functions come before the MCP wrapper, and the wrapper comes before the agent.
Never build on something that isn't confirmed working. Each milestone ends with a green CI run
and becomes one Linear issue.

## Stack & conventions

- Python ≥ 3.12 (CI pinned to 3.12), managed with `uv`. Dependencies: `mcp`, `anthropic`,
  `python-dotenv`. Dev dependencies: `pytest`, `pytest-asyncio`, `ruff`.
- Tools return plain dicts. Expected failures come back as `{"error": code, ...}`; exceptions
  are for bugs only.
- Every date-dependent function takes `as_of: date | None`. Every file-writing function takes an
  injectable path. Tests use pinned dates and `tmp_path`.
- Secrets live in `.env` (gitignored), with a committed `.env.example`:
  `AZURE_FOUNDRY_ENDPOINT`, `AZURE_FOUNDRY_API_KEY`, `MILITECH_MODEL=claude-sonnet-5-5`.
  CI never needs them.
- The data directory comes from `MILITECH_DATA_DIR` and defaults to `./data`.
- **Docker from day one** (requirements §10). Everything runs in one image, `militech-mcp`. CI
  builds the image and runs the checks inside it. `docker compose run --rm test` is the local
  equivalent, and running `uv` directly on your machine still works for fast iteration.

## Target layout

```
src/militech_equipment/        # MCP server package
  models.py        Role, Item, ReasonCode, Decision, EligibilityStatus enums; record TypedDicts
  repository.py    load JSONL → EmployeeRepository, PolicyRepository; append_jsonl()
  dates.py         add_months(), months_between()
  employees.py     get_employee_info()
  policies.py      get_policy_limits()
  eligibility.py   check_request_eligibility()
  escalation.py    flag_for_human_review()
  decisions.py     record_decision(), load_pending()
  server.py        MCPServer app (mcp 2.x): thin @server.tool() wrappers over the functions above
src/militech_agent/
  llm.py           LLMClient protocol, FoundryLLM (AnthropicFoundry), ScriptedLLM (tests)
  mcp_session.py   start stdio server, list tools → Claude tool schemas, call_tool()
  prompts.py       system prompt (rules from requirements §5–6), reviewer prompt
  react.py         ReAct loop, trace capture, step cap, submit_decision terminal tool
  guardrail.py     validate final decision vs observations
  reflection.py    reviewer loop (max 2 rounds)
  result.py        AgentResult, trace → markdown
  demo.py          runs the 6 scenarios from requirements §9
scripts/hello_server.py, scripts/hello_client.py   # milestone 1 proof
data/employees.jsonl, data/policies.jsonl           # seed (committed)
data/escalations.jsonl, data/decisions.jsonl         # runtime (gitignored)
tests/unit/  tests/integration/  tests/agent/
Dockerfile        # multi-stage: python:3.12-slim + uv, non-root user
.dockerignore
compose.yaml      # services: test, demo, server
docker/entrypoint.sh   # dispatches server | demo | test
.github/workflows/ci.yml
```

---

## M0 — Project scaffold, Docker & CI skeleton

- `uv init`, `pyproject.toml` with a src layout, ruff config, and a pytest config
  (`asyncio_mode = auto`). Commit `uv.lock`.
- `.gitignore`: `.env`, `data/escalations.jsonl`, `data/decisions.jsonl`, `traces/`.
- **Dockerfile** (multi-stage):
  - `python:3.12-slim` base, with `COPY --from=ghcr.io/astral-sh/uv` for the uv binary
  - dependency layer: copy `pyproject.toml` and `uv.lock`, then `uv sync --frozen --no-install-project`
  - app layer: copy the source and seed data, then `uv sync --frozen`
  - non-root `militech` user; `ENTRYPOINT ["docker/entrypoint.sh"]`, with `test` as the default command
- `docker/entrypoint.sh`: `server` → `python -m militech_equipment.server`, `demo` →
  `python -m militech_agent.demo`, `test` → `ruff check . && pytest`. Anything else runs as a
  plain command.
- `.dockerignore`: `.env`, `.git`, `.venv`, `traces/`, runtime JSONL files, `MCP-directions.pdf`.
- `compose.yaml`:
  - `test` service: no secrets
  - `demo` service: `env_file: .env`, with `./data` runtime files and `./traces` mounted
  - `server` service: `stdin_open: true`
- `.github/workflows/ci.yml`: checkout → `docker build -t militech-mcp .` → `docker run --rm
  militech-mcp test`. Use the buildx GitHub Actions cache to speed up builds.
- **Red/green:** `tests/test_smoke.py` imports the package and asserts the version. Write it
  first, watch the container run fail, then make it pass.

**Done when** `docker compose run --rm test` passes locally and CI is green on a push.

## M1 — Minimal one-tool MCP server + client (deliverable #2)

- `scripts/hello_server.py`: `MCPServer` (mcp 2.x, formerly FastMCP) with a single `ping(name) -> "pong, {name}"` tool over stdio.
- `scripts/hello_client.py`: `mcp.Client(StdioServerParameters(...))`. It lists the tools, calls `ping`,
  and prints the result.
- **Test:** `tests/integration/test_hello.py` starts the server via the stdio client, asserts
  that `ping` is registered, and asserts the response text.

**Done when** the terminal output shows the tool list and `pong`, both on your machine and inside
the container (`docker compose run --rm test python scripts/hello_client.py`). Screenshot it
for deliverable #2.

## M2 — Seed data, repository, `get_employee_info`

Tests are written first in `tests/unit/test_repository.py` and `test_employees.py`.

- The loader parses `employees.jsonl` and `policies.jsonl`. A malformed line raises with the line number.
- `add_months` / `months_between`: normal dates, end of month (Jan 31 + 1 month → Feb 28/29), and leap years.
- `get_employee_info("E1001", as_of=…)` returns role, handle, `tenure_months` and equipment.
- Unknown ID → `{"error": "employee_not_found", "employee_id": "E9999"}`.
- Malformed ID (`""`, `"1001"`, `None`-ish) → `{"error": "invalid_employee_id"}`.
- Employee with no equipment → `equipment: []`.

Seed: 8 employees with cyberpunk names and handles, built to support the 6 demos plus edge cases.
One record has a future `issued_date` (for `DATA_CONFLICT`) and one has an unknown role.
Then wrap the function as an MCP tool and call it from a client script (Suggested Approach step 3).

**Done when** the unit tests and the integration call both pass.

## M3 — `get_policy_limits`

Tests come first:
- Each of the 4 roles returns all 6 items with values exactly matching the table in requirements §4.1.
- `netrunner.cyberdeck == {max_quantity: 1, refresh_months: 18}`, and `corpo_staff.cyberdeck.max_quantity == 0`.
- Unknown role → `{"error": "unknown_role"}`. Role lookup is case-normalized (`"Netrunner"` works).

Wrap the function, then add an integration call.

## M4 — `check_request_eligibility`

Table-driven tests (`pytest.mark.parametrize`) with a pinned `as_of=2026-11-23`. One case per status:
- `within_policy` as an additional unit: tech with 1/2 displays.
- `within_policy` as a replacement: executive terminal 24 months old vs an 18-month refresh.
- `refresh_not_due`: corpo terminal 14 months old. Assert the exact `next_eligible_date`.
- Boundary: a unit exactly `refresh_months` old is eligible, and one day younger is not.
- `not_entitled`: corpo cyberdeck.
- `off_catalog`: "combat drone".
- `employee_not_found`.
- `data_conflict`: future `issued_date`, unknown role, held count above the cap.
- `request_type_assumed` is reported correctly.
- Item normalization (`"Holo_Display"` works). Plain synonyms are *not* mapped here; the agent maps them.

Wrap the function, then add an integration call.

## M5 — `flag_for_human_review`

Tests come first, using `tmp_path`:
- Appends exactly one JSONL line with `ticket_id, employee_id, request, reason, reason_code,
  created_at, status="open"`.
- Ticket ID format is `ESC-YYYYMMDD-NNNN`, and the sequence increments within a day.
- Works for an unknown employee (`EMPLOYEE_NOT_FOUND` must be escalatable).
- Invalid `reason_code` → `{"error": "invalid_reason_code"}`, and nothing is written.
- Empty `request` or `reason` → error, and nothing is written.
- Creates the file if it's missing.

Wrap the function, then add an integration call that writes to a temporary data directory.

## M6 — `record_decision` + pending approvals

Tests come first:
- Appends `decision_id (DEC-YYYYMMDD-NNNN), employee_id, item, decision, request_type, reason,
  created_at`.
- `approve` when eligibility is false → `{"error": "approval_not_permitted", "status": ...}`, and nothing is written.
- `deny` always records, as long as the inputs are valid.
- Invalid decision, item or request_type → error.
- **Pending approvals feed eligibility:** after approving a 2nd holo-display for the tech, a 3rd
  request returns the replacement reading with `refresh_not_due`. After an approved terminal
  replacement, an immediate repeat request returns `refresh_not_due`.

Wrap the function, then add an integration call.

## M7 — MCP integration suite

`tests/integration/test_server.py` starts `militech_equipment.server` over stdio with
`MILITECH_DATA_DIR` set to a temporary copy of the seed data. It then:
- asserts that all 5 tools are listed with their parameter schemas
- calls each tool through the real client and checks the decoded JSON
- checks that the error dicts survive the protocol round trip

**Done when** CI runs unit and integration tests green. Screenshot server code and passing tests for deliverables #3 and #4.

## M8 — Agent foundation: LLM client + one hardcoded tool call

- `LLMClient` protocol with `create(messages, tools, system) -> response`.
- `FoundryLLM` wraps `AnthropicFoundry(api_key, base_url)` using the env vars.
- `ScriptedLLM` replays canned responses (text and tool_use blocks) for tests.
- `mcp_session.py` converts MCP tool listings into Claude tool definitions and executes calls.
- **Tests:** schema conversion; the `ScriptedLLM` replays in order; a hardcoded request makes
  exactly one `get_employee_info` call through a real stdio session and returns the observation.
- **Manual:** run once against Foundry to confirm credentials.

## M9 — ReAct loop + trace

- Loop: send messages and tools → for each `tool_use`, capture the preceding `Thought:` text →
  call the MCP tool → append the `tool_result` → repeat until `submit_decision` or the step cap.
- System prompt encodes requirements §4–§6: synonym mapping, deny vs escalate,
  reason codes, the explicit replacement and additional rules, and Thought-before-every-action.
- Trace entries: `{step, thought, action, args, observation}`. Printed live and written to
  `traces/<request_id>.md`.

Tests (scripted LLM):
- A full approve path produces T/A/O entries in order.
- A missing `Thought:` before a tool call → the agent re-prompts once, then records `thought="(none)"`.
- Hitting the 8-step cap → the agent calls `flag_for_human_review(UNCLEAR_REQUEST)` itself and returns escalate.
- An MCP error observation is passed to the model and doesn't crash the loop.
- `submit_decision` with an invalid enum → re-prompts.

## M10 — Guardrail

`guardrail.validate(decision, observations) -> list[Violation]`. Table-driven tests:
- approve with no successful `record_decision(approve)` → violation
- escalate with no `ticket_id` → violation
- deny with an escalation-only reason code → violation
- draft missing its `ticket_id` / `decision_id` → violation
- a valid decision of each type → no violations
- the below-cap explicit replacement case: approve when the oldest unit is younger than the refresh → violation. The guardrail recomputes the age from the `get_employee_info` and `get_policy_limits` observations.

On a violation, the loop feeds it back to the model for one correction. If it still fails, the
agent escalates with `UNCLEAR_REQUEST` and the violation recorded.

## M11 — Reflection

- Reviewer prompt receives the draft, the decision and the raw observations. It returns
  `{verdict, issues[]}` as JSON. On `revise`, the agent rewrites the draft (text only; the
  decision is already recorded) and the reviewer runs again. At most 2 rounds, then it ships with
  the issues noted.

Tests (scripted):
- The draft "your terminal will ship Friday" → `revise` → the rewritten draft is `confirmed`, and both rounds appear in `AgentResult.reflection`.
- A clean draft is `confirmed` in round 1.
- Malformed reviewer JSON → treated as `revise` with a parse issue, and doesn't crash.

**Done when** there's evidence of reflection catching a draft (deliverable #8).

## M12 — Demo run & deliverables

- `docker compose run --rm demo` runs the 6 scenarios from requirements §9 against Foundry on a
  fresh copy of the seed data. It prints traces and final results and writes `traces/*.md` to the
  mounted folder on your machine. `uv run python -m militech_agent.demo` still works outside
  Docker.
- `docker run -i militech-mcp server` serves the tools to an MCP client on your machine. Document
  this in the README.
- `tests/agent/test_scenarios.py` replays all 6 with `ScriptedLLM` and asserts the expected
  decision and reason code. This keeps the demos in CI without a key.
- README: setup, `.env`, how to run the server, the client, the tests and the demo.
- Assemble the PDF in the deliverable order (requirements → hello run → server code → tests +
  output → agent code → traces → decisions → reflection → green CI run).

## Rubric traceability

| Rubric item | Milestone |
|---|---|
| Requirements clear and testable (8) | requirements.md |
| Minimal server/client first (7) | M1 |
| Four tools correct (12) | M2–M6, M7 |
| Unit tests: normal + edge cases (13) | M2–M6 |
| Agent connects + ReAct (15) | M8, M9 |
| Clear-cut approve/deny (10) | M9, M10, M12 |
| Escalates with reasons (15) | M5, M9, M12 |
| Reflection improves/confirms (10) | M11 |
| Pipeline green (10) | M0 (containerized CI), all |
