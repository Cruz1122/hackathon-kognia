from pathlib import Path
from types import SimpleNamespace

from app import main
from app.features.synthesis import service


class FakePiperVoice:
    config = SimpleNamespace(sample_rate=22_050)

    def __init__(self) -> None:
        self.stream_exhausted = False
        self.synthesis_config = None

    def synthesize(self, _text, *, syn_config):
        self.synthesis_config = syn_config
        yield SimpleNamespace(audio_int16_bytes=b"pcm-first")
        self.stream_exhausted = True
        yield SimpleNamespace(audio_int16_bytes=b"pcm-second")


def test_stream_tts_audio_yields_before_all_audio_is_generated(monkeypatch) -> None:
    voice = FakePiperVoice()
    synthesis_config = object()
    monkeypatch.setattr(service, "_voice", voice)
    monkeypatch.setattr(service, "_synthesis_config", synthesis_config)

    stream = service.stream_tts_audio("Hola mundo.")
    try:
        first_chunk = next(stream)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()

    assert first_chunk == b"pcm-first"
    assert voice.synthesis_config is synthesis_config
    assert voice.stream_exhausted is False


def test_tts_loads_deterministic_mexican_spanish_voice(monkeypatch) -> None:
    captured: dict[str, object] = {}
    voice = FakePiperVoice()

    class FakeVoiceLoader:
        @staticmethod
        def load(model_path):
            captured["model_path"] = Path(model_path)
            return voice

    class FakeSynthesisConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(service, "_voice", None)
    monkeypatch.setattr(service, "_synthesis_config", None)
    monkeypatch.delenv("PIPER_MODEL_DIR", raising=False)
    monkeypatch.delenv("PIPER_TTS_VOICE", raising=False)
    monkeypatch.setattr(
        service,
        "import_module",
        lambda _name: SimpleNamespace(
            PiperVoice=FakeVoiceLoader,
            SynthesisConfig=FakeSynthesisConfig,
        ),
    )

    service.preload_tts()

    assert captured["model_path"] == (
        service.REPOSITORY_ROOT / "backend/models/piper-es/es_MX-claude-high.onnx"
    )
    assert captured["noise_scale"] == 0.0
    assert captured["noise_w_scale"] == 0.0
    assert service.tts_sample_rate() == 22_050


def test_stream_tts_audio_forwards_every_piper_chunk(monkeypatch) -> None:
    voice = FakePiperVoice()
    monkeypatch.setattr(service, "_voice", voice)
    monkeypatch.setattr(service, "_synthesis_config", object())

    assert list(service.stream_tts_audio("Respuesta completa.")) == [
        b"pcm-first",
        b"pcm-second",
    ]


def test_stream_tts_audio_uses_phonetic_wane_cue_without_mutating_text_contract(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class CapturingVoice(FakePiperVoice):
        def synthesize(self, text, *, syn_config):
            captured["text"] = text
            yield SimpleNamespace(audio_int16_bytes=b"pcm")

    monkeypatch.setattr(service, "_voice", CapturingVoice())
    monkeypatch.setattr(service, "_synthesis_config", object())

    assert list(service.stream_tts_audio("Soy Wane. WANE sigue aquí.")) == [b"pcm"]
    assert captured["text"] == "Soy Güein. Güein sigue aquí."


def test_semantic_chunker_does_not_cut_an_unfinished_spanish_sentence() -> None:
    unfinished = "Claro puedo ayudarte a revisar el problema paso"
    complete = f"{unfinished} a paso."

    assert main._take_semantic_chunk(unfinished) == ("", unfinished)
    assert main._take_semantic_chunk(complete) == (complete, "")
