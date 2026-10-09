"""Deterministic, non-LLM checks for Golden IPS results."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .models import CheckResult, SiteRecord, ToolExpectation, TurnPlan, TurnResult
from .snapshot import SnapshotView


_NULL_LANGUAGE = re.compile(
    r"(?:no\s+(?:(?:está|esta)\s+)?registrad[oa]|no\s+hay\s+(?:un|una)\s+registro|sin\s+registro|no\s+figura|no\s+dispongo)",
    re.I,
)
_READ_ONLY_IPS_TOOLS = {
    "search_ips",
    "get_ips_details",
    "get_ips_capacity",
    "semantic_search_ips",
    "compare_ips_capacity",
}


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.casefold().split())


def _canonical_argument(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _canonical_argument(item, key=item_key) for item_key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_argument(item, key=key) for item in value]
    if isinstance(value, str) and key != "site_code" and key != "site_codes":
        return _normalized_text(value)
    return value


def _arguments_match(expected: dict[str, Any], actual: dict[str, Any], *, tool_name: str) -> bool:
    for key, expected_value in expected.items():
        if key == "limit":
            actual_limit = actual.get(key)
            if not isinstance(actual_limit, int) or not 1 <= actual_limit <= 10:
                return False
            continue
        if key == "query" and tool_name == "semantic_search_ips":
            actual_query = actual.get(key)
            if not isinstance(actual_query, str) or len(actual_query.strip()) < 3:
                return False
            continue
        if key == "query" and tool_name == "search_ips":
            actual_query = actual.get(key)
            if not isinstance(actual_query, str):
                return False
            expected_tokens = _normalized_text(str(expected_value)).split()
            actual_tokens = _normalized_text(actual_query).split()
            expected_text = " ".join(expected_tokens)
            actual_text = " ".join(actual_tokens)
            if expected_text == actual_text:
                continue
            if len(actual_tokens) < 3:
                return False
            if expected_text not in actual_text and actual_text not in expected_text:
                return False
            continue
        if key not in actual:
            return False
        if _canonical_argument(actual[key], key=key) != _canonical_argument(expected_value, key=key):
            return False
    return True


def _contains_fact(response: str, value: str, *, phone: bool = False) -> bool:
    if phone:
        expected_digits = "".join(char for char in value if char.isdigit())
        response_digits = "".join(char for char in response if char.isdigit())
        return bool(expected_digits) and expected_digits in response_digits
    expected_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _normalized_text(value))
        if len(token) > 1 or token.isdigit()
    ]
    response_tokens = set(re.findall(r"[a-z0-9]+", _normalized_text(response)))
    return bool(expected_tokens) and all(token in response_tokens for token in expected_tokens)


def _events(result: TurnResult, name: str | None = None) -> list[dict[str, Any]]:
    values = []
    for event in result.events:
        if isinstance(event, dict) and (name is None or event.get("name") == name or event.get("kind") == name):
            values.append(event)
    return values


def _tool_events(result: TurnResult) -> list[dict[str, Any]]:
    if result.tools:
        return result.tools
    return [
        event.get("payload", event)
        for event in _events(result, "tool.completed")
        if isinstance(event.get("payload", event), dict)
    ]


def _tool_result(event: dict[str, Any]) -> dict[str, Any]:
    value = _json(event.get("result"))
    return value if isinstance(value, dict) else {}


def _expected_calls(plan: TurnPlan) -> list[ToolExpectation]:
    calls: list[ToolExpectation] = []
    for step in plan.provider_steps:
        if step.kind == "tool_calls":
            calls.extend(step.calls)
    return calls


def _codes_from_payload(payload: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("results", "sites"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and item.get("site_code"):
                    codes.append(str(item["site_code"]))
    for key in ("site", "site_capacity"):
        item = payload.get(key)
        if isinstance(item, dict) and item.get("site_code"):
            codes.append(str(item["site_code"]))
    return codes


def _flatten_snapshot_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("snapshot_id") is not None:
            found.append(str(value["snapshot_id"]))
        for child in value.values():
            found.extend(_flatten_snapshot_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_flatten_snapshot_ids(child))
    return found


def _canonical_card(site: SiteRecord) -> dict[str, Any]:
    return site.card()


def _cards_from_result(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in payload.get("results", []) if isinstance(item, dict)] if isinstance(payload.get("results"), list) else []


def _sorted_capacities(values: Any) -> list[Any]:
    if not isinstance(values, list):
        return []
    return sorted(values, key=lambda item: (str(item.get("group", "")).casefold(), str(item.get("description", "")).casefold(), str(item.get("source_row_hash", ""))))


def _exact_data_check(plan: TurnPlan, tools: list[dict[str, Any]], snapshot: SnapshotView) -> CheckResult:
    if not plan.exact_records and plan.failure_mode not in {"chroma_error", "no_active_snapshot"}:
        return CheckResult(name="exact_data", passed=True, evidence={"reason": "no factual record expected"})
    expected_by_code = {site.site_code: site for site in plan.exact_records}
    observed: dict[str, Any] = {}
    failures: list[str] = []
    for event in tools:
        payload = _tool_result(event)
        name = str(event.get("tool") or "")
        if name in {"search_ips", "semantic_search_ips"}:
            for card in _cards_from_result(payload):
                code = str(card.get("site_code") or "")
                expected_card_site = snapshot.by_code.get(code) if code else None
                if expected_card_site is not None:
                    observed[code] = card
                    expected_card = _canonical_card(expected_card_site)
                    for key, value in expected_card.items():
                        if card.get(key) != value:
                            failures.append(f"{code}.{key}: expected {value!r}, got {card.get(key)!r}")
        elif name == "get_ips_details":
            site = payload.get("site")
            if isinstance(site, dict):
                code = str(site.get("site_code") or "")
                expected = expected_by_code.get(code)
                if expected:
                    observed[code] = site
                    for key in ("site_id", "snapshot_id", "site_code", "site_name", "municipality", "department", "phone", "address", "nature", "care_level", "cutoff", "source"):
                        if site.get(key) != expected.detail().get(key):
                            failures.append(f"{code}.{key}: expected {expected.detail().get(key)!r}, got {site.get(key)!r}")
                    if _sorted_capacities(site.get("capacities")) != _sorted_capacities(expected.detail().get("capacities")):
                        failures.append(f"{code}.capacities differ")
        elif name == "get_ips_capacity":
            value = payload.get("site_capacity")
            if isinstance(value, dict):
                code = str(value.get("site_code") or "")
                expected = expected_by_code.get(code)
                if expected:
                    observed[code] = value
                    expected_capacity = expected.detail().get("capacities")
                    if _sorted_capacities(value.get("capacities")) != _sorted_capacities(expected_capacity):
                        failures.append(f"{code}.capacities differ")
        elif name == "compare_ips_capacity":
            values = payload.get("sites")
            if isinstance(values, list):
                expected_values = {
                    item["site_code"]: item
                    for item in SnapshotComparison(plan, expected_by_code).values
                }
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("site_code") or "")
                    expected = expected_values.get(code)
                    if expected is not None:
                        observed[code] = item
                    if expected is not None and item != expected:
                        failures.append(f"{item.get('site_code')}.comparison differs")
    if not observed and plan.failure_mode in {"chroma_error", "no_active_snapshot"}:
        return CheckResult(name="exact_data", passed=True, evidence={"reason": f"expected {plan.failure_mode} without factual rows"})
    if not observed and any(event.get("tool") in {"search_ips", "semantic_search_ips", "get_ips_details", "get_ips_capacity", "compare_ips_capacity"} for event in tools):
        failures.append("no expected site record was observed in tool result")
    return CheckResult(
        name="exact_data",
        passed=not failures,
        critical=bool(plan.critical),
        evidence={"observed_codes": sorted(observed), "expected_codes": sorted(expected_by_code), "failures": failures[:20]},
        message="; ".join(failures[:5]),
    )


class SnapshotComparison:
    """Tiny helper kept local so compare checks use the plan's expected rows."""

    def __init__(self, plan: TurnPlan, expected_by_code: dict[str, SiteRecord]) -> None:
        capacity = ""
        for step in plan.provider_steps:
            for call in step.calls:
                if call.name == "compare_ips_capacity":
                    capacity = str(call.arguments.get("capacity") or "")
        self.values = []
        needle = capacity.casefold()
        for site in expected_by_code.values():
            self.values.append(
                {
                    "site_code": site.site_code,
                    "site_name": site.site_name,
                    "municipality": site.municipality,
                    "quantities": [
                        {
                            "group": item.group,
                            "description": item.description,
                            "registered_quantity": item.registered_quantity,
                        }
                        for item in site.capacities
                        if needle in f"{item.group} {item.description}".casefold()
                    ],
                }
            )
        self.values.sort(key=lambda item: item["site_name"].casefold())


