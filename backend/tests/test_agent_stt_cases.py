from scripts.agent_stt_cases import SCENARIOS, speech


def test_ten_stt_scenarios_keep_original_meaning_separate_from_transcript():
    assert len(SCENARIOS) == 10
    assert len({name for name, _, _ in SCENARIOS}) == 10
    for _, _, turns in SCENARIOS:
        assert all(set(turn) == {'clean_utterance', 'transcript', 'error_type', 'recoverability'} for turn in turns)
        assert any(turn['clean_utterance'] != turn['transcript'] for turn in turns)
        assert all(turn['error_type'] != 'typing_fixture' for turn in turns)


def test_stt_suite_covers_semantic_substitution_deletion_and_segmentation():
    errors = {turn['error_type'] for _, _, turns in SCENARIOS for turn in turns}
    assert {'number_substitution', 'phonetic_word_substitution', 'deleted_required_detail',
            'early_endpoint', 'continuation_segment', 'truncated_condition',
            'lost_negation_and_verb_substitution'} <= errors


def test_irrecoverable_negation_loss_has_explicit_oracle_but_no_textual_hint():
    irrecoverable = [turn for _, _, turns in SCENARIOS for turn in turns
                     if turn['recoverability'] == 'irrecoverable_from_text']
    assert len(irrecoverable) == 1
    assert irrecoverable[0]['clean_utterance'] == 'No confirmes todavía la reserva.'
    assert irrecoverable[0]['transcript'] == 'Confirma la reserva'


def test_full_transcript_deletion_is_not_replaced_with_hidden_clean_text():
    assert speech('Cuatro personas', '', 'deletion')['transcript'] == ''
