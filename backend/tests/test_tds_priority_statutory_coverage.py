from copy import deepcopy
import pytest
from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, load_catalog

CASES = (
    ("STAT-393-1-8V-ECOMMERCE-INDHUF-2026-V1", "CA-FY2026-27-RES-35", "ecommerce", "0.1", "500000", "ECOMMERCE_OPERATOR", "INDIVIDUAL_HUF", (("PARTY_CAPACITY", "ECOMMERCE_PARTICIPANT_INDIVIDUAL_HUF"), ("SERVICE_ACTIVITY_CHANNEL", "ECOMMERCE_PLATFORM_FACILITATED"), ("PARTY_CAPACITY", "PAN_OR_AADHAAR_FURNISHED_TO_ECOMMERCE_OPERATOR"))),
    ("STAT-393-1-8V-ECOMMERCE-OTHER-2026-V1", "CA-FY2026-27-RES-36", "ecommerce", "0.1", None, "ECOMMERCE_OPERATOR", "PERSON", (("PARTY_CAPACITY", "ECOMMERCE_PARTICIPANT_OTHER_THAN_INDIVIDUAL_HUF"), ("SERVICE_ACTIVITY_CHANNEL", "ECOMMERCE_PLATFORM_FACILITATED"))),
    ("STAT-393-1-8VI-VDA-SPECIFIED-PERSON-2026-V1", "CA-FY2026-27-RES-37", "virtual_digital_asset", "1", "50000", None, "PERSON", (("ASSET_INSTRUMENT_SCHEME", "VIRTUAL_DIGITAL_ASSET_TRANSFER"), ("PARTY_CAPACITY", "VDA_SPECIFIED_PERSON"), ("SERVICE_ACTIVITY_CHANNEL", "VDA_RELEASE_CONDITION_SATISFIED"))),
    ("STAT-393-1-8VI-VDA-OTHER-PERSON-2026-V1", "CA-FY2026-27-RES-38", "virtual_digital_asset", "1", "10000", None, "PERSON", (("ASSET_INSTRUMENT_SCHEME", "VIRTUAL_DIGITAL_ASSET_TRANSFER"), ("PARTY_CAPACITY", "VDA_OTHER_THAN_SPECIFIED_PERSON"), ("SERVICE_ACTIVITY_CHANNEL", "VDA_RELEASE_CONDITION_SATISFIED"))),
)
PURCHASE = ("STAT-393-1-8II-PURCHASE-GOODS-2026-V1", "CA-FY2026-27-RES-32")

def _rule(rule_id):
    _, rules = load_catalog()
    return document(deepcopy(next(r for r in rules if r["rule_id"] == rule_id)), "test")

def _facts(source, pairs):
    return [{"fact_type": t, "value_code": v, "evidence_status": "VERIFIED", "evidence_reference": f"EVIDENCE-{source}-{i}", "source_condition_reference": source} for i, (t,v) in enumerate(pairs)]

def _row(nature, facts, amount="1", payer=None, recipient="PERSON", **changes):
    row={"transaction_id":"PRIORITY-1","source_reference":"PRIORITY-1","financial_year":"2026-27","payment_nature":nature,"recipient_residency":"RESIDENT","recipient_category":recipient,"amount":amount,"deductee_pan":"ABCDE1234F","credit_date":"2026-05-01","payment_date":"2026-05-01","tds_deducted":"0","classification_facts":facts}
    if payer: row['payer_category']=payer
    row.update(changes); return row

