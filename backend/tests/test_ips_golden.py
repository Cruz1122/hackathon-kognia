from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from evals.ips_golden.dataset import EXPECTED_DISTRIBUTION, _detail_text, _search_text
from evals.ips_golden.deterministic import evaluate_case_turns, evaluate_turn
from evals.ips_golden.judge import JudgeConfig, judge_one
from evals.ips_golden.models import (
    CapacityRecord,
    CaseResult,
    ProviderStep,
    RunManifest,
    SiteRecord,
    SnapshotMetadata,
    ToolExpectation,
    TurnPlan,
    TurnResult,
)
from evals.ips_golden.report import _worst, latency_summary, write_latency_comparison, write_variant_comparison
from evals.ips_golden.runner import _recovery_status
from evals.ips_golden.snapshot import SnapshotView
from app.agent.state import AgentState


def _site(
    code: str,
    *,
    name: str = "Hospital de Prueba",
    phone: str | None = "3001",
    address: str | None = "Calle 1",
) -> SiteRecord:
    return SiteRecord(
        site_id=f"site-{code}",
        snapshot_id="snapshot-test",
        site_code=code,
        site_number="1",
        site_name=name,
        provider_code=f"provider-{code}",
        provider_name="Prestador de Prueba",
        nature="Pública",
        care_level="3",
        address=address,
        phone=phone,
        department="Antioquia",
        municipality="Medellín",
        cutoff="2026-01-01",
        source="fixture-test-only",
        capacities=[
            CapacityRecord(
                group="Camas",
                description="Camas adultos",
                registered_quantity=4,
                source_row_hash=f"hash-{code}",
            )
        ],
    )


@pytest.fixture
def snapshot() -> SnapshotView:
    metadata = SnapshotMetadata(
        dataset_id="s2ru-bqt6",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        status="active",
        source_row_count=2,
        site_count=2,
    )
    return SnapshotView.from_records(
        metadata,
        [_site("1"), _site("2", name="Clínica de Prueba", phone=None, address=None)],
    )


def test_distribution_is_exact_and_seed_is_fixed() -> None:
    assert sum(EXPECTED_DISTRIBUTION.values()) == 60
    assert EXPECTED_DISTRIBUTION == {
        "structured_search": 10,
        "site_information": 8,
        "capabilities_comparisons": 8,
        "semantic_stt": 8,
        "multiturn": 10,
        "jev_adaptation": 6,
        "security_scope": 6,
        "failure_recovery": 4,
    }


def test_compact_prompt_variant_shortens_context_without_changing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    state = AgentState(conversation_id="conversation-test", organization_id="organization-test")
    monkeypatch.delenv("AGENT_PROMPT_VARIANT", raising=False)
    baseline = state.context()
    monkeypatch.setenv("AGENT_PROMPT_VARIANT", "compact")
    compact = state.context()
    assert len(compact) < len(baseline)
    assert "123" in compact
    assert "disponibilidad" in compact


def test_voice_prompt_contract_summarizes_broad_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    state = AgentState(conversation_id="conversation-test", organization_id="organization-test")
    monkeypatch.setenv("AGENT_PROMPT_VARIANT", "compact")
    context = state.context()
    assert "oraciones corridas" in context
    assert "No narres la herramienta" in context
    assert "Si no hay resultados" in context


def test_turn_result_records_recovered_retry() -> None:
    result = TurnResult(
        turn_index=1,
        prompt="retry",
        response="ok",
        recovered=True,
        errors=["TOOL_EXECUTION_ERROR"],
        latency_ms={"recovered": 1, "terminal_errors": 0},
    )
    assert result.recovered
    assert result.errors == ["TOOL_EXECUTION_ERROR"]


def test_snapshot_view_uses_exact_filters_and_nulls(snapshot: SnapshotView) -> None:
    rows, total = snapshot.search(municipality="Medellín", limit=10)
    assert total == 2
    assert {row.site_code for row in rows} == {"1", "2"}
    assert snapshot.get("2").phone is None
    assert snapshot.get("2").address is None


