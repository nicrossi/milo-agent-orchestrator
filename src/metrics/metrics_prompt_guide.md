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