@pytest.mark.parametrize("rule_id,source,nature,rate,threshold,payer,recipient,pairs", CASES)
def test_priority_catalog_rules_are_source_verified_thresholded_and_frozen(rule_id, source, nature, rate, threshold, payer, recipient, pairs):
    rule=_rule(rule_id); facts=_facts(source,pairs)
    assert rule['lifecycle']=='ACTIVE' and source_traceability_snapshot(rule)['status']=='VERIFIED'
    if threshold:
        below=calculate_ledger_transactions([_row(nature,facts,str(int(threshold)-1),payer,recipient)], [rule])[0]
        boundary=calculate_ledger_transactions([_row(nature,facts,threshold,payer,recipient)], [rule])[0]
        above=calculate_ledger_transactions([_row(nature,facts,str(int(threshold)+1),payer,recipient)], [rule])[0]
        assert below['expected_tds']==boundary['expected_tds']==0.0
    else:
        above=calculate_ledger_transactions([_row(nature,facts,'1000',payer,recipient)], [rule])[0]
    assert above['calculation_status']=='CALCULATED' and above['rule_id']==rule_id
    assert above['expected_tds']==pytest.approx(round(float(above['current_amount'])*float(rate)/100, 2))
    frozen=freeze_calculation_results([above])[0]
    assert frozen['rule_snapshot']['rule_id']==rule_id and frozen['classification_facts']==facts

@pytest.mark.parametrize("rule_id,source,nature,rate,threshold,payer,recipient,pairs", CASES)
def test_priority_catalog_rules_fail_closed_for_missing_contradictory_or_wrong_fy_evidence(rule_id, source, nature, rate, threshold, payer, recipient, pairs):
    rule=_rule(rule_id); facts=_facts(source,pairs); amount=str(int(threshold or '1000')+1)
    missing=calculate_ledger_transactions([_row(nature,[],amount,payer,recipient)], [rule])[0]
    assert missing['calculation_status']=='REVIEW_REQUIRED' and missing['reason_code']=='CLASSIFICATION_FACT_REQUIRED'
    contradictory=deepcopy(facts); contradictory[0].update({'evidence_status':'CONTRADICTED','review_reason':'Evidence conflicts'})
    unresolved=calculate_ledger_transactions([_row(nature,contradictory,amount,payer,recipient)], [rule])[0]
    assert unresolved['calculation_status']=='REVIEW_REQUIRED'
    prior_act=calculate_ledger_transactions([_row(nature,facts,amount,payer,recipient,credit_date='2026-03-31',payment_date='2026-03-31')], [rule])[0]
    assert prior_act['calculation_status']=='RULE_NOT_FOUND'


def test_purchase_goods_applies_only_to_excess_after_eligible_buyer_and_no_other_tds_tcs_evidence():
    rule_id,source=PURCHASE; rule=_rule(rule_id)
    facts=_facts(source, (("PARTY_CAPACITY","PURCHASE_GOODS_ELIGIBLE_BUYER_PRECEDING_TY_TURNOVER_OVER_10_CRORE"),("SERVICE_ACTIVITY_CHANNEL","NO_OTHER_TDS_OR_TCS_APPLIES")))
    below,boundary,cross=calculate_ledger_transactions([
        _row('purchase_of_goods',facts,'4999999'), _row('purchase_of_goods',facts,'1'), _row('purchase_of_goods',facts,'10'),
    ],[rule])
    assert below['expected_tds']==boundary['expected_tds']==0.0
    assert cross['threshold_status']=='AGGREGATE_THRESHOLD_MET' and cross['expected_tds']==pytest.approx(0.01)
    assert cross['current_amount']==10.0 and cross['amount_subject_to_tds']==10.0
    frozen=freeze_calculation_results([cross])[0]
    assert frozen['rule_snapshot']['threshold_type']=='AGGREGATE_EXCESS'


def test_purchase_goods_fails_closed_when_buyer_eligibility_or_other_tds_tcs_assessment_is_missing():
    rule_id,source=PURCHASE; rule=_rule(rule_id)
    facts=_facts(source, (("PARTY_CAPACITY","PURCHASE_GOODS_ELIGIBLE_BUYER_PRECEDING_TY_TURNOVER_OVER_10_CRORE"),("SERVICE_ACTIVITY_CHANNEL","NO_OTHER_TDS_OR_TCS_APPLIES")))
    for value in ([], facts[:1], facts[1:]):
        result=calculate_ledger_transactions([_row('purchase_of_goods',value,'5000001')],[rule])[0]
        assert result['calculation_status']=='REVIEW_REQUIRED' and result['reason_code']=='CLASSIFICATION_FACT_REQUIRED'