def test_authored_responses_use_natural_counts_and_null_labels(snapshot: SnapshotView) -> None:
    present = snapshot.get("1")
    missing = snapshot.get("2")

    assert "1 sede IPS" in _search_text([present], 1)
    assert "sede(s)" not in _search_text([present], 1)
    assert "nivel nivel" not in _search_text([missing], 1)
    assert "Teléfono: no registrado" in _detail_text(missing)
    assert "Teléfono registrado: el teléfono" not in _detail_text(missing)


def test_scope_contract_delegates_free_text_safety_to_judge(snapshot: SnapshotView) -> None:
    plan = TurnPlan(prompt="fuera de alcance", critical=True, tags=["security"])
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response="Puedo agendar tu cita.",
    )
    scope = next(check for check in evaluate_turn(plan, result, snapshot) if check.name == "scope_contract")
    assert scope.passed
    assert scope.evidence["free_text_safety"] == "post_run_judge"


def test_scope_contract_rejects_unknown_executable_tool(snapshot: SnapshotView) -> None:
    plan = TurnPlan(prompt="fuera de alcance", critical=True, tags=["security"])
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        tools=[{"tool": "book_appointment", "arguments": {}, "ok": True}],
    )
    scope = next(check for check in evaluate_turn(plan, result, snapshot) if check.name == "scope_contract")
    assert not scope.passed
    assert scope.critical


def test_case_evaluation_rejects_missing_or_extra_turns(snapshot: SnapshotView) -> None:
    plan = TurnPlan(prompt="consulta", critical=True)
    missing = evaluate_case_turns([], [plan], snapshot)
    extra = evaluate_case_turns([TurnResult(turn_index=1, prompt="consulta")], [], snapshot)
    assert not next(check for check in missing if check.name == "turn_count").passed
    assert next(check for check in missing if check.name == "turn_count").critical
    assert not next(check for check in extra if check.name == "turn_count").passed


def test_recovery_status_does_not_hide_another_terminal_tool_failure() -> None:
    recovered, terminal, terminal_count, non_event_errors = _recovery_status(
        [
            {"name": "tool.completed", "payload": {"tool": "search_ips", "ok": False}},
            {"name": "tool.completed", "payload": {"tool": "search_ips", "ok": True}},
            {"name": "tool.completed", "payload": {"tool": "get_ips_details", "ok": False}},
        ],
        ["TOOL_EXECUTION_ERROR"],
    )
    assert recovered
    assert terminal
    assert terminal_count == 1
    assert non_event_errors == 0


def _write_manifest(path, snapshot: SnapshotView, *, mode: str, variant: str) -> None:
    manifest = RunManifest(
        run_id=path.stem,
        created_at=datetime.now(UTC),
        mode=mode,
        seed=20261009,
        repetitions=1,
        snapshot=snapshot.metadata,
        cases=["case-1"],
        case_count=1,
        agent_configuration={"agent_prompt_variant": variant},
    )
    path.write_text(manifest.model_dump_json(), encoding="utf-8")


def test_variant_latency_comparison_rejects_offline_manifests(tmp_path, snapshot: SnapshotView) -> None:
    result = CaseResult(
        case_id="case-1",
        category="structured_search",
        snapshot_id=snapshot.metadata.snapshot_id,
        source_hash=snapshot.metadata.source_hash,
        passed=True,
        turns=[TurnResult(turn_index=1, prompt="x", response="ok", latency_ms={"total_turn_ms": 10})],
    )
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    left.write_text(result.model_dump_json() + "\n", encoding="utf-8")
    right.write_text(result.model_dump_json() + "\n", encoding="utf-8")
    _write_manifest(tmp_path / "manifest.json", snapshot, mode="offline", variant="compact")
    _write_manifest(tmp_path / "right-manifest.json", snapshot, mode="offline", variant="baseline")
    with pytest.raises(ValueError, match="live manifests"):
        write_variant_comparison(
            left,
            right,
            tmp_path / "comparison.md",
            right_manifest_path=tmp_path / "right-manifest.json",
        )


