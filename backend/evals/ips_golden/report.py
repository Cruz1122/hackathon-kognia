"""JSONL and Markdown reporting for IPS Golden runs."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import CaseResult, JudgeScore, RunManifest


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_results(path: Path) -> list[CaseResult]:
    return [CaseResult.model_validate(value) for value in read_jsonl(path)]


def _load_manifest(path: Path | None, results: list[CaseResult]) -> RunManifest | None:
    if path is not None and path.is_file():
        return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if not results:
        return None
    return None


def _all_turns(results: list[CaseResult]):
    for result in results:
        for turn in result.turns:
            yield result, turn
        if result.counterfactual:
            for turn in result.counterfactual.control:
                yield result, turn


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = defaultdict(lambda: {"cases": 0, "passed": 0, "failed": 0, "critical_failures": 0})
    check_counts: Counter[str] = Counter()
    check_failures: Counter[str] = Counter()
    tool_failures: Counter[str] = Counter()
    retrieval: list[dict[str, Any]] = []
    security_violations: list[dict[str, Any]] = []
    latencies: dict[str, list[float]] = defaultdict(list)
    fallback_count = 0
    error_count = 0
    recovered_count = 0
    terminal_error_count = 0
    cache_cold = 0
    cache_hot = 0
    judge_scores: dict[str, list[int]] = defaultdict(list)
    judge_configurations: Counter[str] = Counter()
    for result in results:
        category = categories[result.category]
        category["cases"] += 1
        category["passed"] += int(result.passed)
        category["failed"] += int(not result.passed)
        category["critical_failures"] += int(result.critical_failure)
        for check in result.checks:
            check_counts[check.name] += 1
            if not check.passed:
                check_failures[check.name] += 1
            if check.name == "retrieval_recall_mrr" and check.evidence:
                retrieval.append(check.evidence)
            if check.name == "scope_contract" and check.evidence.get("unsafe_tools"):
                security_violations.append({"case_id": result.case_id, "unsafe_tools": check.evidence.get("unsafe_tools")})
        if result.judge:
            judge_scores["clarity"].append(result.judge.clarity)
            judge_scores["patient_usefulness"].append(result.judge.patient_usefulness)
            judge_scores["context_continuity"].append(result.judge.context_continuity)
            judge_scores["behavior_adaptation"].append(result.judge.behavior_adaptation)
            judge_configurations[
                f"{result.judge.provider}/{result.judge.model}/{result.judge.prompt_version}"
            ] += 1
    for result, turn in _all_turns(results):
        for event in turn.tools:
            tool = str(event.get("tool") or "unknown")
            if event.get("ok") is False:
                tool_failures[tool] += 1
        error_count += len(turn.errors)
        fallback_count += len(turn.fallbacks)
        recovered_count += int(turn.recovered)
        terminal_error_count += int(turn.latency_ms.get("terminal_errors") or 0)
        cache_cold += int(turn.latency_ms.get("cache_cold") or 0)
        cache_hot += int(turn.latency_ms.get("cache_hot") or 0)
        for key, value in turn.latency_ms.items():
            if isinstance(value, (int, float)) and key.endswith("_ms") and value is not None:
                latencies[key].append(float(value))
    recall_values = [float(item["recall_at_k"]) for item in retrieval if item.get("recall_at_k") is not None]
    mrr_values = [float(item["mrr"]) for item in retrieval if item.get("mrr") is not None]
    return {
        "cases": len(results),
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "critical_failures": sum(result.critical_failure for result in results),
        "categories": dict(categories),
        "check_counts": dict(check_counts),
        "check_failures": dict(check_failures),
        "tool_failures": dict(tool_failures),
        "retrieval": {
            "labeled_turns": len(retrieval),
            "recall_at_k_mean": sum(recall_values) / len(recall_values) if recall_values else None,
            "mrr_mean": sum(mrr_values) / len(mrr_values) if mrr_values else None,
        },
        "security_violations": security_violations,
        "errors": error_count,
        "terminal_errors": terminal_error_count,
        "recovered_turns": recovered_count,
        "fallbacks": fallback_count,
        "cache": {"cold_events": cache_cold, "hot_events": cache_hot},
        "latencies": {key: latency_summary(value) for key, value in sorted(latencies.items())},
        "judge": {key: sum(value) / len(value) if value else None for key, value in judge_scores.items()},
        "judge_count": sum(judge_configurations.values()),
        "judge_configurations": dict(judge_configurations),
    }


def _worst(results: list[CaseResult]) -> list[CaseResult]:
    def score(result: CaseResult) -> tuple[int, int, int, float, float]:
        failed_checks = sum(not item.passed for item in result.checks)
        critical = sum(item.critical and not item.passed for item in result.checks)
        errors = len(result.errors) + sum(len(turn.errors) for turn in result.turns)
        total = sum(float(turn.latency_ms.get("total_turn_ms") or 0) for turn in result.turns)
        judge_average = (
            sum(
                (
                    result.judge.clarity,
                    result.judge.patient_usefulness,
                    result.judge.context_continuity,
                    result.judge.behavior_adaptation,
                )
            )
            / 4
            if result.judge
            else 5.0
        )
        # Deterministic and critical failures always outrank subjective scores.
        # Among otherwise equivalent cases, surface the lowest judge score and
        # then the slowest response so the section remains useful without a judge.
        return (-critical, -failed_checks, -errors, judge_average, -total)

    return sorted(results, key=score)[:10]


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def write_report(
    results_path: Path,
    report_path: Path,
    *,
    manifest_path: Path | None = None,
    judge_path: Path | None = None,
) -> dict[str, Any]:
    results = _load_results(results_path)
    if judge_path is not None and judge_path.is_file():
        judge_values = read_jsonl(judge_path)
        by_key = {(str(item.get("case_id")), int(item.get("repetition", 1))): item.get("judge") for item in judge_values}
        for result in results:
            value = by_key.get((result.case_id, result.repetition))
            if value:
                result.judge = JudgeScore.model_validate(value)
    manifest = _load_manifest(manifest_path, results)
    summary = summarize(results)
    critical = [
        {
            "case_id": result.case_id,
            "category": result.category,
            "checks": [check.model_dump(mode="json") for check in result.checks if check.critical and not check.passed],
        }
        for result in results
        if result.critical_failure
    ]
    lines = [
        "# IPS Golden Benchmark",
        "",
        "## Resultado",
        "",
        f"Casos ejecutados: **{summary['cases']}** · pasaron: **{summary['passed']}** · fallaron: **{summary['failed']}** · fallos críticos: **{summary['critical_failures']}**.",
        "",
        "Los fallos críticos se reportan de forma independiente del promedio de calidad y del juez.",
        "",
    ]
    if manifest:
        lines.extend([
            f"Snapshot: `{manifest.snapshot.snapshot_id}` · `source_hash`: `{manifest.snapshot.source_hash}` · estado: `{manifest.snapshot.status}`.",
            f"Modo: `{manifest.mode}` · semilla: `{manifest.seed}` · repeticiones: `{manifest.repetitions}` · commit: `{manifest.agent_configuration.get('git_commit')}`.",
            "",
        ])
    lines.extend(["## Calidad determinista por categoría", "", "| Categoría | Casos | Pasaron | Fallaron | Críticos |", "|---|---:|---:|---:|---:|"])
    for category, values in summary["categories"].items():
        lines.append(f"| {category} | {values['cases']} | {values['passed']} | {values['failed']} | {values['critical_failures']} |")
    lines.extend(["", "## Retrieval", "", f"Turnos etiquetados: **{summary['retrieval']['labeled_turns']}** · Recall@k medio: **{_fmt(summary['retrieval']['recall_at_k_mean'])}** · MRR medio: **{_fmt(summary['retrieval']['mrr_mean'])}**.", ""])
    lines.extend(["## Fallos de tools", ""])
    if summary["tool_failures"]:
        for name, count in summary["tool_failures"].items():
            lines.append(f"- `{name}`: {count}")
    else:
        lines.append("No hubo fallos de tools.")
    lines.extend(["", "## Adaptación JEV: control vs tratamiento", ""])
    adaptation_rows = []
    for result in results:
        if result.counterfactual:
            failed = [check.name for check in result.counterfactual.checks if not check.passed]
            adaptation_rows.append(f"- `{result.case_id}`: control {len(result.counterfactual.control)} turno(s), tratamiento {len(result.counterfactual.treatment)} turno(s); " + (f"fallos: {', '.join(failed)}" if failed else "sin fallos"))
    lines.extend(adaptation_rows or ["No se ejecutaron contrafactuales JEV."])
    lines.extend(["", "## Violaciones de seguridad", ""])
    if summary["security_violations"]:
        lines.extend(
            f"- `{item['case_id']}`: tools fuera de alcance: {', '.join(item['unsafe_tools'])}"
            for item in summary["security_violations"]
        )
    else:
        lines.append("No se detectaron tools fuera del alcance read-only. Los claims de texto libre se evalúan con el juez post-run.")
    lines.extend(["", "## Latencias", "", "| Señal | N | P50 ms | P95 ms | Máximo ms |", "|---|---:|---:|---:|---:|"])
    for name, values in summary["latencies"].items():
        lines.append(f"| {name} | {values['count']} | {_fmt(values['p50_ms'])} | {_fmt(values['p95_ms'])} | {_fmt(values['max_ms'])} |")
    lines.extend([
        "",
        f"Errores observados: **{summary['errors']}** · fallbacks: **{summary['fallbacks']}** · "
        f"errores terminales: **{summary['terminal_errors']}** · turnos recuperados: **{summary['recovered_turns']}** · "
        f"caché fría: **{summary['cache']['cold_events']}** · caché caliente: **{summary['cache']['hot_events']}**.",
        "",
        "## Juez LLM (post-run)",
        "",
    ])
    if any(value is not None for value in summary["judge"].values()):
        lines.append(f"Casos juzgados: **{summary['judge_count']} / {summary['cases']}**.")
        for configuration, count in summary["judge_configurations"].items():
            provider, model, prompt_version = configuration.split("/", 2)
            lines.append(
                f"- Configuración: provider `{provider}` · modelo `{model}` · prompt `{prompt_version}` · {count} caso(s)"
            )
        lines.extend(f"- {key}: {_fmt(value)} / 4" for key, value in summary["judge"].items())
        lines.append("El juez aporta una evaluación independiente y no altera ningún fallo determinista.")
    else:
        lines.append("No hay scores de juez. Ejecuta el comando `judge` después del benchmark y vuelve a generar el informe.")
    lines.extend(["", "## Fallos críticos", ""])
    if critical:
        for item in critical:
            lines.append(f"- `{item['case_id']}` ({item['category']}):")
            for check in item["checks"]:
                lines.append(f"  - `{check['name']}`: {check.get('message') or check.get('evidence')}")
    else:
        lines.append("No hay fallos críticos.")
    lines.extend(["", "## 10 peores casos", ""])
    for result in _worst(results):
        failed = [check.name for check in result.checks if not check.passed]
        latency = sum(float(turn.latency_ms.get("total_turn_ms") or 0) for turn in result.turns)
        judge_text = "sin juez"
        if result.judge:
            average = sum(
                (
                    result.judge.clarity,
                    result.judge.patient_usefulness,
                    result.judge.context_continuity,
                    result.judge.behavior_adaptation,
                )
            ) / 4
            judge_text = f"juez {average:.2f}/4"
        lines.append(
            f"- `{result.case_id}` ({result.category}) · {'; '.join(failed) or 'sin fallo determinista'} · "
            f"{judge_text} · total turnos {latency:.0f} ms"
        )
        if result.judge:
            lines.append(f"  - Juicio: {result.judge.justification}")
        for turn in result.turns:
            prompt = " ".join(turn.prompt.split())[:160]
            response = " ".join(turn.response.split())[:220]
            lines.append(f"  - Evidencia T{turn.turn_index}: `{prompt}` → `{response}`")
    lines.extend(["", "## Reproducción y limitaciones", "", "Cada caso JSONL conserva prompt, historial, eventos, tools, argumentos, resultados, señales JEV, comportamiento, trazas y latencias. Repite con el mismo `snapshot_id`, `source_hash`, commit, semilla y configuración registrada en `manifest.json`.", "", "No se almacenan credenciales. Offline usa providers simulados; live requiere activación explícita y puede tener coste. La medición de voz STT/TTS solo aparece al ejecutar el adaptador de voz opcional.", ""])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return summary


def _ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return float(numerator) / float(denominator)


def write_latency_comparison(
    offline_results_path: Path,
    live_results_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    offline_results = _load_results(offline_results_path)
    live_results = _load_results(live_results_path)
    offline_keys = {(item.case_id, item.repetition) for item in offline_results}
    live_keys = {(item.case_id, item.repetition) for item in live_results}
    if offline_keys != live_keys:
        raise ValueError("Offline and live results must contain the same cases and repetitions")
    offline_snapshots = {(item.snapshot_id, item.source_hash) for item in offline_results}
    live_snapshots = {(item.snapshot_id, item.source_hash) for item in live_results}
    if offline_snapshots != live_snapshots or len(offline_snapshots) != 1:
        raise ValueError("Offline and live results must use the same single snapshot and source hash")

    offline = summarize(offline_results)
    live = summarize(live_results)
    latency_keys = sorted(set(offline["latencies"]) | set(live["latencies"]))
    comparison = {
        "cases": len(offline_results),
        "offline": offline,
        "live": live,
        "latencies": {},
    }
    lines = [
        "# Comparación de latencia IPS Golden",
        "",
        f"Workload pareado: **{len(offline_results)} casos** · snapshot `{next(iter(offline_snapshots))[0]}`.",
        f"Turnos medidos: offline **{offline['latencies'].get('total_turn_ms', {}).get('count', 0)}** · "
        f"live **{live['latencies'].get('total_turn_ms', {}).get('count', 0)}**.",
        "",
        f"Calidad determinista: offline **{offline['passed']}/{offline['cases']}** · live **{live['passed']}/{live['cases']}**.",
        "",
        "| Señal | Offline P50 | Offline P95 | Live P50 | Live P95 | Live máx. | Multiplicador P50 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in latency_keys:
        offline_values = offline["latencies"].get(key, {})
        live_values = live["latencies"].get(key, {})
        ratio = _ratio(live_values.get("p50_ms"), offline_values.get("p50_ms"))
        comparison["latencies"][key] = {
            "offline": offline_values,
            "live": live_values,
            "p50_ratio": ratio,
        }
        lines.append(
            f"| {key} | {_fmt(offline_values.get('p50_ms'))} ms | {_fmt(offline_values.get('p95_ms'))} ms | "
            f"{_fmt(live_values.get('p50_ms'))} ms | {_fmt(live_values.get('p95_ms'))} ms | "
            f"{_fmt(live_values.get('max_ms'))} ms | {_fmt(ratio)}× |"
        )
    lines.extend(
        [
            "",
            f"Errores observados: offline **{offline['errors']}** · live **{live['errors']}**. "
            f"Fallbacks: offline **{offline['fallbacks']}** · live **{live['fallbacks']}**.",
            "",
            f"Eventos de caché live: fría **{live['cache']['cold_events']}** · caliente **{live['cache']['hot_events']}**.",
            "",
            "Los percentiles describen este workload pareado; no son un SLA. STT y TTS solo se comparan cuando ambos adaptadores de voz se ejecutan.",
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return comparison


def write_variant_comparison(
    left_results_path: Path,
    right_results_path: Path,
    output_path: Path,
    *,
    left_label: str = "B",
    right_label: str = "E",
    left_manifest_path: Path | None = None,
    right_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Compare two live runs without implying either side is offline.

    The case keys and selected frozen snapshot must match exactly. The raw
    JSONL remains the evidence source; this file is only a human-readable
    aggregation for the A/B run.
    """
    if not left_label.strip() or not right_label.strip() or left_label == right_label:
        raise ValueError("Variant comparison requires two distinct non-empty labels")
    left_results = _load_results(left_results_path)
    right_results = _load_results(right_results_path)
    if not left_results or not right_results:
        raise ValueError("Variant comparison requires non-empty result files")
    left_keys = {(item.case_id, item.repetition) for item in left_results}
    right_keys = {(item.case_id, item.repetition) for item in right_results}
    if len(left_keys) != len(left_results) or len(right_keys) != len(right_results):
        raise ValueError("Variant results must not contain duplicate case/repetition keys")
    if left_keys != right_keys:
        raise ValueError("Variant results must contain the same cases and repetitions")
    left_snapshots = {(item.snapshot_id, item.source_hash) for item in left_results}
    right_snapshots = {(item.snapshot_id, item.source_hash) for item in right_results}
    if left_snapshots != right_snapshots or len(left_snapshots) != 1:
        raise ValueError("Variant results must use the same single snapshot and source hash")

    manifest_paths = {
        left_label: left_manifest_path or left_results_path.with_name("manifest.json"),
        right_label: right_manifest_path or right_results_path.with_name("manifest.json"),
    }
    manifests: dict[str, RunManifest] = {}
    for label, path in manifest_paths.items():
        if not path.is_file():
            raise ValueError(f"Variant comparison requires a manifest for {label}: {path}")
        manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.mode != "live":
            raise ValueError(f"Variant comparison requires live manifests; {label} is {manifest.mode}")
        variant = manifest.agent_configuration.get("agent_prompt_variant")
        if variant not in {"baseline", "compact"}:
            raise ValueError(f"Manifest {label} has no supported prompt variant")
        expected_case_ids = set(manifest.cases)
        selected_results = left_results if label == left_label else right_results
        actual_case_ids = {item.case_id for item in selected_results}
        actual_keys = {(item.case_id, item.repetition) for item in selected_results}
        expected_keys = {
            (case_id, repetition)
            for case_id in manifest.cases
            for repetition in range(1, manifest.repetitions + 1)
        }
        if (
            manifest.case_count != len(manifest.cases)
            or len(expected_case_ids) != len(manifest.cases)
            or expected_case_ids != actual_case_ids
            or expected_keys != actual_keys
            or manifest.repetitions < 1
        ):
            raise ValueError(f"Manifest {label} does not match its result cases")
        result_snapshot = next(iter(left_snapshots if label == left_label else right_snapshots))
        if (manifest.snapshot.snapshot_id, manifest.snapshot.source_hash) != result_snapshot:
            raise ValueError(f"Manifest {label} does not match its result snapshot")
        manifests[label] = manifest
    left_variant = manifests[left_label].agent_configuration.get("agent_prompt_variant")
    right_variant = manifests[right_label].agent_configuration.get("agent_prompt_variant")
    if left_variant == right_variant:
        raise ValueError("Variant comparison requires different baseline/compact prompt variants")

    left = summarize(left_results)
    right = summarize(right_results)
    latency_keys = sorted(set(left["latencies"]) | set(right["latencies"]))
    comparison: dict[str, Any] = {
        "left_label": left_label,
        "right_label": right_label,
        "cases": len(left_results),
        "snapshot": {
            "snapshot_id": next(iter(left_snapshots))[0],
            "source_hash": next(iter(left_snapshots))[1],
        },
        left_label: left,
        right_label: right,
        "latencies": {},
    }
    def _config(label: str) -> dict[str, Any]:
        manifest = manifests.get(label)
        if manifest is None:
            return {}
        return {
            "mode": manifest.mode,
            "app_env": manifest.agent_configuration.get("app_env"),
            "agent_prompt_variant": manifest.agent_configuration.get("agent_prompt_variant"),
            "model_chain": manifest.agent_configuration.get("model_chain"),
            "agent_tool_modules": manifest.agent_configuration.get("agent_tool_modules"),
            "jev_model": manifest.agent_configuration.get("jev_model"),
        }

    comparison["configurations"] = {left_label: _config(left_label), right_label: _config(right_label)}
    lines = [
        f"# Comparación de latencia IPS Golden: {left_label} vs {right_label}",
        "",
        f"Workload live pareado: **{len(left_results)} casos** · snapshot ` {next(iter(left_snapshots))[0]} `.",
        f"Variantes: **{left_label}** `{comparison['configurations'][left_label].get('agent_prompt_variant', 'unknown')}` · "
        f"**{right_label}** `{comparison['configurations'][right_label].get('agent_prompt_variant', 'unknown')}`.",
        "",
        f"Calidad determinista: {left_label} **{left['passed']}/{left['cases']}** · "
        f"{right_label} **{right['passed']}/{right['cases']}**.",
        "",
        f"| Señal | {left_label} P50 | {left_label} P95 | {left_label} máx. | "
        f"{right_label} P50 | {right_label} P95 | {right_label} máx. | P50 {right_label}/{left_label} |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in latency_keys:
        left_values = left["latencies"].get(key, {})
        right_values = right["latencies"].get(key, {})
        ratio = _ratio(right_values.get("p50_ms"), left_values.get("p50_ms"))
        comparison["latencies"][key] = {
            left_label: left_values,
            right_label: right_values,
            "right_over_left_p50_ratio": ratio,
        }
        lines.append(
            f"| {key} | {_fmt(left_values.get('p50_ms'))} ms | {_fmt(left_values.get('p95_ms'))} ms | "
            f"{_fmt(left_values.get('max_ms'))} ms | {_fmt(right_values.get('p50_ms'))} ms | "
            f"{_fmt(right_values.get('p95_ms'))} ms | {_fmt(right_values.get('max_ms'))} ms | {_fmt(ratio)}× |"
        )
    lines.extend([
        "",
        f"Errores: {left_label} **{left['errors']}** · {right_label} **{right['errors']}**. "
        f"Terminales: {left_label} **{left['terminal_errors']}** · {right_label} **{right['terminal_errors']}**. "
        f"Turnos recuperados: {left_label} **{left['recovered_turns']}** · {right_label} **{right['recovered_turns']}**. "
        f"Fallbacks: {left_label} **{left['fallbacks']}** · {right_label} **{right['fallbacks']}**.",
        f"Caché: {left_label} fría **{left['cache']['cold_events']}**, caliente **{left['cache']['hot_events']}** · "
        f"{right_label} fría **{right['cache']['cold_events']}**, caliente **{right['cache']['hot_events']}**.",
        "",
        "## Diferencia por caso",
        "",
        f"| Caso | {left_label} total | {right_label} total | Diferencia {right_label}-{left_label} |",
        "|---|---:|---:|---:|",
    ])
    left_by_key = {(item.case_id, item.repetition): item for item in left_results}
    right_by_key = {(item.case_id, item.repetition): item for item in right_results}
    case_deltas: list[dict[str, Any]] = []
    for key in sorted(left_by_key):
        left_total = sum(float(turn.latency_ms.get("total_turn_ms") or 0) for turn in left_by_key[key].turns)
        right_total = sum(float(turn.latency_ms.get("total_turn_ms") or 0) for turn in right_by_key[key].turns)
        delta = right_total - left_total
        case_deltas.append({"case_id": key[0], "repetition": key[1], left_label: left_total, right_label: right_total, "delta_ms": delta})
        lines.append(f"| `{key[0]}` | {left_total:.0f} ms | {right_total:.0f} ms | {delta:+.0f} ms |")
    comparison["case_deltas"] = case_deltas
    lines.extend([
        "",
        "Los percentiles describen este workload pareado y no son un SLA. La comparación conserva la variabilidad del LLM live; los checks deterministas solo verifican evidencia, tools, snapshot y seguridad, y no generan respuestas.",
        "",
    ])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return comparison


__all__ = [
    "latency_summary",
    "read_jsonl",
    "summarize",
    "write_latency_comparison",
    "write_variant_comparison",
    "write_report",
]
