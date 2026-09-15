"""Streaming STT quality/latency evaluation against oracle fixtures."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from dotenv import load_dotenv

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
JUDGE_SYSTEM = (
    "Eres juez de un agente de voz en español. Oracle = lo que el usuario dijo. "
    "Hipótesis = transcripción STT. Decide si el agente podría entender el PEDIDO "
    "(saludo, prueba de si entiende, usar el agente de audio) y responder útilmente. "
    "usable=true aunque falten escenario (oficina/discoteca), marcas (TTS), haya "
    "spacing raro (entre cortado), homófonos (la gente/agente) o una palabra mal. "
    "usable=false solo si es ilegible, alucinación, solo un saludo vacío, o desaparece "
    "por completo la pregunta/pedido. "
    'Responde JSON: {"usable": true, "reason": "frase corta"}.'
)


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


def is_silence_oracle(text: str) -> bool:
    return "ruido blanco" in text.casefold()


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
    if "restaurante" in folded:
        return "restaurant"
    if "rápido" in folded or "rapido" in folded:
        return "fast_speech"
    if "muletillas" in folded:
        return "fillers"
    if any(token in folded for token in ("febrero", "junio", "agosto", "diciembre")):
        return "numbers_dates"
    if "nodejs" in folded or "whisper" in folded:
        return "self_correction"
    if "ruido blanco" in folded:
        return "white_noise"
    if "volviste" in folded or "landres" in folded:
        return "overlapping_talk"
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


def hot_peak_limit(pcm: bytes, gate: float = 0.95, ceiling: float = 0.90) -> bytes:
    """Scale only 100 ms frames whose peak exceeds `gate`. Leaves quieter frames untouched."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm
    out = samples.copy()
    for start in range(0, samples.size, FRAME_SAMPLES):
        chunk = out[start : start + FRAME_SAMPLES]
        peak = float(np.max(np.abs(chunk))) / 32768.0
        if peak <= gate or peak <= 0:
            continue
        chunk *= ceiling / peak
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def sparse_clip_gain(pcm: bytes, peak_gate: float = 0.95, max_clip_frac: float = 0.001, target_rms: float = 0.10) -> bytes:
    """AGC only if the clip is near full-scale but clipping is rare (skip chopped bursts)."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm
    peak = float(np.max(np.abs(samples))) / 32768.0
    clip_frac = float(np.mean(np.abs(samples) >= 0.95 * 32768.0))
    if peak <= peak_gate or clip_frac >= max_clip_frac:
        return pcm
    return gain_normalize(pcm, target_rms)


def hot_frame_rms(pcm: bytes, gate: float = 0.95, target_rms: float = 0.10) -> bytes:
    """Per 100 ms frame: if peak>gate, scale that frame toward target RMS (streamable)."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm
    out = samples.copy()
    for start in range(0, samples.size, FRAME_SAMPLES):
        chunk = out[start : start + FRAME_SAMPLES]
        peak = float(np.max(np.abs(chunk))) / 32768.0
        if peak <= gate or peak <= 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(chunk)))) / 32768.0
        if rms < 1e-5:
            continue
        chunk *= min(target_rms / rms, 8.0)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def afftdn_denoise(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """ffmpeg afftdn with noise tracking. Full-utterance diagnostic (not per-chunk)."""
    return _ffmpeg_pcm_filter(pcm, "afftdn=nr=12:nf=-50:tn=1", sample_rate)


def arnndn_denoise(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """ffmpeg arnndn (RNNoise std.rnnn). Frame-based; harness still runs on the full clip."""
    model = ROOT / "backend" / "models" / "rnnoise" / "std.rnnn"
    if not model.is_file():
        raise FileNotFoundError(f"Falta {model}")
    return _ffmpeg_pcm_filter(pcm, f"arnndn=m={model}:mix=1", sample_rate)


def _ffmpeg_pcm_filter(pcm: bytes, audio_filter: str, sample_rate: int) -> bytes:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-af",
            audio_filter,
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "pipe:1",
        ],
        input=pcm,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace") or f"ffmpeg {audio_filter} failed")
    return completed.stdout


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


def _load_eval_env() -> None:
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(BACKEND / ".env", override=False)


def heuristic_intent_judge(oracle: str, hypothesis: str, semantic: float) -> dict[str, Any]:
    folded = normalize_text(hypothesis)
    tokens = folded.split()
    if not tokens:
        return {"usable": False, "reason": "vacío", "source": "heuristic"}
    if len(tokens) <= 2 and folded in {"hola", "hola hola"}:
        return {"usable": False, "reason": "solo saludo", "source": "heuristic"}
    if semantic >= 0.93 and len(tokens) >= 8:
        return {"usable": True, "reason": "E5>=0.93 y texto largo", "source": "heuristic"}
    return {"usable": False, "reason": f"E5={semantic:.2f} insuficiente", "source": "heuristic"}


def _parse_judge_json(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "usable" not in parsed:
        return None
    usable = bool(parsed.get("usable"))
    reason = str(parsed.get("reason") or "").strip() or ("usable" if usable else "no usable")
    return {"usable": usable, "reason": reason}


def llm_intent_judge(oracle: str, hypothesis: str) -> dict[str, Any] | None:
    prompt = f"Oracle: {oracle}\nHipótesis: {hypothesis or '(vacío)'}"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent",
                params={"key": gemini_key},
                json={
                    "systemInstruction": {"parts": [{"text": JUDGE_SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                },
                timeout=20.0,
            )
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _parse_judge_json(text)
            if parsed and len(parsed["reason"]) >= 12:
                return {**parsed, "source": "gemini"}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            pass
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key and openai_key.lower() not in {"tu_clave_de_openai"}:
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=20.0,
            )
            response.raise_for_status()
            parsed = _parse_judge_json(response.json()["choices"][0]["message"]["content"])
            if parsed and len(parsed["reason"]) >= 12:
                return {**parsed, "source": "openai"}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            pass
    return None


def intent_judge(oracle: str, hypothesis: str, semantic: float) -> dict[str, Any]:
    judged = llm_intent_judge(oracle, hypothesis)
    return judged or heuristic_intent_judge(oracle, hypothesis, semantic)


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


def evaluate_clip(
    directory: Path,
    embeddings: E5EmbeddingProvider,
    *,
    apply_gain: bool,
    apply_peak_limit: bool,
    apply_hot_peak_limit: bool,
    apply_sparse_clip_gain: bool,
    apply_hot_frame_rms: bool,
    apply_afftdn: bool,
    apply_arnndn: bool,
) -> dict[str, Any]:
    oracle = (directory / "oracle.txt").read_text(encoding="utf-8").strip()
    audio_path = directory / "audio.ogg"
    pcm = sherpa.decode_audio_file(audio_path)
    peak = float(np.max(np.abs(np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)))) / 32768.0 if pcm else 0.0
    rms = float(np.sqrt(np.mean(np.square(np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32))))) / 32768.0 if pcm else 0.0
    if apply_gain:
        pcm = gain_normalize(pcm)
    if apply_sparse_clip_gain:
        pcm = sparse_clip_gain(pcm)
    if apply_peak_limit:
        pcm = peak_limit(pcm)
    if apply_hot_peak_limit:
        pcm = hot_peak_limit(pcm)
    if apply_hot_frame_rms:
        pcm = hot_frame_rms(pcm)
    if apply_afftdn:
        pcm = afftdn_denoise(pcm)
    if apply_arnndn:
        pcm = arnndn_denoise(pcm)
    timed = stream_transcribe(pcm)
    hypothesis = timed["text"]
    silence = is_silence_oracle(oracle)
    hyp_tokens = normalize_text(hypothesis).split()
    if silence:
        hallucinated = bool(hyp_tokens)
        wer = 1.0 if hallucinated else 0.0
        cer = 1.0 if hallucinated else 0.0
        semantic = 0.0 if hallucinated else 1.0
        judged = {
            "usable": not hallucinated,
            "reason": "silencio correcto" if not hallucinated else "alucinación sobre ruido blanco",
            "source": "silence_oracle",
        }
    else:
        wer = word_error_rate(oracle, hypothesis)
        cer = char_error_rate(oracle, hypothesis)
        vectors = embeddings.embed_passages([hypothesis or " ", oracle])
        semantic = cosine(vectors[0], vectors[1])
        judged = intent_judge(oracle, hypothesis, semantic)
    critical: list[str] = []
    if not hypothesis and not silence:
        critical.append("empty")
    if not judged["usable"]:
        critical.append("intent_fail")
    if semantic < SEMANTIC_CRITICAL and not silence:
        critical.append("low_semantic")
    if wer > WER_CRITICAL and not judged["usable"]:
        critical.append("high_wer")
    partial_ok = timed["audio_ms_to_first_partial"] is not None and timed["audio_ms_to_first_partial"] < 500 and timed["faster_than_realtime"]
    final_ok = timed["tail_ms"] < 1500
    if critical:
        result = "critical"
    elif judged["usable"]:
        result = "pass"
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
        "judge_usable": judged["usable"],
        "judge_reason": judged["reason"],
        "judge_source": judged["source"],
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
        "| Audio | Escenario | WER | CER | Semántica | Judge | Partial latency | Final latency | Resultado |",
        "|---|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for row in rows:
        partial = row["audio_ms_to_first_partial"]
        lines.append(
            f"| {row['id']} | {row['scenario']} | {row['wer']:.3f} | {row['cer']:.3f} | {row['semantic']:.3f} | "
            f"{'sí' if row.get('judge_usable') else 'no'} | "
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
                f"Peak limit: {args.peak_limit}",
                f"Hot peak limit: {args.hot_peak_limit}",
                f"Sparse clip gain: {args.sparse_clip_gain}",
                f"Hot frame RMS: {args.hot_frame_rms}",
                f"afftdn: {args.afftdn}",
                f"arnndn: {args.arnndn}",
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
    parser.add_argument(
        "--hot-peak-limit",
        action="store_true",
        help="Escala solo frames de 100 ms con peak>0.95 hacia 0.90 (no AGC global)",
    )
    parser.add_argument(
        "--sparse-clip-gain",
        action="store_true",
        help="RMS 0.10 solo si peak>0.95 y fracción clipped < 0.001 (omite chopped)",
    )
    parser.add_argument(
        "--hot-frame-rms",
        action="store_true",
        help="RMS 0.10 solo en frames 100 ms con peak>0.95 (aprox. streamable de sparse-clip)",
    )
    parser.add_argument(
        "--afftdn",
        action="store_true",
        help="ffmpeg afftdn (nr=12, nf=-50, track_noise) sobre el PCM completo",
    )
    parser.add_argument(
        "--arnndn",
        action="store_true",
        help="ffmpeg arnndn (RNNoise std.rnnn, mix=1) sobre el PCM completo",
    )
    parser.add_argument("--override", action="append", default=[], type=parse_override, help="key=value sobre SHERPA_CONFIG")
    args = parser.parse_args()
    _load_eval_env()
    destination = ARTIFACTS / "experiments" / args.name
    if destination.exists():
        raise SystemExit(f"El experimento {args.name} ya existe en {destination}")
    overrides = dict(args.override)
    if overrides:
        sherpa.apply_config(overrides)
    destination.mkdir(parents=True)
    embeddings = local_e5()
    rows = [
        evaluate_clip(
            clip,
            embeddings,
            apply_gain=args.gain_normalize,
            apply_peak_limit=args.peak_limit,
            apply_hot_peak_limit=args.hot_peak_limit,
            apply_sparse_clip_gain=args.sparse_clip_gain,
            apply_hot_frame_rms=args.hot_frame_rms,
            apply_afftdn=args.afftdn,
            apply_arnndn=args.arnndn,
        )
        for clip in discover_clips()
    ]
    config = dict(sherpa.SHERPA_CONFIG)
    payload = {
        "name": args.name,
        "notes": args.notes,
        "change": args.change,
        "gain_normalize": args.gain_normalize,
        "peak_limit": args.peak_limit,
        "hot_peak_limit": args.hot_peak_limit,
        "sparse_clip_gain": args.sparse_clip_gain,
        "hot_frame_rms": args.hot_frame_rms,
        "afftdn": args.afftdn,
        "arnndn": args.arnndn,
        "config": config,
        "clips": rows,
        "aggregates": {
            "n": len(rows),
            "mean_wer": round(sum(row["wer"] for row in rows) / len(rows), 4),
            "mean_cer": round(sum(row["cer"] for row in rows) / len(rows), 4),
            "mean_semantic": round(sum(row["semantic"] for row in rows) / len(rows), 4),
            "critical_count": sum(bool(row["critical_errors"]) for row in rows),
            "judge_usable_count": sum(bool(row.get("judge_usable")) for row in rows),
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
