from app.telephony.runtime import MAX_UTTERANCE_SECONDS, SILENCE_SECONDS, utterance_ready


def test_recognizer_endpoint_does_not_cut_a_slow_speaker() -> None:
    now = 10.0
    last_voice_at = now - (SILENCE_SECONDS * 0.6)

    assert utterance_ready(
        last_voice_at=last_voice_at,
        first_voice_at=last_voice_at - 2.0,
        now=now,
        endpoint=True,
    ) is False


def test_turn_closes_after_the_real_silence_window() -> None:
    now = 10.0
    last_voice_at = now - SILENCE_SECONDS

    assert utterance_ready(
        last_voice_at=last_voice_at,
        first_voice_at=last_voice_at - 2.0,
        now=now,
        endpoint=True,
    ) is True


def test_duration_cap_still_recovers_from_continuous_audio() -> None:
    first_voice_at = 10.0
    now = first_voice_at + MAX_UTTERANCE_SECONDS
    last_voice_at = now - min(0.2, SILENCE_SECONDS * 0.25)

    assert utterance_ready(
        last_voice_at=last_voice_at,
        first_voice_at=first_voice_at,
        now=now,
        endpoint=False,
    ) is True