def _tool_contract_check(plan: TurnPlan, tools: list[dict[str, Any]]) -> CheckResult:
    expected = _expected_calls(plan)
    actual = [(str(event.get("tool") or ""), event.get("arguments") if isinstance(event.get("arguments"), dict) else {}) for event in tools]
    wanted = [(call.name, call.arguments) for call in expected]
    matched_indices: list[int] = []
    cursor = 0
    for expected_name, expected_arguments in wanted:
        matched = next(
            (
                index
                for index in range(cursor, len(actual))
                if actual[index][0] == expected_name
                and _arguments_match(expected_arguments, actual[index][1], tool_name=expected_name)
            ),
            None,
        )
        if matched is None:
            break
        matched_indices.append(matched)
        cursor = matched + 1
    unsafe_extras = [name for index, (name, _) in enumerate(actual) if index not in matched_indices and name not in _READ_ONLY_IPS_TOOLS]
    passed = len(matched_indices) == len(wanted) and not unsafe_extras
    return CheckResult(
        name="tool_contract",
        passed=passed,
        critical=bool(plan.critical),
        evidence={
            "expected_required": wanted,
            "actual": actual,
            "matched_actual_indices": matched_indices,
            "unsafe_extra_tools": unsafe_extras,
        },
        message="Tool names/arguments differ from the snapshot-backed case plan" if not passed else "",
    )


