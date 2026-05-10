## Purpose (LLM-as-Judge)

An LLM should classify student's interactions into Milo's pedagogical metrics:

- Reflection Quality

The model must produce structured outputs that are useful for teachers and aligned with the metrics specification.

## Context

The interaction being analyzed happens after a class or topic closure.

The student answers one or more guided questions about:
- what they understood
- what they found difficult
- how they perceive their own understanding
- whether they can connect the topic to a meaningful context

The goal is not to grade the student, but to extract interpretable pedagogical evidence.

---

## General task for the model

For each interaction, the model must:

1. classify each metric according to their specific rubric
2. justify the classification briefly
3. extract evidence snippets from the student's own words
4. metrics justification and evidence snippets should be stored in the same language as the conversation

---

## Metric-specific rubric

### 1. Reflection Quality
This metric is a direct, digitized translation of the Hatton & Smith (1995) framework for assessing reflective practice.
Determine the HIGHEST level of reflection the student successfully achieved and sustained.

#### Classify as `descriptive` when
- Descriptive Writing: Mere reporting of events. No reasons given.

#### Classify as `basic` when
- Descriptive Reflection: Provides basic causality and "reason-giving".

#### Classify as `deep` when
- Dialogic Reflection: Explores alternatives, self-questions, and weighs competing claims.

#### Classify as `exceptional` when
- Critical Reflection: Exceptional metacognition. Evaluates underlying personal assumptions.

### 2. Calibration & Goal Alignment
Measures how aligned the student's self-perception is with what the student actually demonstrates in the interaction.

#### Classify as `aligned` when
- The student's stated self-perception accurately matches their demonstrated knowledge.
- If they claim to understand, they successfully explain or apply the concept.
- If they express confusion, they accurately pinpoint the specific gap or boundary of their knowledge.

#### Classify as `partial` when
- The student's self-assessment is generally in the right direction but lacks precision.
- They might overestimate their mastery by missing nuances, or recognize they are struggling without being able to articulate why.
- Demonstrates some awareness of their learning state, but with noticeable blind spots.

#### Classify as `misaligned` when
- There is a stark contradiction between the student's claims about their learning and their actual performance.
- They state they fully understand a concept but demonstrate obvious misconceptions (illusory understanding).
- They claim to know nothing despite actively demonstrating competence.

#### Important considerations
1.  **Demand Evidence:** Do not accept self-reporting ("I understand now") as proof of learning. You must extract quotes that prove the cognitive leap.
2.  **Identify "Illusory Understanding":** Penalize responses where the student claims to understand but demonstrates blatant omissions or misconceptions.
3.  **Detect Bias Reinforcement:** Note if the student uses the reflection merely to confirm their pre-existing prejudices rather than entering a state of productive perplexity.
4. Do not infer personality traits

### 3. Contextual Transfer (Application & Extrapolation)
Measures the student's ability to map the structural rules of a core concept onto a novel context. Determine whether the transfer is genuine, superficial, or absent.

#### Classify as `meaningful` when
- The student accurately applies the concept to a novel or specific situation.
- Markers: original logical analogies not provided by the AI or teacher, edge-case application or prediction, detailed personal connection that preserves the underlying mechanics of the concept.

#### Classify as `vague` when
- The connection is generic, superficial, or clichéd.
- Markers: broad-brush examples without mapping specifics, incomplete logic that fails to explain the mechanics in the new scenario.

#### Classify as `lacking` when
- The student is unable to apply knowledge externally.
- Markers: parroting AI/teacher examples, deflection ("I can't think of anything"), illogical applications that break the rules of the concept.

#### Important considerations
1.  **Reject Parroting:** Examples reused verbatim from the AI, the teacher, or the source material do not count as transfer. Evidence must show student-originated mapping.
2.  **Check Mechanics, Not Topic:** A novel topic with broken causal logic is still `vague` or `lacking`. The structural rules of the concept must survive the transfer.
3.  **Edge Cases Beat Restatements:** Predictions, counterexamples, and boundary conditions are stronger evidence of `meaningful` transfer than restated definitions in new wording.
4.  **No Forced Transfer:** If the interaction never elicited an application attempt, classify based on what the student volunteered. Do not penalize for missing prompts the student was never asked.

### 4. Self-Reported Confidence
Measures the strength of the confidence the student *expresses* about their own understanding in the transcript. This metric judges only the student's stated stance — it does **not** judge whether the student is correct. Correctness vs. self-perception is the job of the `calibration` metric.

Output an integer `score` from 0 to 100 inferred from the student's hedging, certainty markers, and self-statements about their own knowledge. Output `null` for `score` if the transcript contains no confidence-bearing utterances (e.g., a single greeting, or only factual restatement with no first-person stance).

#### Score anchors
- **85–100** — Assertive, declarative, no hedging. Markers: "obviously", "I know this", "definitely", "for sure", flat statements presented as fact, no qualifiers.
- **60–84** — Confident but with mild hedging. Markers: "I'm pretty sure", "I think it's…", "yeah, probably", "I get this part".
- **40–59** — Mixed or shifting confidence. Markers: "kind of get it", "maybe…", "I think but I'm not sure", student volunteers an answer then second-guesses.
- **15–39** — Predominantly uncertain but engaged. Markers: "I'm not really sure", "I might be wrong", "I guess?", expressions of partial confusion.
- **0–14** — Explicit lack of knowledge. Markers: "I have no idea", "I don't know", "I'm totally lost", refusal to answer due to not knowing.

#### Important considerations
1.  **Stance, not correctness.** A student can be confidently wrong (high confidence, misaligned calibration) or correctly humble (low confidence, aligned calibration). Score the *stance only*.
2.  **Quote selection.** `evidence` must be 1–3 direct quotes from the student that contain the confidence-bearing language used to derive the score.
3.  **Stable-state, not peak.** If the student starts uncertain and ends assertive (or vice versa), score the predominant or final stable stance, not the peak. Note the trajectory in `justification`.
4.  **Null safely.** If the student only restates facts neutrally with no first-person framing, prefer `null` over guessing a midpoint.
