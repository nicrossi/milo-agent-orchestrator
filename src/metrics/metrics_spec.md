# Metrics Feature Spec

## Overview

This document defines Milo's metrics feature.

The goal of this feature is to transform a post-class student interaction into three interpretable pedagogical metrics for teachers:

- Reflection Quality
- Calibration between perception and performance
- Contextual Transfer

These metrics are designed to support teacher decision-making after a class or topic closure.

---

## Feature Goal

Provide teachers with structured insights about how a student finishes a learning instance, based on a guided post-class interaction.

The feature does not aim to generate a final grade or replace formal assessment. Its purpose is to surface interpretable evidence that helps teachers identify which students may need further support and what kind of support may be more appropriate.

---

## Scope

- a single post-class interaction
- no longitudinal progress calculation
- no single aggregate score

This feature evaluates evidence from one specific interaction only. Metrics should not be interpreted as a personality trait.

---

## Metrics

### 1. Reflection Quality

#### Definition
Measures how deeply the student can explain what they understood, what they found difficult, and what meaning they gave to the topic at the end of the class.

#### Observable signals
- clarity in explaining what was understood
- ability to mention a difficulty or confusion
- ability to explain what helped understanding
- ability to express meaning or relevance

#### Levels
- **Red**: the response is vague, purely descriptive, or does not show real reflection
- **Yellow**: the response shows partial reflection, but it is still superficial or incomplete
- **Green**: the response clearly explains understanding, difficulty, or meaning in a coherent way

#### Teacher interpretation
- **Red**: the student may need more scaffolding to verbalize learning
- **Yellow**: the student shows partial evidence but may still need guided reflection
- **Green**: the student shows solid reflective evidence for that interaction

---

### 2. Calibration between Perception and Performance

#### Definition
Measures how aligned the student's self-perception is with what the student actually demonstrates in the interaction.

#### Observable signals
- explicit confidence or self-assessment
- consistency between confidence and demonstrated understanding
- mismatch between what the student claims and what the student explains

#### Levels
- **Red**: strong misalignment between perceived understanding and demonstrated understanding
- **Yellow**: partial or moderate alignment
- **Green**: good alignment between self-perception and demonstrated understanding

#### Teacher interpretation
- **Red**: the student may be overconfident or underconfident and may need guided self-evaluation
- **Yellow**: the student shows some alignment, but not consistently
- **Green**: the student seems able to position themselves reasonably well relative to their own understanding

---

### 3. Contextual Transfer

#### Definition
Measures whether the student can connect the concept to a meaningful, real-life, or relevant context beyond the formal classroom definition.

#### Observable signals
- ability to mention a concrete real-world context
- ability to explain why the concept makes sense in that context
- ability to move beyond memorized or purely formal definitions

#### Levels
- **Red**: the student cannot connect the topic to a meaningful context
- **Yellow**: the student mentions a context, but the connection is vague, weak, or forced
- **Green**: the student connects the concept to a clear and meaningful context and explains why it fits

#### Teacher interpretation
- **Red**: the student may still see the topic as something to memorize rather than understand
- **Yellow**: the student is beginning to connect the concept with meaning, but only partially
- **Green**: the student shows evidence that the concept has acquired meaningful value beyond rote learning

---

## Output expectations

For each student interaction, the feature should produce:

- one value per metric: red, yellow, or green
- a brief justification
- one or more evidence snippets from the student interaction
- a suggested teacher action

---

## Teacher-facing value

These metrics are useful because they do not only show whether a student may need attention. They also suggest what kind of pedagogical intervention could be more appropriate.

Examples:
- low reflection quality may suggest guided reflective prompts
- low calibration may suggest self-evaluation support
- low contextual transfer may suggest recontextualization with real-life examples

---

## Non-goals

This feature should not:

- assign a final grade
- infer intelligence, motivation, or personality
- diagnose the student
- claim progress over time without a baseline
- replace teacher judgment