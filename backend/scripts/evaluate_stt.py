"""Streaming STT quality/latency evaluation against oracle fixtures."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.features.transcription import service as sherpa
from app.platform.rag.embeddings import E5EmbeddingProvider

FIXTURES = ROOT / "tests" / "fixtures" / "stt"
ARTIFACTS = ROOT / "artifacts" / "stt-eval"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 1600
FRAME_BYTES = FRAME_SAMPLES * 2
CALL_SILENCE_MS = 800.0
SEMANTIC_CRITICAL = 0.70
WER_CRITICAL = 0.50


def parse_override(raw: str) -> tuple[str, Any]:
    key, _, value = raw.partition("=")
    if not key or not _:
        raise argparse.ArgumentTypeError(f"override inválido: {raw}")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return key, lowered == "true"
    try:
        if "." in value:
            return key, float(value)
        return key, int(value)
    except ValueError:
        return key, value


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).casefold()
    folded = re.sub(r"[^\w\sáéíóúüñ]", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


def levenshtein(left: list[str] | str, right: list[str] | str) -> int:
    rows, cols = len(left) + 1, len(right) + 1
    previous = list(range(cols))
    for i, token in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (token != other)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def word_error_rate(oracle: str, hypothesis: str) -> float:
    reference = normalize_text(oracle).split()
    hypothesis_tokens = normalize_text(hypothesis).split()
    if not reference:
        return 0.0 if not hypothesis_tokens else 1.0
    return levenshtein(reference, hypothesis_tokens) / len(reference)


def char_error_rate(oracle: str, hypothesis: str) -> float:
    reference = normalize_text(oracle).replace(" ", "")
    hypothesis_chars = normalize_text(hypothesis).replace(" ", "")
    if not reference:
        return 0.0 if not hypothesis_chars else 1.0
    return levenshtein(reference, hypothesis_chars) / len(reference)


def cosine(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def scenario_from_oracle(text: str) -> str:
    folded = text.casefold()
    if "oficina" in folded:
        return "office_noise"
    if "susurr" in folded:
        return "whisper"
    if "entrecort" in folded:
        return "chopped"
    if "discoteca" in folded:
        return "nightclub"
    if "distorsi" in folded:
        return "distortion"
    return "unknown"


def peak_limit(pcm: bytes, ceiling: float = 0.80) -> bytes:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm
    peak = float(np.max(np.abs(samples))) / 32768.0
    if peak <= ceiling or peak <= 0:
        return pcm
    clipped = np.clip(samples * (ceiling / peak), -32768, 32767).astype(np.int16)
    return clipped.tobytes()


def gain_normalize(pcm: bytes, target_rms: float = 0.10) -> bytes:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm
    rms = float(np.sqrt(np.mean(np.square(samples)))) / 32768.0
    if rms < 1e-5:
        return pcm
    scale = min(target_rms / rms, 8.0)
    clipped = np.clip(samples * scale, -32768, 32767).astype(np.int16)
    return clipped.tobytes()


def local_e5() -> E5EmbeddingProvider:
    snapshots = Path.home() / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small/snapshots"
    if snapshots.is_dir():
        hashes = sorted(path for path in snapshots.iterdir() if path.is_dir())
        if hashes:
            return E5EmbeddingProvider(str(hashes[-1]))
    return E5EmbeddingProvider()


def discover_clips() -> list[Path]:
    clips = sorted(path for path in FIXTURES.glob("audio-*") if path.is_dir())
    if not clips:
        raise FileNotFoundError(f"No hay fixtures STT en {FIXTURES}")
    return clips


def stream_transcribe(pcm: bytes) -> dict[str, Any]:
    stream = sherpa.create_stream()
    started = time.perf_counter()
    audio_ms = 0.0
    first_partial_audio_ms: float | None = None
    first_partial_wall_ms: float | None = None
    last_partial = ""
    for offset in range(0, len(pcm), FRAME_BYTES):
        chunk = pcm[offset : offset + FRAME_BYTES]
        if len(chunk) < 2:
            break
        text, _ended = sherpa.feed_pcm(stream, chunk, SAMPLE_RATE)
        audio_ms += (len(chunk) // 2) / SAMPLE_RATE * 1000.0
        if text.strip():
            last_partial = text.strip()
            if first_partial_audio_ms is None:
                first_partial_audio_ms = audio_ms
                first_partial_wall_ms = (time.perf_counter() - started) * 1000.0
    tail_started = time.perf_counter()
    final = sherpa.finish_stream(stream, SAMPLE_RATE).strip()
    tail_ms = (time.perf_counter() - tail_started) * 1000.0
    wall_final_ms = (time.perf_counter() - started) * 1000.0
    hypothesis = final or last_partial
    return {
        "text": hypothesis,
        "partial_text": last_partial,
        "audio_duration_ms": audio_ms,
        "audio_ms_to_first_partial": first_partial_audio_ms,
        "wall_ms_to_first_partial": first_partial_wall_ms,
        "wall_ms_to_final": wall_final_ms,
        "tail_ms": tail_ms,
        "conversational_final_est_ms": CALL_SILENCE_MS + tail_ms,
        "faster_than_realtime": wall_final_ms < audio_ms if audio_ms else True,
    }


def evaluate_clip(directory: Path, embeddings: E5EmbeddingProvider, *, apply_gain: bool, apply_peak_limit: bool) -> dict[str, Any]:
    oracle = (directory / "oracle.txt").read_text(encoding="utf-8").strip()
    audio_path = directory / "audio.ogg"
    pcm = sherpa.decode_audio_file(audio_path)
    peak = float(np.max(np.abs(np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)))) / 32768.0 if pcm else 0.0
    rms = float(np.sqrt(np.mean(np.square(np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32))))) / 32768.0 if pcm else 0.0
    if apply_gain:
        pcm = gain_normalize(pcm)
    if apply_peak_limit:
        pcm = peak_limit(pcm)
    timed = stream_transcribe(pcm)
    hypothesis = timed["text"]
    wer = word_error_rate(oracle, hypothesis)
    cer = char_error_rate(oracle, hypothesis)
    vectors = embeddings.embed_passages([hypothesis or " ", oracle])
    semantic = cosine(vectors[0], vectors[1])
    critical: list[str] = []
    if not hypothesis:
        critical.append("empty")
    if semantic < SEMANTIC_CRITICAL:
        critical.append("low_semantic")
    if wer > WER_CRITICAL:
        critical.append("high_wer")
    partial_ok = timed["audio_ms_to_first_partial"] is not None and timed["audio_ms_to_first_partial"] < 500 and timed["faster_than_realtime"]
    final_ok = timed["tail_ms"] < 1500
    if critical:
        result = "critical"
    elif wer <= 0.25 and semantic >= 0.85:
        result = "pass"
    else:
        result = "weak"
    return {
        "id": directory.name,
        "scenario": scenario_from_oracle(oracle),
        "oracle": oracle,
        "hypothesis": hypothesis,
        "wer": round(wer, 4),
        "cer": round(cer, 4),
        "semantic": round(semantic, 4),
        "critical_errors": critical,
        "result": result,
        "partial_gate_ok": partial_ok,
        "final_gate_ok": final_ok,
        "pcm_peak": round(peak, 4),
        "pcm_rms": round(rms, 4),
        **{key: (round(value, 2) if isinstance(value, float) else value) for key, value in timed.items() if key != "text"},
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        partial = row["audio_ms_to_first_partial"]
        lines.append(
            f"| {row['id']} | {row['scenario']} | {row['wer']:.3f} | {row['cer']:.3f} | {row['semantic']:.3f} | "
            f"{partial if partial is not None else '—'} / {row['wall_ms_to_first_partial'] if row['wall_ms_to_first_partial'] is not None else '—'} | "
            f"{row['tail_ms']:.1f} | {row['result']} |"
        )
    return "\n".join(lines)


def write_notes(path: Path, args: argparse.Namespace, config: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    mean_wer = sum(row["wer"] for row in rows) / len(rows)
    mean_semantic = sum(row["semantic"] for row in rows) / len(rows)
    critical = sum(1 for row in rows if row["critical_errors"])
    path.write_text(
        "\n".join(
            [
                f"Experiment: {args.name}",
                f"Cambio: {args.change or args.notes or 'n/a'}",
                f"Motivo: {args.notes or 'n/a'}",
                f"Configuración: {json.dumps(config, ensure_ascii=False)}",
                f"Gain normalize: {args.gain_normalize}",
                f"Resultado: mean WER={mean_wer:.3f} mean semantic={mean_semantic:.3f} critical={critical}/{len(rows)}",
                "Mejora: (completar tras comparar con baseline)",
                "Regresión: (completar tras comparar con baseline)",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa Sherpa STT contra tests/fixtures/stt")
    parser.add_argument("--name", required=True, help="Nombre único del experimento (directorio de artefactos)")
    parser.add_argument("--notes", default="", help="Motivo del experimento")
    parser.add_argument("--change", default="", help="Cambio aplicado")
    parser.add_argument("--gain-normalize", action="store_true")
    parser.add_argument("--peak-limit", action="store_true", help="Escala picos > 0.80 hacia 0.80 (sin boost)")
    parser.add_argument("--override", action="append", default=[], type=parse_override, help="key=value sobre SHERPA_CONFIG")
    args = parser.parse_args()
    destination = ARTIFACTS / "experiments" / args.name
    if destination.exists():
        raise SystemExit(f"El experimento {args.name} ya existe en {destination}")
    overrides = dict(args.override)
    if overrides:
        sherpa.apply_config(overrides)
    destination.mkdir(parents=True)
    embeddings = local_e5()
    rows = [evaluate_clip(clip, embeddings, apply_gain=args.gain_normalize, apply_peak_limit=args.peak_limit) for clip in discover_clips()]
    config = dict(sherpa.SHERPA_CONFIG)
    payload = {
        "name": args.name,
        "notes": args.notes,
        "change": args.change,
        "gain_normalize": args.gain_normalize,
        "peak_limit": args.peak_limit,
        "config": config,
        "clips": rows,
        "aggregates": {
            "n": len(rows),
            "mean_wer": round(sum(row["wer"] for row in rows) / len(rows), 4),
            "mean_cer": round(sum(row["cer"] for row in rows) / len(rows), 4),
            "mean_semantic": round(sum(row["semantic"] for row in rows) / len(rows), 4),
            "critical_count": sum(bool(row["critical_errors"]) for row in rows),
            "mean_tail_ms": round(sum(row["tail_ms"] for row in rows) / len(rows), 2),
            "mean_audio_ms_to_first_partial": round(
                sum(row["audio_ms_to_first_partial"] or 0 for row in rows) / len(rows),
                2,
            ),
        },
    }
    (destination / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "summary.md").write_text(
        f"# {args.name}\n\n{markdown_table(rows)}\n\n```json\n{json.dumps(payload['aggregates'], indent=2)}\n```\n",
        encoding="utf-8",
    )
    write_notes(destination / "notes.md", args, config, rows)
    print(json.dumps(payload["aggregates"], ensure_ascii=False, indent=2))
    print(markdown_table(rows))


if __name__ == "__main__":
    main()