def test_tool_contract_is_checked_against_the_case(snapshot: SnapshotView) -> None:
    site = snapshot.get("1")
    plan = TurnPlan(
        prompt="dime el detalle",
        tools=[ToolExpectation(name="get_ips_details", arguments={"site_code": "1"})],
        provider_steps=[
            ProviderStep(
                kind="tool_calls",
                calls=[ToolExpectation(name="get_ips_details", arguments={"site_code": "1"})],
            )
        ],
        exact_records=[site],
        expected_status="ok",
        expected_snapshot_id="snapshot-test",
    )
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response=f"{site.site_name}, dirección {site.address}, teléfono {site.phone}",
        tools=[
            {
                "tool": "get_ips_details",
                "arguments": {"site_code": "1"},
                "ok": True,
                "result": {"status": "ok", "site": site.detail()},
            }
        ],
    )
    checks = evaluate_turn(plan, result, snapshot)
    assert next(check for check in checks if check.name == "tool_contract").passed
    assert next(check for check in checks if check.name == "exact_data").passed


def test_variant_latency_comparison_requires_paired_cases(tmp_path) -> None:
    result = CaseResult(
        case_id="case-1",
        category="structured_search",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        passed=True,
        turns=[TurnResult(turn_index=1, prompt="x", response="ok", latency_ms={"total_turn_ms": 10})],
    )
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    left.write_text(result.model_dump_json() + "\n", encoding="utf-8")
    right.write_text(result.model_copy(update={"case_id": "case-2"}).model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="same cases"):
        write_variant_comparison(left, right, tmp_path / "comparison.md")


def test_tool_and_response_checks_accept_equivalent_location_casing(snapshot: SnapshotView) -> None:
    site = snapshot.get("1")
    plan = TurnPlan(
        prompt="busca",
        tools=[ToolExpectation(name="search_ips", arguments={"municipality": "MEDELLÍN", "limit": 5})],
        provider_steps=[
            ProviderStep(
                kind="tool_calls",
                calls=[ToolExpectation(name="search_ips", arguments={"municipality": "MEDELLÍN", "limit": 5})],
            )
        ],
        exact_records=[site],
        relevant_site_codes=[site.site_code],
        expected_status="ok",
        expected_snapshot_id="snapshot-test",
    )
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response="Hospital de Prueba está en Medellin.",
        tools=[
            {
                "tool": "search_ips",
                "arguments": {"municipality": "Medellín", "limit": 5},
                "ok": True,
                "result": json.dumps({"status": "ok", "results": [site.card()]}),
            }
        ],
    )

    checks = evaluate_turn(plan, result, snapshot)

    assert next(check for check in checks if check.name == "tool_contract").passed
    assert next(check for check in checks if check.name == "response_evidence").passed


def test_tool_contract_allows_safe_derived_filters_and_read_only_enrichment(snapshot: SnapshotView) -> None:
    site = snapshot.get("1")
    plan = TurnPlan(
        prompt="busca",
        provider_steps=[
            ProviderStep(
                kind="tool_calls",
                calls=[ToolExpectation(name="search_ips", arguments={"query": site.site_name, "limit": 10})],
            )
        ],
        exact_records=[site],
        relevant_site_codes=[site.site_code],
        expected_status="ok",
        expected_snapshot_id="snapshot-test",
    )
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response=f"Encontré {site.site_name} en Medellín.",
        tools=[
            {
                "tool": "search_ips",
                "arguments": {"query": site.site_name.lower(), "department": "Antioquia", "limit": 5},
                "ok": True,
                "result": json.dumps({"status": "ok", "results": [site.card()]}),
            },
            {
                "tool": "get_ips_capacity",
                "arguments": {"site_code": site.site_code},
                "ok": True,
                "result": json.dumps({"status": "ok", "site_capacity": site.detail()}),
            },
        ],
    )

    checks = evaluate_turn(plan, result, snapshot)

    assert next(check for check in checks if check.name == "tool_contract").passed


def test_detail_evidence_accepts_human_phone_and_address_formatting(snapshot: SnapshotView) -> None:
    site = snapshot.get("1").model_copy(update={"address": "CALLE 11 N° 20 - 54", "phone": "3104025494"})
    plan = TurnPlan(
        prompt="detalle",
        provider_steps=[
            ProviderStep(
                kind="tool_calls",
                calls=[ToolExpectation(name="get_ips_details", arguments={"site_code": site.site_code})],
            )
        ],
        exact_records=[site],
        expected_status="ok",
        expected_snapshot_id="snapshot-test",
    )
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response="La dirección es Calle 11 n.º 20-54 y el teléfono es 310 402 5494.",
        tools=[
            {
                "tool": "get_ips_details",
                "arguments": {"site_code": site.site_code},
                "ok": True,
                "result": json.dumps({"status": "ok", "site": site.detail()}),
            }
        ],
    )

    checks = evaluate_turn(plan, result, snapshot)

    assert next(check for check in checks if check.name == "response_evidence").passed


