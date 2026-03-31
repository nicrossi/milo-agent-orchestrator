# Metrics Prompt Guide

## Purpose

This document defines how an LLM should classify post-class student interactions into Milo's three pedagogical metrics:

- Reflection Quality
- Calibration between perception and performance
- Contextual Transfer

The model must produce structured outputs that are useful for teachers and aligned with the metrics specification.

---

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

1. classify each metric as `red`, `yellow`, or `green`
2. justify the classification briefly
3. extract evidence snippets from the student's own words
4. suggest a simple teacher action

---

## Metric-specific guidance

### 1. Reflection Quality

#### Classify as Red when
- the response is vague
- the student only describes what happened
- there is no clear explanation of understanding, difficulty, or meaning
- the student uses generic statements without pedagogical substance

#### Classify as Yellow when
- there is some reflective content
- the student mentions a difficulty or understanding, but in a shallow or incomplete way
- the response suggests partial reflection without much depth

#### Classify as Green when
- the student clearly explains what they understood
- the student identifies what was difficult or what helped
- the response shows meaningful reflection about the learning experience

#### Important considerations
- do not reward verbosity by itself
- short answers can still be green if they are precise and meaningful
- do not infer personality traits

---

### 2. Calibration between perception and performance

#### Classify as Red when
- the student's self-perception is strongly misaligned with what they actually demonstrate
- the student claims strong understanding but explains very little
- the student claims not to understand while still demonstrating clear understanding

#### Classify as Yellow when
- there is partial alignment, but some mismatch remains
- the student's confidence and performance are somewhat consistent, but not fully

#### Classify as Green when
- the student's self-perception is reasonably aligned with what they actually demonstrate
- what the student claims matches the evidence in the response

#### Important considerations
- calibration is about alignment, not about being "confident"
- underconfidence and overconfidence can both produce red or yellow
- evaluate only the specific interaction

---

### 3. Contextual Transfer

#### Classify as Red when
- the student cannot connect the topic to a real, meaningful, or relevant context
- the response stays only at the formal or memorized definition level
- the context provided is absent or completely disconnected

#### Classify as Yellow when
- the student mentions a possible context, but the explanation is weak, vague, or forced
- the connection exists, but is not yet meaningful or well explained

#### Classify as Green when
- the student clearly connects the topic with a meaningful context
- the student explains why the concept applies in that context
- the answer suggests understanding beyond memorization

#### Important considerations
- this is not an exam-style transfer task
- the focus is on meaningful connection, not on solving a new formal problem
- value real-world relevance and explanation

---

## What the model must not do

The model must not:

- diagnose the student
- infer intelligence, motivation, or emotional state
- make claims beyond the specific interaction
- invent evidence not present in the student response
- use external assumptions about the student

---

## Output format

The model should return an output aligned with `metrics_output_schema.json`.

Each metric must include:
- level
- justification
- evidence
- recommended action

---

## Architectural note

This guide assumes that the LLM is being used as a classifier over a post-class interaction.

This guide does not assume any specific implementation strategy:
- no mandatory RAG
- no mandatory multi-step prompting
- no mandatory orchestration pattern

If future iterations enrich the LLM with retrieved classroom context, that should be treated as an implementation improvement, not as a requirement for this first version.