"""The deterministic guardrail (requirements §8, plan M10).

`validate(submission, trace, as_of=...)` checks the agent's final submit_decision against what
the tools actually returned. It reads only the trace, so these tests build traces by hand.
"""

from datetime import date

import pytest

from militech_agent.guardrail import Violation, validate

AS_OF = date(2026, 11, 23)
DEC = "DEC-20261123-0001"
ESC = "ESC-20261123-0001"


def entry(action, args, observation):
    return {"step": 0, "thought": "t", "action": action, "args": args, "observation": observation}


def recorded(decision, item="holo_display", request_type="additional", decision_id=DEC):
    return entry(
        "record_decision",
        {"employee_id": "E1001", "item": item, "decision": decision,
         "request_type": request_type, "reason": "r"},
        {"decision_id": decision_id, "created_at": "2026-11-23T12:00:00+00:00"},
    )  # fmt: skip


def refused(decision="approve", item="holo_display"):
    return entry(
        "record_decision",
        {"employee_id": "E1001", "item": item, "decision": decision,
         "request_type": "additional", "reason": "r"},
        {"error": "approval_not_permitted", "status": "not_entitled"},
    )  # fmt: skip


def flagged(reason_code="EXCEPTION_CLAIMED", ticket_id=ESC):
    return entry(
        "flag_for_human_review",
        {"employee_id": "E1002", "request": "r", "reason": "r", "reason_code": reason_code},
        {"ticket_id": ticket_id, "status": "open", "created_at": "2026-11-23T12:00:00+00:00"},
    )  # fmt: skip


def submission(decision, reason_code=None, item="holo_display", request_type="additional",
               draft=None):  # fmt: skip
    ids = {"approve": DEC, "deny": DEC, "escalate": ESC}
    draft = draft if draft is not None else f"Done: {ids[decision]}."
    return {"decision": decision, "reason_code": reason_code, "item": item,
            "request_type": request_type, "draft_response": draft}  # fmt: skip


def codes(violations):
    return [v.code for v in violations]


# --- a valid decision of each type passes ---


@pytest.mark.parametrize(
    ("sub", "trace"),
    [
        (submission("approve"), [recorded("approve")]),
        (submission("deny", "NOT_ENTITLED", item="cyberdeck"), [recorded("deny", "cyberdeck")]),
        (submission("escalate", "EXCEPTION_CLAIMED", item="terminal", request_type=None),
         [flagged()]),
        # A refused approval followed by a recorded deny is still a clean deny.
        (submission("deny", "NOT_ENTITLED", item="cyberdeck"),
         [refused(item="cyberdeck"), recorded("deny", "cyberdeck")]),
    ],
    ids=["approve", "deny", "escalate", "refused-then-deny"],
)  # fmt: skip
def test_valid_decisions_have_no_violations(sub, trace):
    assert validate(sub, trace, as_of=AS_OF) == []


# --- each rule ---


@pytest.mark.parametrize(
    ("sub", "trace", "expected"),
    [
        # approve needs a successful record_decision(approve) for that item
        (submission("approve"), [], "approve_not_recorded"),
        (submission("approve"), [refused()], "approve_not_recorded"),
        (submission("approve"), [recorded("deny")], "approve_not_recorded"),
        (submission("approve"), [recorded("approve", item="terminal")], "approve_not_recorded"),
        # deny needs a successful record_decision(deny), with a deny code
        (submission("deny", "NOT_ENTITLED"), [], "deny_not_recorded"),
        (submission("deny", "OFF_CATALOG"), [recorded("deny")], "deny_code_invalid"),
        (submission("deny", None), [recorded("deny")], "deny_code_invalid"),
        # escalate needs a ticket_id from flag_for_human_review
        (submission("escalate", "OFF_CATALOG", request_type=None), [], "escalation_not_flagged"),
        (submission("escalate", "OFF_CATALOG", request_type=None),
         [entry("flag_for_human_review", {}, {"error": "invalid_reason_code"})],
         "escalation_not_flagged"),
        # the submitted code must be the one the ticket was opened with
        (submission("escalate", "OFF_CATALOG", request_type=None),
         [flagged("EXCEPTION_CLAIMED")], "escalation_code_mismatch"),
        # the draft must quote the id the employee needs
        (submission("approve", draft="Approved, enjoy!"), [recorded("approve")],
         "draft_missing_id"),
        (submission("escalate", "EXCEPTION_CLAIMED", request_type=None, draft="Escalated."),
         [flagged()], "draft_missing_id"),
        # what was recorded must match what is submitted
        (submission("approve", request_type="replacement"), [recorded("approve")],
         "request_type_mismatch"),
        # one outcome only: no decision recorded for an escalation, and no ticket for a decision
        (submission("escalate", "EXCEPTION_CLAIMED", request_type=None),
         [recorded("approve"), flagged()], "conflicting_records"),
        (submission("approve"), [recorded("approve"), flagged()], "conflicting_records"),
    ],
)  # fmt: skip
def test_each_rule_is_enforced(sub, trace, expected):
    assert expected in codes(validate(sub, trace, as_of=AS_OF))


