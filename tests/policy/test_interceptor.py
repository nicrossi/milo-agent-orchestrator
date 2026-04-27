from src.policy.interceptors.direct_answer_detector import DirectAnswerDetectorInterceptor


def test_no_violation_passes_through():
    i = DirectAnswerDetectorInterceptor()
    ok, text = i.process("¿Qué estrategia pensás usar?", "¿Cómo vas con eso?")
    assert not ok
    assert text == "¿Qué estrategia pensás usar?"


def test_direct_answer_appends_question():
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("La respuesta es 42.", "¿Qué aprendiste de esto?")
    assert modified
    assert "¿Qué aprendiste de esto?" in text


def test_no_question_mark_triggers_interceptor():
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("El resultado es X, Y, Z.", "¿Cómo llegaste a eso?")
    assert modified


def test_correction_appended_as_suffix():
    i = DirectAnswerDetectorInterceptor()
    original = "El resultado es X, Y, Z."
    question = "¿Cómo llegaste a eso?"
    _, text = i.process(original, question)
    assert text.startswith(original)
    assert text.endswith(question)


def test_multiple_question_marks_clean():
    i = DirectAnswerDetectorInterceptor()
    ok, text = i.process("¿Qué aprendiste? ¿Qué cambiarías?", "fallback?")
    assert not ok


def test_direct_prefix_with_question_mark_still_triggers():
    # Output starts with direct-answer prefix — violation even if it has a ?
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("La respuesta es 42. ¿Entendiste?", "¿Qué aprendiste?")
    assert modified
    assert "¿Qué aprendiste?" in text


# --- Phase 2: adversarial cases not caught by the bare-"?" check ---

def test_only_rhetorical_question_triggers():
    # A response with ONLY a closed/rhetorical "?" — the old check let this
    # pass; the strengthened detector catches it.
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process(
        "Los herbívoros mueren de hambre eventualmente. ¿Entendiste?",
        "¿Qué te hace pensar eso?",
    )
    assert modified
    assert "¿Qué te hace pensar eso?" in text


def test_yes_no_only_question_triggers():
    # "¿Probaste con X?" is interrogative but not generative — should fire.
    i = DirectAnswerDetectorInterceptor()
    modified, _ = i.process(
        "Esa idea funciona bien para este caso. ¿Probaste con esa estrategia?",
        "¿Qué alternativas considerarías?",
    )
    # Mid-score (~0.3) is below threshold, so this fires.
    assert modified


def test_open_wh_question_passes():
    i = DirectAnswerDetectorInterceptor()
    ok, _ = i.process(
        "Hay varias formas de pensarlo. ¿Por qué creés que pasa eso?",
        "fallback",
    )
    assert not ok


def test_empty_output_replaced_with_question():
    # If the LLM produces nothing, send the planned question so the user has
    # something to engage with rather than silence.
    i = DirectAnswerDetectorInterceptor()
    modified, text = i.process("", "¿Qué pensás?")
    assert modified
    assert text == "¿Qué pensás?"