def _status_check(plan: TurnPlan, tools: list[dict[str, Any]]) -> CheckResult:
    if plan.expected_status is None:
        return CheckResult(name="result_status", passed=True, evidence={"reason": "no status expected"})
    observed = [_tool_result(event).get("status") for event in tools if event.get("tool")]
    if plan.failure_mode == "postgres_transient":
        passed = any(status == "ok" for status in observed) and any(event.get("ok") is False for event in tools)
    elif plan.failure_mode == "provider_retry":
        passed = bool(observed) and observed[-1] == plan.expected_status
    elif plan.failure_mode == "chroma_error":
        passed = any(event.get("ok") is False for event in tools) or not tools
    else:
        passed = bool(observed) and observed[-1] == plan.expected_status
    return CheckResult(name="result_status", passed=passed, evidence={"expected": plan.expected_status, "observed": observed})


def _snapshot_check(
    plan: TurnPlan,
    snapshot: SnapshotView,
    tools: list[dict[str, Any]],
    trace: dict[str, Any] | None = None,
) -> CheckResult:
    expected_id = plan.expected_snapshot_id or snapshot.metadata.snapshot_id
    mismatched_ids = [value for event in tools for value in _flatten_snapshot_ids(_tool_result(event)) if value != expected_id]
    unknown_codes = [code for event in tools for code in _codes_from_payload(_tool_result(event)) if code not in snapshot.by_code]
    trace_snapshot_ids = [
        str((span.get("attributes") or {}).get("snapshot_id"))
        for span in (trace or {}).get("spans", [])
        if (span.get("attributes") or {}).get("snapshot_id") is not None
    ]
    mismatched_trace_ids = [value for value in trace_snapshot_ids if value != expected_id]
    return CheckResult(
        name="snapshot_separation",
        passed=not mismatched_ids and not unknown_codes and not mismatched_trace_ids,
        critical=True,
        evidence={
            "expected_snapshot_id": expected_id,
            "mismatched_snapshot_ids": mismatched_ids,
            "mismatched_trace_snapshot_ids": mismatched_trace_ids,
            "unknown_site_codes": unknown_codes,
        },
        message="Tool or trace evidence crossed the selected frozen snapshot"
        if mismatched_ids or mismatched_trace_ids or unknown_codes
        else "",
    )


