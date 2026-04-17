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

#### Important considerations

- do not infer personality traits