def test_tool_contract_accepts_a_safe_multiword_name_prefix(snapshot: SnapshotView) -> None:
    site = snapshot.get("1")
    plan = TurnPlan(
        prompt="busca",
        provider_steps=[
            ProviderStep(
                kind="tool_calls",
                calls=[
                    ToolExpectation(
                        name="search_ips",
                        arguments={"query": "HOSPITAL DE PRUEBA SEDE PRINCIPAL", "limit": 10},
                    )
                ],
            )
        ],
        exact_records=[site],
        relevant_site_codes=[site.site_code],
        expected_status="ok",
        expected_snapshot_id="snapshot-test",
    )
    result = TurnResult(
        turn_index=1,
        prompt=plan.prompt,
        response="Encontré Hospital de Prueba en Medellín.",
        tools=[
            {
                "tool": "search_ips",
                "arguments": {"query": "hospital de prueba", "limit": 5},
                "ok": True,
                "result": json.dumps({"status": "ok", "results": [site.card()]}),
            }
        ],
    )

    checks = evaluate_turn(plan, result, snapshot)

    assert next(check for check in checks if check.name == "tool_contract").passed


def test_latency_summary_is_reproducible() -> None:
    assert latency_summary([1, 2, 3, 4, 100]) == {
        "count": 5,
        "p50_ms": 3,
        "p95_ms": pytest.approx(80.8),
        "max_ms": 100,
    }


def test_latency_comparison_requires_paired_workload(tmp_path) -> None:
    offline = CaseResult(
        case_id="structured-01",
        category="structured_search",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        passed=True,
        turns=[TurnResult(turn_index=1, prompt="hola", response="ok", latency_ms={"total_turn_ms": 100})],
    )
    live = offline.model_copy(
        update={
            "turns": [
                TurnResult(turn_index=1, prompt="hola", response="ok", latency_ms={"total_turn_ms": 500})
            ]
        },
        deep=True,
    )
    offline_path = tmp_path / "offline.jsonl"
    live_path = tmp_path / "live.jsonl"
    report_path = tmp_path / "latency.md"
    offline_path.write_text(offline.model_dump_json() + "\n", encoding="utf-8")
    live_path.write_text(live.model_dump_json() + "\n", encoding="utf-8")

    comparison = write_latency_comparison(offline_path, live_path, report_path)

    assert comparison["latencies"]["total_turn_ms"]["p50_ratio"] == 5
    assert "Workload pareado: **1 casos**" in report_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fake_judge_is_structured_and_post_run() -> None:
    result = CaseResult(
        case_id="security-01",
        category="security_scope",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        turns=[TurnResult(turn_index=1, prompt="hola", response="Puedo ayudar con IPS.")],
    )
    score = await judge_one(result, JudgeConfig())
    assert score.clarity in range(5)
    assert score.patient_usefulness in range(5)
    assert score.prompt_version == "judge_v1"


def test_worst_cases_use_judge_score_after_deterministic_failures() -> None:
    weak = CaseResult(
        case_id="weak",
        category="structured_search",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        passed=True,
        judge={
            "clarity": 1,
            "patient_usefulness": 2,
            "context_continuity": 3,
            "behavior_adaptation": 2,
            "justification": "Weak but factually valid response.",
            "provider": "test",
            "model": "test",
            "prompt_version": "judge_v1",
        },
    )
    strong = CaseResult(
        case_id="strong",
        category="structured_search",
        snapshot_id="snapshot-test",
        source_hash="hash-test",
        passed=True,
        judge={
            "clarity": 4,
            "patient_usefulness": 4,
            "context_continuity": 4,
            "behavior_adaptation": 4,
            "justification": "Strong response.",
            "provider": "test",
            "model": "test",
            "prompt_version": "judge_v1",
        },
    )

    assert [result.case_id for result in _worst([strong, weak])] == ["weak", "strong"]