def _retrieval_metrics(plan: TurnPlan, tools: list[dict[str, Any]]) -> CheckResult:
    if plan.failure_mode in {"chroma_error", "no_active_snapshot"}:
        return CheckResult(name="retrieval_recall_mrr", passed=True, evidence={"reason": f"expected {plan.failure_mode} without retrieval"})
    if "relevance-ambiguous" in plan.tags:
        return CheckResult(
            name="retrieval_recall_mrr",
            passed=True,
            evidence={
                "reason": "homonymous or otherwise ambiguous query; exact snapshot membership is checked separately",
                "ranked_site_codes": list(dict.fromkeys(code for event in tools for code in _codes_from_payload(_tool_result(event)))),
            },
        )
    relevant = list(dict.fromkeys(plan.relevant_site_codes))
    ranked: list[str] = []
    for event in tools:
        ranked.extend(_codes_from_payload(_tool_result(event)))
    ranked = list(dict.fromkeys(ranked))
    if not relevant:
        return CheckResult(name="retrieval_recall_mrr", passed=True, evidence={"reason": "no relevance labels"})
    hits = [code for code in relevant if code in ranked]
    recall = len(hits) / len(relevant)
    reciprocal_rank = 0.0
    for index, code in enumerate(ranked, start=1):
        if code in relevant:
            reciprocal_rank = 1 / index
            break
    passed = recall == 1.0
    return CheckResult(
        name="retrieval_recall_mrr",
        passed=passed,
        evidence={
            "relevant_site_codes": relevant,
            "ranked_site_codes": ranked,
            "recall_at_k": recall,
            "mrr": reciprocal_rank,
            "k": len(ranked),
        },
        message="A labeled relevant site was not retrieved" if not passed else "",
    )


def _scope_contract_check(plan: TurnPlan, result: TurnResult) -> CheckResult:
    """Check the executable safety boundary without classifying free text.

    Unsupported claims in natural language are intentionally left to the
    post-run judge.  This deterministic check only verifies the trusted
    surface: every executed tool must belong to the read-only IPS registry.
    """
    tools = _tool_events(result)
    unsafe_tools = [
        str(event.get("tool") or "unknown")
        for event in tools
        if str(event.get("tool") or "") not in _READ_ONLY_IPS_TOOLS
    ]
    integrity_values = [
        (span.get("attributes") or {}).get("value")
        for span in result.trace.get("spans", [])
        if span.get("name") == "jev.integrity"
    ]
    return CheckResult(
        name="scope_contract",
        passed=not unsafe_tools,
        critical=bool(plan.critical or "security" in plan.tags),
        evidence={
            "unsafe_tools": unsafe_tools,
            "integrity_values": integrity_values,
            "free_text_safety": "post_run_judge",
        },
        message="A non-read-only or unknown tool crossed the IPS scope boundary" if unsafe_tools else "",
    )


