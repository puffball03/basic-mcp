"""The six acceptance demos (requirements §9), replayed through the full agent (plan M12).

`run_demo` is what `docker compose run --rm demo` runs against Foundry. Here the model and the
reviewer are scripted, so CI covers the demo path end to end without a key: real MCP server,
ReAct loop, guardrail and reflection, on a fresh copy of the seed data.
"""

import json

from militech_agent import demo
from militech_agent.demo import SCENARIOS, mismatches, run_demo
from militech_agent.llm import ScriptedLLM, reply, text, tool_use

EXPECTED = {
    "REQ-0001": ("E1001", "approve", None),
    "REQ-0002": ("E1003", "deny", "NOT_ENTITLED"),
    "REQ-0003": ("E1002", "escalate", "EXCEPTION_CLAIMED"),
    "REQ-0004": ("E1004", "escalate", "OFF_CATALOG"),
    "REQ-0005": ("E9999", "escalate", "EMPLOYEE_NOT_FOUND"),
    "REQ-0006": ("E1005", "approve", None),
}


def test_scenarios_match_requirements_9():
    assert [s["request_id"] for s in SCENARIOS] == list(EXPECTED)
    for scenario in SCENARIOS:
        employee_id, decision, reason_code = EXPECTED[scenario["request_id"]]
        assert scenario["employee_id"] == employee_id
        assert (scenario["expected_decision"], scenario["expected_reason_code"]) == (
            decision, reason_code,
        )  # fmt: skip
        assert scenario["message"]
    assert "EMP surge" in SCENARIOS[2]["message"]  # demo 3's exception claim
    assert "combat drone" in SCENARIOS[3]["message"]


# --- a well-behaved scripted agent for all six, in order ---


def act(why, tool, **args):
    return reply(text(f"Thought: {why}"), tool_use(tool, args))


def submit_quoting(decision, reason_code, item, request_type):
    def _reply(messages):
        observation = json.loads(messages[-1]["content"][0]["content"])
        quoted = observation.get("decision_id") or observation.get("ticket_id")
        return reply(text("Thought: outcome recorded; submit and quote the id."),
                     tool_use("submit_decision", {
                         "decision": decision, "reason_code": reason_code, "item": item,
                         "request_type": request_type,
                         "draft_response": f"Militech Asset Control: {decision} ({quoted}).",
                     }))  # fmt: skip

    return _reply


def flag(employee_id, code, request):
    return act(f"Escalate with {code}.", "flag_for_human_review", employee_id=employee_id,
               request=request, reason=f"Needs a human: {code}", reason_code=code)  # fmt: skip


def six_demo_script():
    return ScriptedLLM([
        # 1: second holo-display -> approve
        act("Check eligibility.", "check_request_eligibility", employee_id="E1001",
            item="holo_display"),
        act("Within policy: record.", "record_decision", employee_id="E1001", item="holo_display",
            decision="approve", request_type="additional", reason="Within the cap of 2"),
        submit_quoting("approve", None, "holo_display", "additional"),
        # 2: corpo cyberdeck -> deny NOT_ENTITLED
        act("Check eligibility.", "check_request_eligibility", employee_id="E1003",
            item="cyberdeck"),
        act("Not entitled: record the denial.", "record_decision", employee_id="E1003",
            item="cyberdeck", decision="deny", request_type="additional",
            reason="NOT_ENTITLED: corpo_staff are never issued cyberdecks"),
        submit_quoting("deny", "NOT_ENTITLED", "cyberdeck", "additional"),
        # 3: EMP-fried terminal -> escalate EXCEPTION_CLAIMED
        act("Check eligibility.", "check_request_eligibility", employee_id="E1002",
            item="terminal"),
        flag("E1002", "EXCEPTION_CLAIMED", "EMP surge fried my terminal"),
        submit_quoting("escalate", "EXCEPTION_CLAIMED", "terminal", None),
        # 4: combat drone -> escalate OFF_CATALOG
        act("Check eligibility.", "check_request_eligibility", employee_id="E1004",
            item="combat drone"),
        flag("E1004", "OFF_CATALOG", "Military-grade combat drone"),
        submit_quoting("escalate", "OFF_CATALOG", "combat drone", None),
        # 5: unknown employee -> escalate EMPLOYEE_NOT_FOUND
        act("Look up the employee.", "get_employee_info", employee_id="E9999"),
        flag("E9999", "EMPLOYEE_NOT_FOUND", "Need a neural headset"),
        submit_quoting("escalate", "EMPLOYEE_NOT_FOUND", "neural_headset", None),
        # 6: executive terminal replacement -> approve
        act("Check eligibility.", "check_request_eligibility", employee_id="E1005",
            item="terminal"),
        act("Replacement within policy: record.", "record_decision", employee_id="E1005",
            item="terminal", decision="approve", request_type="replacement",
            reason="Past the 18-month refresh"),
        submit_quoting("approve", None, "terminal", "replacement"),
    ])  # fmt: skip


