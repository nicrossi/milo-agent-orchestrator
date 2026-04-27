"""
Smoke-test runner for iterating on the PolicyEngine without the server.

Usage:
    .venv/bin/python3.11 scripts/try_policy.py

Simulates a multi-turn conversation and prints FSM transitions, selected
questions, applied rules, and interceptor firings. Edit the `TURNS` list
below to craft scenarios you want to validate.
"""
from src.policy.engine import PolicyEngine
from src.policy.types import FSMState, PolicyContext, UserSignals

# (user_message, confidence_1_to_5, simulated_llm_output)
TURNS = [
    ("hola",                              3, "¿Qué querés lograr en esta sesión?"),
    ("quiero entender recursion",         3, "Contame más sobre qué te cuesta."),
    ("dame la respuesta",                 3, "La respuesta es usar recursividad."),   # expects no_direct_answers + interceptor
    ("probe varias ideas",                4, "Buen avance. ¿Cómo podrías validarlo?"),
    ("creo que ya entendi",               5, "Excelente. ¿Cómo aplicarías esto en otro problema?"),
    ("no se, estoy perdido",              1, "Respiremos. Volvamos al plan inicial."),
]


def main():
    engine = PolicyEngine()
    state = FSMState.PLANNING
    asked_ids: list[str] = []

    print(f"{'turn':>4} {'state→next':25} {'qid':10} {'rules':30} intercept?")
    print("-" * 90)

    for turn_idx, (msg, conf, raw_llm) in enumerate(TURNS):
        ctx = PolicyContext(
            current_state=state,
            turn_count=turn_idx,
            recent_question_ids=asked_ids.copy(),
            user_message=msg,
            user_signals=UserSignals(confidence=conf),
        )
        decision = engine.evaluate(ctx)
        was_intercepted, final = engine.check_output(raw_llm, decision)

        transition = f"{state.value}→{decision.next_state.value}"
        rules = ",".join(decision.applied_rules) or "-"
        print(f"{turn_idx:>4} {transition:25} {decision.plan.question_id:10} {rules:30} {'YES' if was_intercepted else 'no'}")
        if was_intercepted:
            print(f"     ↳ corrected output tail: …{final[-80:]!r}")

        state = decision.next_state
        asked_ids.append(decision.plan.question_id)

    print()


if __name__ == "__main__":
    main()