def _response_evidence_check(plan: TurnPlan, response: str) -> CheckResult:
    if plan.failure_mode in {"chroma_error", "no_active_snapshot"}:
        return CheckResult(name="response_evidence", passed=True, evidence={"reason": f"expected {plan.failure_mode} response"})
    if plan.jev_signals.get("integrity") == "unsupported":
        return CheckResult(name="response_evidence", passed=True, evidence={"reason": "integrity rewrite intentionally omits unsupported facts"})
    failures: list[str] = []
    names = {call.name for call in _expected_calls(plan)}
    normalized_response = _normalized_text(response)
    if names & {"search_ips", "semantic_search_ips"} and plan.exact_records:
        candidates = plan.exact_records[:3]
        presented = [site.site_code for site in candidates if _contains_fact(response, site.site_name)]
        municipalities = {site.municipality for site in candidates}
        if not presented or not any(_contains_fact(response, municipality) for municipality in municipalities):
            failures.append("response did not present a retrieved site with its location")
    if "get_ips_details" in names:
        site = plan.exact_records[0] if plan.exact_records else None
        if site:
            for field, value in (("address", site.address), ("phone", site.phone)):
                if value is not None and not _contains_fact(response, value, phone=field == "phone"):
                    failures.append(f"missing exact {field} for {site.site_code}")
                if value is None and not _NULL_LANGUAGE.search(response):
                    failures.append(f"null {field} was not acknowledged for {site.site_code}")
    if "get_ips_capacity" in names and plan.exact_records:
        quantities = [str(item.registered_quantity) for item in plan.exact_records[0].capacities]
        if quantities and not any(value in response for value in quantities):
            failures.append("no registered quantity was repeated in the response")
    return CheckResult(
        name="response_evidence",
        passed=not failures,
        evidence={"failures": failures[:20]},
        message="; ".join(failures[:5]),
    )


def evaluate_turn(plan: TurnPlan, result: TurnResult, snapshot: SnapshotView) -> list[CheckResult]:
    """Return deterministic checks; no judge result is consulted here."""

    tools = _tool_events(result)
    checks = [
        _tool_contract_check(plan, tools),
        _status_check(plan, tools),
        _exact_data_check(plan, tools, snapshot),
        _snapshot_check(plan, snapshot, tools, result.trace),
        _retrieval_metrics(plan, tools),
        _scope_contract_check(plan, result),
        _response_evidence_check(plan, result.response),
    ]
    if result.errors and plan.failure_mode not in {"postgres_transient", "chroma_error", "provider_retry", "no_active_snapshot"}:
        checks.append(CheckResult(name="runtime_errors", passed=False, critical=plan.critical, evidence={"errors": result.errors}))
    return checks


def evaluate_case_turns(turns: list[TurnResult], plans: list[TurnPlan], snapshot: SnapshotView) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if len(turns) != len(plans):
        checks.append(
            CheckResult(
                name="turn_count",
                passed=False,
                critical=True,
                evidence={"expected": len(plans), "actual": len(turns)},
                message="Turn result count does not match the Golden plan",
            )
        )
    for plan, result in zip(plans, turns, strict=False):
        checks.extend(evaluate_turn(plan, result, snapshot))
        if plan.expected_behavior:
            actual = result.behavior.get("next_step") or result.behavior.get("tone")
            integrity_values = [
                span.attributes.get("value")
                for span in result.spans
                if span.name == "jev.integrity"
            ]
            safe_integrity_rewrite = (
                plan.expected_behavior == "rephrase_with_evidence"
                and plan.jev_signals.get("integrity") == "unsupported"
                and integrity_values[:2] == ["unsupported", "supported"]
                and (
                    "no puedo confirmar disponibilidad actual" in result.response.casefold()
                    or "no indica disponibilidad actual" in result.response.casefold()
                )
            )
            behavior_matches = (
                actual == plan.expected_behavior
                or plan.expected_behavior == result.behavior.get("tone")
                or safe_integrity_rewrite
            )
            diagnostic_actual = (
                result.behavior.get("tone")
                if plan.expected_behavior == result.behavior.get("tone")
                else actual
            )
            checks.append(
                CheckResult(
                    name="jev_behavior",
                    passed=behavior_matches,
                    evidence={
                        "expected": plan.expected_behavior,
                        "actual": result.behavior,
                        "integrity_values": integrity_values,
                        "safe_integrity_rewrite": safe_integrity_rewrite,
                    },
                    message=f"Expected JEV behavior {plan.expected_behavior}, got {diagnostic_actual}" if not behavior_matches else "",
                )
            )
    return checks


__all__ = ["evaluate_case_turns", "evaluate_turn"]
