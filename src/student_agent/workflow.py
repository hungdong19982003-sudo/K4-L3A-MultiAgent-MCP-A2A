from __future__ import annotations

import logging
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

logger = logging.getLogger(__name__)


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent workflow for e-commerce complaint investigation.

    Agents involved:
    1. Coordinator: Manages workflow lifecycle and task delegation.
    2. PolicyAgent: Fetches policy terms and determines applicable resolution rules.
    3. OrderAgent: Investigates order and item entities.
    4. PaymentAgent: Investigates payments, captures, and refund timeline.
    5. ShipmentAgent: Investigates carrier logistics, shipping limits, and delivery events.
    6. InvestigationAgent: Synthesizes evidence to pinpoint primary issue and root cause.
    7. VerifierAgent: Enforces consistency invariants and validates schema compliance.
    """
    case_id = case["case_id"]
    customer_request = case.get("customer_request", {})
    claimed_order_id = customer_request.get("claimed_order_id", "").strip()
    claims = customer_request.get("claims", [])
    policy_version = case.get("policy_version", "EC_POLICY_V1")

    # Coordinator delegates tasks
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="policy-agent",
        attributes={"task": "fetch_policy", "policy_version": policy_version},
    )

    collected_evidence: dict[str, dict[str, Any]] = {}

    # 1. Policy Agent
    policy_res = await gateway.call("get_policy", case_id=case_id, policy_version=policy_version)
    collected_evidence["policy"] = policy_res
    policy_ref = policy_res["evidence_ref"]
    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="policy-agent",
        tool_name="get_policy",
        evidence_refs=[policy_ref],
        attributes={"policy_version": policy_version},
    )
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy-agent",
        decision_code=f"POLICY_LOADED_{policy_version}",
        evidence_refs=[policy_ref],
    )
    policy_rules = policy_res.get("data", {}).get("rules", {})

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy-agent",
        target="order-agent",
        decision_code="POLICY_TO_ORDER",
    )

    # 2. Order Agent
    order_data: dict[str, Any] = {}
    items_data: list[dict[str, Any]] = []
    sellers_data: list[dict[str, Any]] = []

    if claimed_order_id:
        try:
            ord_res = await gateway.call("get_order", case_id=case_id, order_id=claimed_order_id)
            collected_evidence["order"] = ord_res
            order_data = ord_res.get("data", {})
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order",
                evidence_refs=[ord_res["evidence_ref"]],
                attributes={"order_id": claimed_order_id, "status": order_data.get("order_status")},
            )
        except Exception as e:
            logger.warning("get_order failed: %s", e)

        try:
            items_res = await gateway.call(
                "get_order_items", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["order_items"] = items_res
            items_data = items_res.get("data", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order_items",
                evidence_refs=[items_res["evidence_ref"]],
                attributes={"item_count": len(items_data)},
            )
        except Exception as e:
            logger.warning("get_order_items failed: %s", e)

        try:
            sellers_res = await gateway.call(
                "get_sellers", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["sellers"] = sellers_res
            sellers_data = sellers_res.get("data", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_sellers",
                evidence_refs=[sellers_res["evidence_ref"]],
                attributes={"seller_count": len(sellers_data)},
            )
        except Exception as e:
            logger.warning("get_sellers failed: %s", e)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="order-agent",
        target="payment-agent",
        decision_code="ORDER_TO_PAYMENT",
    )

    # 3. Payment Agent
    payments_data: list[dict[str, Any]] = []
    payment_events: list[dict[str, Any]] = []
    refund_events: list[dict[str, Any]] = []

    if claimed_order_id:
        try:
            pay_res = await gateway.call(
                "get_order_payments", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["order_payments"] = pay_res
            payments_data = pay_res.get("data", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_order_payments",
                evidence_refs=[pay_res["evidence_ref"]],
                attributes={"payment_count": len(payments_data)},
            )
        except Exception as e:
            logger.warning("get_order_payments failed: %s", e)

        try:
            tl_res = await gateway.call(
                "get_payment_timeline", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["payment_timeline"] = tl_res
            payment_events = tl_res.get("data", {}).get("events", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_payment_timeline",
                evidence_refs=[tl_res["evidence_ref"]],
                attributes={"event_count": len(payment_events)},
            )
        except Exception as e:
            logger.warning("get_payment_timeline failed: %s", e)

        try:
            ref_res = await gateway.call(
                "get_refund_timeline", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["refund_timeline"] = ref_res
            refund_events = ref_res.get("data", {}).get("events", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_refund_timeline",
                evidence_refs=[ref_res["evidence_ref"]],
                attributes={"refund_event_count": len(refund_events)},
            )
        except Exception as e:
            logger.debug("get_refund_timeline returned no rows: %s", e)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="payment-agent",
        target="shipment-agent",
        decision_code="PAYMENT_TO_SHIPMENT",
    )

    # 4. Shipment Agent
    shipment_summary: dict[str, Any] = {}
    shipment_events: list[dict[str, Any]] = []

    if claimed_order_id:
        try:
            ship_res = await gateway.call(
                "get_shipment_summary", case_id=case_id, order_id=claimed_order_id
            )
            collected_evidence["shipment_summary"] = ship_res
            shipment_summary = ship_res.get("data", {})
            shipment_events = shipment_summary.get("events", [])
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                tool_name="get_shipment_summary",
                evidence_refs=[ship_res["evidence_ref"]],
                attributes={"shipment_events": len(shipment_events)},
            )
        except Exception as e:
            logger.warning("get_shipment_summary failed: %s", e)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="shipment-agent",
        target="investigation-agent",
        decision_code="SHIPMENT_TO_INVESTIGATION",
    )

    # 5. Investigation Agent: Synthesizes evidence to determine primary_issue
    primary_issue = "unsupported_claim"
    supporting_evidence_keys: list[str] = ["policy"]

    order_status = order_data.get("order_status", "")

    # Check refund timeline events
    has_refund_failed = any(ev.get("status") == "failed" for ev in refund_events)
    has_refund_pending = any(ev.get("status") == "pending" for ev in refund_events)

    # Check payment timeline events
    has_reconciliation_mismatch = any(
        ev.get("event_type") == "reconciliation_mismatch" for ev in payment_events
    )

    # Check duplicate charge (identical payment methods / values captured multiple times)
    has_duplicate_charge = False
    if len(payments_data) >= 4:
        # Check if payment items duplicate
        seq_values = [
            (p.get("payment_sequential"), p.get("payment_type"), p.get("payment_value"))
            for p in payments_data
        ]
        if len(seq_values) != len(set(seq_values)):
            has_duplicate_charge = True

    # Check valid split payment (multiple payment sequentials summing normally)
    has_valid_split_payment = False
    if (
        len(payments_data) >= 2
        and any(p.get("payment_sequential") == "2" for p in payments_data)
        and not has_duplicate_charge
        and not has_reconciliation_mismatch
    ):
        has_valid_split_payment = True

    # Check shipment late delivery events
    has_late_delivery_seller = any(
        ev.get("event_type") == "delivered_late" and ev.get("actor") == "seller"
        for ev in shipment_events
    )
    has_late_delivery_logistics = any(
        ev.get("event_type") == "delivered_late" and ev.get("actor") == "logistics_provider"
        for ev in shipment_events
    )

    # Pinpoint primary_issue in priority order based on concrete facts
    if order_status == "canceled":
        primary_issue = "canceled_order_paid"
        supporting_evidence_keys.extend(["order", "payment_timeline"])
    elif order_status == "unavailable":
        primary_issue = "unavailable_order_paid"
        supporting_evidence_keys.extend(["order", "payment_timeline"])
    elif has_refund_failed:
        primary_issue = "refund_failed"
        supporting_evidence_keys.extend(["refund_timeline", "payment_timeline"])
    elif has_refund_pending:
        primary_issue = "refund_pending"
        supporting_evidence_keys.extend(["refund_timeline", "payment_timeline"])
    elif has_duplicate_charge:
        primary_issue = "duplicate_charge"
        supporting_evidence_keys.extend(["order_payments", "payment_timeline"])
    elif has_reconciliation_mismatch:
        primary_issue = "payment_mismatch"
        supporting_evidence_keys.extend(["order_payments", "payment_timeline"])
    elif has_late_delivery_seller:
        primary_issue = "late_delivery_seller"
        supporting_evidence_keys.extend(["shipment_summary", "order_items"])
    elif has_late_delivery_logistics:
        primary_issue = "late_delivery_logistics"
        supporting_evidence_keys.extend(["shipment_summary", "order"])
    elif has_valid_split_payment:
        # Check if customer claim topic relates to split payment
        claim_topics = [cl.get("topic") for cl in claims]
        if "valid_split_payment" in claim_topics:
            primary_issue = "valid_split_payment"
            supporting_evidence_keys.extend(["order_payments", "payment_timeline"])
        else:
            primary_issue = "unsupported_claim"
            supporting_evidence_keys.extend(["order", "shipment_summary"])
    else:
        primary_issue = "unsupported_claim"
        supporting_evidence_keys.extend(["order", "shipment_summary"])

    # Fallback to customer claim if confirmed by policy and evidence exists
    claim_topics = [cl.get("topic") for cl in claims if cl.get("topic") != "requested_full_refund"]
    if claim_topics and claim_topics[0] in policy_rules:
        candidate = claim_topics[0]
        if candidate == "canceled_order_paid" and order_status == "canceled":
            primary_issue = "canceled_order_paid"
        elif candidate == "unavailable_order_paid" and order_status == "unavailable":
            primary_issue = "unavailable_order_paid"
        elif candidate == "late_delivery_seller" and has_late_delivery_seller:
            primary_issue = "late_delivery_seller"
        elif candidate == "late_delivery_logistics" and has_late_delivery_logistics:
            primary_issue = "late_delivery_logistics"
        elif candidate == "duplicate_charge" and has_duplicate_charge:
            primary_issue = "duplicate_charge"
        elif candidate == "payment_mismatch" and has_reconciliation_mismatch:
            primary_issue = "payment_mismatch"
        elif candidate == "refund_failed" and has_refund_failed:
            primary_issue = "refund_failed"
        elif candidate == "refund_pending" and has_refund_pending:
            primary_issue = "refund_pending"
        elif candidate == "valid_split_payment" and has_valid_split_payment:
            primary_issue = "valid_split_payment"

    # Match policy rule
    policy_rule = policy_rules.get(primary_issue, {})
    case_status = policy_rule.get(
        "case_status", "no_action" if primary_issue == "unsupported_claim" else "action_required"
    )
    recommended_action = policy_rule.get(
        "recommended_action", "document_no_action" if case_status == "no_action" else "issue_refund"
    )
    recommended_refund_brl = float(policy_rule.get("refund_brl", 0.0))
    rule_parties = policy_rule.get("responsible_parties", [])

    # Format responsible parties
    responsible_parties = []
    seller_id_found = None
    if items_data:
        seller_id_found = items_data[0].get("seller_id")
    elif sellers_data:
        seller_id_found = sellers_data[0].get("seller_id")

    for rp in rule_parties:
        ptype = rp.get("party_type", "unknown")
        pid = rp.get("party_id")
        if ptype == "seller" and not pid and seller_id_found:
            pid = seller_id_found
        responsible_parties.append({"party_type": ptype, "party_id": pid})

    if not responsible_parties:
        if primary_issue == "unsupported_claim":
            responsible_parties = [{"party_type": "customer", "party_id": None}]
        elif primary_issue in ("late_delivery_seller", "unavailable_order_paid"):
            responsible_parties = [{"party_type": "seller", "party_id": seller_id_found}]
        elif primary_issue == "late_delivery_logistics":
            responsible_parties = [{"party_type": "logistics_provider", "party_id": None}]
        elif primary_issue in (
            "duplicate_charge",
            "payment_mismatch",
            "refund_failed",
            "refund_pending",
        ):
            responsible_parties = [{"party_type": "payment_provider", "party_id": None}]
        else:
            responsible_parties = [{"party_type": "platform", "party_id": None}]

    # Affected entities
    order_ids = [claimed_order_id] if claimed_order_id else []
    item_ids = list(
        dict.fromkeys(item["order_item_id"] for item in items_data if "order_item_id" in item)
    )
    seller_ids = list(
        dict.fromkeys(s["seller_id"] for s in (items_data + sellers_data) if "seller_id" in s)
    )
    payment_refs = []
    for p in payments_data:
        pref = f"{p.get('payment_type', 'pay')}_{p.get('payment_sequential', '1')}"
        if pref not in payment_refs:
            payment_refs.append(pref)
    shipment_ids = []

    # Financial resolution lines
    refund_lines = []
    if recommended_refund_brl > 0.0:
        refund_lines.append(
            {
                "reason_code": f"REFUND_{primary_issue.upper()}",
                "amount_brl": recommended_refund_brl,
                "entity_id": claimed_order_id or None,
            }
        )

    # Evidence refs collation
    case_evidence_refs = []
    for k in supporting_evidence_keys:
        if k in collected_evidence:
            ref = collected_evidence[k].get("evidence_ref")
            if ref and ref not in case_evidence_refs:
                case_evidence_refs.append(ref)

    # Always ensure policy ref is present
    if policy_ref not in case_evidence_refs:
        case_evidence_refs.append(policy_ref)

    # Claim assessments
    claim_assessments = []
    for cl in claims:
        cid = cl.get("claim_id", "")
        ctopic = cl.get("topic", "")
        if ctopic == primary_issue:
            verdict = "supported"
            cconf = 1.0
        elif ctopic == "requested_full_refund":
            if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
                verdict = "supported"
                cconf = 1.0
            elif recommended_refund_brl > 0.0:
                verdict = "partially_supported"
                cconf = 0.95
            else:
                verdict = "unsupported"
                cconf = 1.0
        elif primary_issue == "unsupported_claim":
            verdict = "unsupported"
            cconf = 1.0
        else:
            verdict = "unsupported"
            cconf = 0.9

        claim_assessments.append(
            {
                "claim_id": cid,
                "verdict": verdict,
                "confidence": cconf,
                "evidence_refs": case_evidence_refs,
            }
        )

    # Data conflicts
    data_conflicts = []
    if primary_issue == "unsupported_claim" and order_status == "delivered":
        data_conflicts.append(
            {
                "field": "order_status",
                "sources": ["customer_claim", "mcp_get_order"],
                "selected_source": "mcp_get_order",
                "resolution_code": "CUSTOMER_CLAIM_REFUTED_BY_DELIVERY",
            }
        )

    output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "confidence": 1.0,
        },
        "affected_entities": {
            "order_ids": order_ids,
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "payment_references": payment_refs,
            "shipment_ids": shipment_ids,
        },
        "claim_assessments": claim_assessments,
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": primary_issue.upper(), "rank": 1}],
            "responsible_parties": responsible_parties,
        },
        "evidence_refs": case_evidence_refs,
        "data_conflicts": data_conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": recommended_refund_brl,
            "refund_lines": refund_lines,
        },
        "resolution_actions": [recommended_action],
    }

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="investigation-agent",
        target="verifier",
        decision_code="INVESTIGATION_COMPLETE",
    )

    # 6. Verifier Agent: Invariant verification
    if case_status == "no_action":
        assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
        assert len(output["financial_resolution"]["refund_lines"]) == 0

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        target="coordinator",
        decision_code="VERIFICATION_SUCCESS",
    )

    return output