def test_violations_explain_themselves():
    (violation,) = validate(submission("approve"), [], as_of=AS_OF)

    assert isinstance(violation, Violation)
    assert violation.code == "approve_not_recorded"
    assert "record_decision" in violation.message
    assert violation.to_dict() == {"code": violation.code, "message": violation.message}


# --- explicit replacement below the cap: the guardrail recomputes the age itself ---


def employee_info(*issued, item="haptic_keyboard"):
    units = [{"asset_id": f"MT-{n}", "item": item, "issued_date": d} for n, d in enumerate(issued)]
    return entry("get_employee_info", {"employee_id": "E2001"},
                 {"employee_id": "E2001", "role": "netrunner", "equipment": units})  # fmt: skip


def policy(refresh_months=12, item="haptic_keyboard", max_quantity=2):
    limits = {item: {"max_quantity": max_quantity, "refresh_months": refresh_months,
                     "entitled": True}}  # fmt: skip
    return entry("get_policy_limits", {"role": "netrunner"},
                 {"role": "netrunner", "limits": limits})  # fmt: skip


def below_cap(item="haptic_keyboard"):
    return entry("check_request_eligibility", {"employee_id": "E2001", "item": item},
                 {"status": "within_policy", "eligible": True, "item": item,
                  "request_type_assumed": "additional", "held": 1, "max_quantity": 2})  # fmt: skip


def replacement(item="haptic_keyboard"):
    return submission("approve", item=item, request_type="replacement")


def test_replacement_below_the_cap_is_allowed_once_the_oldest_unit_is_due():
    trace = [employee_info("2024-01-01", "2026-06-01"), policy(12), below_cap(),
             recorded("approve", "haptic_keyboard", "replacement")]  # fmt: skip

    assert validate(replacement(), trace, as_of=AS_OF) == []


def test_replacement_below_the_cap_before_refresh_is_a_violation():
    trace = [employee_info("2026-03-01"), policy(12), below_cap(),
             recorded("approve", "haptic_keyboard", "replacement")]  # fmt: skip

    violations = validate(replacement(), trace, as_of=AS_OF)

    assert codes(violations) == ["replacement_not_due"]
    assert "2027-03-01" in violations[0].message  # when it will be due


def test_refresh_is_due_exactly_on_the_anniversary():
    trace = [employee_info("2025-11-23"), policy(12), below_cap(),
             recorded("approve", "haptic_keyboard", "replacement")]  # fmt: skip

    assert validate(replacement(), trace, as_of=AS_OF) == []


@pytest.mark.parametrize(
    "missing",
    ["get_employee_info", "get_policy_limits"],
)
def test_replacement_below_the_cap_needs_the_data_to_check_it(missing):
    trace = [employee_info("2024-01-01"), policy(12), below_cap(),
             recorded("approve", "haptic_keyboard", "replacement")]  # fmt: skip
    trace = [e for e in trace if e["action"] != missing]

    assert codes(validate(replacement(), trace, as_of=AS_OF)) == ["replacement_not_verified"]


def test_replacement_at_the_cap_is_left_to_the_server():
    # At the cap, check_request_eligibility already applied the refresh rule.
    at_cap = entry("check_request_eligibility", {"employee_id": "E1005", "item": "terminal"},
                   {"status": "within_policy", "eligible": True, "item": "terminal",
                    "request_type_assumed": "replacement"})  # fmt: skip
    trace = [at_cap, recorded("approve", "terminal", "replacement")]

    assert validate(replacement("terminal"), trace, as_of=AS_OF) == []