def confirming_reviewer(times=6):
    return ScriptedLLM([reply(text('{"verdict": "confirmed", "issues": []}'))
                        for _ in range(times)])  # fmt: skip


async def replay(data_dir, tmp_path, out=None):
    traces = tmp_path / "traces"
    reviewer = confirming_reviewer()
    results = await run_demo(six_demo_script(), reviewer, seed_dir=data_dir, traces_dir=traces,
                             out=out or (lambda line: None))  # fmt: skip
    return results, reviewer, traces


# --- the replay ---


async def test_all_six_reach_their_expected_outcomes(data_dir, tmp_path):
    results, _, _ = await replay(data_dir, tmp_path)

    assert [(r.request_id, r.decision, r.reason_code) for r in results] == [
        (rid, decision, code) for rid, (_, decision, code) in EXPECTED.items()
    ]
    assert mismatches(results) == []
    assert all(r.decision_id for r in results if r.decision in ("approve", "deny"))
    assert all(r.ticket_id for r in results if r.decision == "escalate")


async def test_the_demo_reflects_on_every_drafted_reply(data_dir, tmp_path):
    results, reviewer, _ = await replay(data_dir, tmp_path)

    assert len(reviewer.calls) == 6
    assert all(r.reflection and r.reflection[0]["verdict"] == "confirmed" for r in results)


async def test_the_demo_writes_traces_a_summary_and_the_logs(data_dir, tmp_path):
    results, _, traces = await replay(data_dir, tmp_path)

    for rid in EXPECTED:
        assert (traces / f"{rid}.md").exists(), rid
    summary = (traces / "summary.md").read_text()
    for result in results:
        assert result.request_id in summary
        assert (result.decision_id or result.ticket_id) in summary
    assert "EXCEPTION_CLAIMED" in summary and "confirmed" in summary
    decisions = (traces / "decisions.jsonl").read_text().splitlines()
    escalations = (traces / "escalations.jsonl").read_text().splitlines()
    assert [json.loads(d)["decision"] for d in decisions] == ["approve", "deny", "approve"]
    assert [json.loads(e)["reason_code"] for e in escalations] == [
        "EXCEPTION_CLAIMED", "OFF_CATALOG", "EMPLOYEE_NOT_FOUND",
    ]  # fmt: skip


async def test_the_demo_never_writes_to_the_seed_data(data_dir, tmp_path):
    before = sorted(p.name for p in data_dir.iterdir())

    await replay(data_dir, tmp_path)

    assert sorted(p.name for p in data_dir.iterdir()) == before  # no decisions/escalations


async def test_the_demo_prints_each_step_and_a_summary(data_dir, tmp_path):
    lines = []

    await replay(data_dir, tmp_path, out=lines.append)

    printed = "\n".join(lines)
    for label in ("Thought:", "Action:", "Observation:", "Decision:"):
        assert label in printed, label
    assert "REQ-0006" in printed and "6/6 as expected" in printed


# --- expectations, and the command's exit codes ---


async def test_mismatches_name_the_request(data_dir, tmp_path):
    results, _, _ = await replay(data_dir, tmp_path)
    results[1].decision, results[1].reason_code = "approve", None

    (problem,) = mismatches(results)

    assert "REQ-0002" in problem and "deny" in problem and "approve" in problem


def test_main_without_credentials_exits_2(monkeypatch, tmp_path):
    for name in ("AZURE_FOUNDRY_ENDPOINT", "AZURE_FOUNDRY_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert demo.main(env_file=tmp_path / "missing.env") == 2


def test_main_exits_1_when_a_demo_misses_its_expected_outcome(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", "https://example.services.ai.azure.com/anthropic")
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "test-key")

    async def off_by_one(llm, reviewer, **kwargs):
        return ["stand-in result"]

    monkeypatch.setattr(demo, "run_demo", off_by_one)
    monkeypatch.setattr(demo, "mismatches", lambda results: ["REQ-0002: expected deny"])

    assert demo.main(env_file=tmp_path / "missing.env") == 1
    assert "REQ-0002: expected deny" in capsys.readouterr().err


def test_main_passes_a_reviewer(monkeypatch, tmp_path):
    # Reflection only runs when a reviewer is passed (M11); the demo must pass one.
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", "https://example.services.ai.azure.com/anthropic")
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "test-key")
    seen = {}

    async def capture(llm, reviewer, **kwargs):
        seen["reviewer"] = reviewer
        return []

    monkeypatch.setattr(demo, "run_demo", capture)

    assert demo.main(env_file=tmp_path / "missing.env") == 0
    assert seen["reviewer"] is not None
