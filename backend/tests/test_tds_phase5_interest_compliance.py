from copy import deepcopy

from engine.tds_compliance.core import calculate_interest_compliance, calculate_interest_from_deposit_results, calculate_interest_periods


PHASE2 = {"transaction_id": "PAY-1", "calculation_status": "CALCULATED", "expected_tds": 100.0, "tds_deducted": 100.0, "effective_event_date": "2025-06-01", "governing_act": "ACT", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL"}
DUE_SNAPSHOT = {"policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE", "rule_id": "TEST-DUE-1", "rule_version": "v1", "active": True, "lifecycle": "ACTIVE", "source_traceability_status": "VERIFIED", "approved_at": "2026-10-06T00:00:00Z", "approved_by": "TEST-CA"}
DEPOSIT = {"transaction_id": "PAY-1", "calculation_status": "CALCULATED", "expected_tds": 100.0, "actual_tds_deducted": 100.0, "deposited_tds": 100.0, "deposit_difference": 0.0, "governing_law": "ACT", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL", "deductible_date": "2025-06-01", "actual_deduction_date": "2025-06-01", "evidence_version_id": "EV-1", "evidence_entries": [{"deposit_date": "2025-06-03", "tds_amount": 100.0}], "contractor_due_date_status": "DETERMINED", "deposit_due_date": "2025-06-03", "due_date_policy_snapshot": DUE_SNAPSHOT, "due_date_policy_branch_inputs": {"deductor_type": "OTHER_DEDUCTOR"}}


def rule(kind, **overrides):
    base = {
        "rule_id": f"{kind}-1", "rule_version": "v1", "interest_type": kind,
        "active": True, "lifecycle": "ACTIVE", "governing_law": "ACT",
        "effective_from": "2025-04-01", "effective_to": "2026-03-31",
        "payment_nature": "contractor", "deductee_type": "INDIVIDUAL", "priority": 1,
        "rate": "1", "interest_base": "expected_tds",
        "period_counting_method": "CALENDAR_MONTH_OR_PART", "rounding_method": "HALF_UP",
        "rounding_precision": 2,
        # These are isolated policy-shape fixtures.  Real policy records
        # still require the governed lifecycle before they can be ACTIVE.
        "source": "TEST_OFFICIAL_SOURCE", "source_reference": "TEST-SECTION-398",
        "source_url": "https://example.test/section-398",
        "source_document_title": "Isolated Phase 5 policy fixture",
        "source_provision_reference": "Section 398(3)",
        "source_retrieved_at": "2026-10-06", "source_verified_at": "2026-10-06",
        "source_verification_evidence": "Test-only verified policy shape.",
        "approved_at": "2026-10-06T00:00:00Z", "approved_by": "TEST-CA",
        "approval_metadata": {"approval_authority": "TEST-CA", "approval_reference": "TEST-398"},
    }
    base.update(overrides)
    return base


RULES = [rule("DEDUCTION_DELAY_INTEREST"), rule("DEPOSIT_DELAY_INTEREST")]


def calculate(deposit=DEPOSIT, phase2=PHASE2, rules=RULES):
    return calculate_interest_from_deposit_results([phase2], [deposit], rules, assignment_id="A-1", calculation_id="CALC-1", deposit_run_id="DEP-1", ledger_version_id="LEDGER-1")[0]


def test_no_delay_uses_frozen_phase2_and_persisted_phase4_sources():
    result = calculate()
    assert result["expected_tds"] == 100.0
    assert result["deposited_tds"] == 100.0
    assert result["overall_status"] == "NO_INTEREST_INDICATED"
    assert result["deduction_interest"] == 0.0
    assert result["deposit_interest"] == 0.0


def test_late_deduction_and_late_deposit_are_separate_components():
    deposit = {**DEPOSIT, "actual_deduction_date": "2025-06-02", "evidence_entries": [{"deposit_date": "2025-06-08", "tds_amount": 100.0}]}
    result = calculate(deposit=deposit)
    assert result["deduction_status"] == "LATE_DEDUCTION"
    assert result["deposit_status"] == "LATE_DEPOSIT"
    assert result["deduction_interest"] == 1.0
    assert result["deposit_interest"] == 1.0
    assert result["overall_status"] == "INTEREST_DUE"


def test_late_deduction_at_one_percent_with_timely_deposit_has_no_deposit_interest():
    deposit = {
        **DEPOSIT,
        "actual_deduction_date": "2025-06-02",
        # The test policy's deadline is three days after the deduction date.
        "evidence_entries": [{"deposit_date": "2025-06-03", "tds_amount": 100.0}],
    }
    result = calculate(deposit=deposit)

    assert result["deduction_status"] == "LATE_DEDUCTION"
    assert result["deduction_interest_rate"] == 1.0
    assert result["deduction_interest"] == 1.0
    assert result["deposit_status"] == "DEPOSIT_SUPPORTED"
    assert result["deposit_interest"] == 0.0


def test_timely_deduction_with_late_deposit_at_one_point_five_percent():
    deposit = {
        **DEPOSIT,
        "evidence_entries": [{"deposit_date": "2025-06-05", "tds_amount": 100.0}],
    }
    deposit_rule = rule("DEPOSIT_DELAY_INTEREST", rate="1.5", interest_base="actual_tds")
    result = calculate(deposit=deposit, rules=[rule("DEDUCTION_DELAY_INTEREST"), deposit_rule])

    assert result["deduction_interest"] == 0.0
    assert result["deposit_status"] == "LATE_DEPOSIT"
    assert result["deposit_interest_rate"] == 1.5
    assert result["deposit_interest"] == 1.5


def test_same_day_deduction_and_payment_has_no_interest():
    deposit = {
        **DEPOSIT,
        "actual_deduction_date": "2025-06-01",
        "evidence_entries": [{"deposit_date": "2025-06-01", "tds_amount": 100.0}],
    }
    result = calculate(deposit=deposit)

    assert result["deduction_delay_periods"] == 0
    assert result["deposit_delay_periods"] == 0
    assert result["total_interest"] == 0.0
    assert result["overall_status"] == "NO_INTEREST_INDICATED"


def test_old_act_thirty_day_month_interpretation_is_configurable_not_a_global_default():
    """PDF p. 15 cites the 1961 Act s.201(1A) 30-day interpretation."""
    old_act_rule = rule(
        "DEPOSIT_DELAY_INTEREST",
        governing_law="INCOME_TAX_ACT_1961",
        period_counting_method="CONFIGURED_FIXED_DAY_BLOCK",
        period_day_block=30,
    )

    # The cited example: 12 July to 10 August spans one 30-day period.
    assert calculate_interest_periods("2026-07-12", "2026-08-10", old_act_rule) == 1


def test_fy_2026_27_deposit_interest_uses_deduction_to_actual_payment_period():
    """Section 398(3)(a)(ii): 1.5% per month or part from deduction to payment."""
    phase2 = {
        **PHASE2,
        "effective_event_date": "2026-04-30",
        "governing_act": "INCOME_TAX_ACT_2025",
    }
    deposit = {
        **DEPOSIT,
        "governing_law": "INCOME_TAX_ACT_2025",
        "deductible_date": "2026-04-30",
        "actual_deduction_date": "2026-04-30",
        "evidence_entries": [{"deposit_date": "2026-05-08", "tds_amount": 100.0}],
        "deposit_due_date": "2026-05-07",
    }
    deduction = rule("DEDUCTION_DELAY_INTEREST", governing_law="INCOME_TAX_ACT_2025", effective_from="2026-04-01", effective_to="2027-03-31")
    deposit_rule = rule(
        "DEPOSIT_DELAY_INTEREST",
        governing_law="INCOME_TAX_ACT_2025",
        effective_from="2026-04-01",
        effective_to="2027-03-31",
        rate="1.5",
        interest_base="actual_tds",
    )

    result = calculate(deposit=deposit, phase2=phase2, rules=[deduction, deposit_rule])

    assert result["deposit_due_date"] == "2026-05-07"
    assert result["deposit_status"] == "LATE_DEPOSIT"
    assert result["deposit_delay_periods"] == 2
    assert result["deposit_interest_rate"] == 1.5
    assert result["deposit_interest"] == 3.0


def test_missing_dates_and_invalid_date_order_are_controlled():
    missing = calculate(deposit={**DEPOSIT, "actual_deduction_date": None})
    invalid = calculate(deposit={**DEPOSIT, "actual_deduction_date": "2025-05-30"})
    no_deposit = calculate(deposit={**DEPOSIT, "evidence_entries": [{"tds_amount": 100.0}]})
    assert missing["overall_status"] == "DATE_NOT_DETERMINABLE"
    assert invalid["overall_status"] == "INVALID_DATE_ORDER"
    assert no_deposit["overall_status"] == "DATE_NOT_DETERMINABLE"


def test_missing_or_ambiguous_policy_never_guesses_interest():
    missing = calculate(rules=[])
    ambiguous = calculate(rules=[rule("DEDUCTION_DELAY_INTEREST"), rule("DEDUCTION_DELAY_INTEREST", rule_id="D2"), rule("DEPOSIT_DELAY_INTEREST")])
    assert missing["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert ambiguous["overall_status"] == "INTEREST_REVIEW_REQUIRED"


def test_active_policy_without_required_governance_metadata_is_not_selected():
    """An active-looking record cannot bypass the governed policy lifecycle."""
    ungoverned = rule("DEDUCTION_DELAY_INTEREST", approval_metadata={})
    result = calculate(rules=[ungoverned, rule("DEPOSIT_DELAY_INTEREST")])

    assert result["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert "deduction_rule_snapshot" not in result


def test_api_persisted_governing_act_scope_is_selected_for_interest():
    api_style_rules = [
        {key: value for key, value in rule("DEDUCTION_DELAY_INTEREST", governing_law=None, governing_act="ACT").items() if key != "governing_law"},
        {key: value for key, value in rule("DEPOSIT_DELAY_INTEREST", governing_law=None, governing_act="ACT").items() if key != "governing_law"},
    ]

    result = calculate(rules=api_style_rules)

    assert result["overall_status"] == "NO_INTEREST_INDICATED"
    assert result["deduction_rule_snapshot"]["governing_act"] == "ACT"
    assert result["deposit_rule_snapshot"]["governing_act"] == "ACT"


def test_historical_snapshots_remain_unchanged_after_source_changes():
    phase2, deposit = deepcopy(PHASE2), deepcopy(DEPOSIT)
    result = calculate(phase2=phase2, deposit=deposit)
    phase2["expected_tds"] = 999.0
    deposit["evidence_entries"][0]["deposit_date"] = "2025-12-31"
    assert result["expected_tds"] == 100.0
    assert result["actual_deposit_date"] == "2025-06-03"
    assert result["calculation_id"] == "CALC-1"
    assert result["deposit_run_id"] == "DEP-1"


def test_interest_uses_the_frozen_governed_contractor_due_date_snapshot():
    snapshot = deepcopy(DUE_SNAPSHOT)
    deposit = {
        **DEPOSIT,
        "contractor_due_date_status": "DETERMINED",
        "deposit_due_date": "2025-06-03",
        "due_date_policy_snapshot": snapshot,
        "due_date_policy_branch_inputs": {"deductor_type": "OTHER_DEDUCTOR"},
    }
    result = calculate(deposit=deposit)
    snapshot["rule_version"] = "v2"

    assert result["overall_status"] == "NO_INTEREST_INDICATED"
    assert result["deposit_due_date"] == "2025-06-03"
    assert result["due_date_policy_snapshot"]["rule_id"] == "TEST-DUE-1"
    assert result["due_date_policy_snapshot"]["rule_version"] == "v1"
    assert result["due_date_policy_branch_inputs"] == {"deductor_type": "OTHER_DEDUCTOR"}


def test_later_live_due_date_policy_change_cannot_alter_phase5_snapshot_result():
    phase4_result = deepcopy(DEPOSIT)
    live_policy = deepcopy(DUE_SNAPSHOT)
    phase4_result["due_date_policy_snapshot"] = deepcopy(live_policy)
    live_policy.update({"rule_version": "v2", "deadline_mode": "MONTH_END_PLUS_DAYS"})

    result = calculate(deposit=phase4_result)

    assert result["deposit_due_date"] == "2025-06-03"
    assert result["due_date_policy_snapshot"]["rule_version"] == "v1"


def test_phase5_rejects_missing_or_malformed_phase4_due_date_snapshot():
    missing = calculate(deposit={key: value for key, value in DEPOSIT.items() if key not in {"due_date_policy_snapshot", "due_date_policy_branch_inputs"}})
    malformed = calculate(deposit={**DEPOSIT, "due_date_policy_snapshot": {**DUE_SNAPSHOT, "approved_by": None}})

    assert missing["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert malformed["overall_status"] == "POLICY_NOT_CONFIGURED"


def test_legacy_interest_preview_cannot_bypass_the_frozen_phase4_due_date_snapshot():
    legacy = calculate_interest_compliance(
        [PHASE2],
        [{"transaction_id": "PAY-1", "deductee_type": "INDIVIDUAL", "deduction_date": "2025-06-01", "deposit_date": "2025-06-03"}],
        RULES,
        assignment_id="A-1",
        calculation_id="CALC-1",
        ledger_version_id="LEDGER-1",
    )[0]

    assert legacy["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert "frozen Phase 4 due-date policy snapshot" in legacy["reason"]
    assert "deposit_due_date" not in legacy
