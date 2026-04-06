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
