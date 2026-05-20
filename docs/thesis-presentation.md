---
marp: true
theme: uncover
class: invert
paginate: true
size: 16:9
style: |
  section {
    font-family: 'Inter', 'Helvetica Neue', sans-serif;
    font-size: 26px;
    text-align: left;
    padding: 60px 80px;
    justify-content: flex-start;
  }
  section.title {
    text-align: center;
    justify-content: center;
  }
  h1 {
    font-size: 44px;
    color: #ffffff;
    margin-bottom: 24px;
  }
  h2 {
    font-size: 32px;
    color: #9ecbff;
    margin-bottom: 18px;
  }
  h3 {
    font-size: 24px;
    color: #c0d6ff;
  }
  strong { color: #ffd479; }
  em { color: #c0d6ff; }
  blockquote {
    border-left: 4px solid #ffd479;
    padding-left: 18px;
    color: #e0e0e0;
    font-style: normal;
  }
  table {
    font-size: 22px;
    margin-top: 12px;
  }
  th { background: #2a2a3a; }
  td, th { padding: 8px 14px; }
  code {
    background: #1f2330;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 22px;
  }
  ul, ol { line-height: 1.5; }
  .small { font-size: 20px; color: #aab; }
  .label { color: #ffd479; font-weight: 600; }
---

<!-- _class: title invert -->

# Milo

## An AI metacognitive agent that refuses to give answers

<br>

Thesis Defense — ITBA
Grupo 09

<!--
Speaker note:
Open with the one-liner. Don't read the title — say it: "Milo is an AI tutor whose only output is questions. Today I'll explain why that matters, how it works, and show you what it produces that no other tool can."
-->

---

## The problem

Generative AI broke a hidden assumption of education:

> that the work students hand in is evidence of the thinking that produced it.

When a student asks ChatGPT for an answer, three things happen at once:

- The **product** improves — the essay, the proof, the code
- The **process** disappears — no struggle, no reasoning trace
- The **signal** disappears with it — the teacher cannot tell who actually understood

This is not a cheating problem. It is a **measurement collapse**.

<!--
Speaker note:
Frame this carefully. Don't say "students are cheating." Say: the artifacts teachers used to grade are no longer correlated with cognition. Learning is invisible, and what is invisible cannot be taught, corrected, or graded. This is the thesis premise — everything follows from it.
-->

---

## Who has this problem

**The student** — passes the assignment, fails the exam, doesn't know why.
AI gave them fluency without competence. They lose the metacognitive habit
of *noticing what they don't know*.

**The teacher** — used to read a wrong answer and infer the misconception
behind it. Now every answer is technically correct, so misconceptions go
undetected until the next high-stakes assessment.

**The institution** — accreditation depends on certifying competence.
If grades no longer map to cognition, grades stop being a credential.

<!--
Speaker note:
Don't list — explain how the same root problem manifests differently for each. The student feels it last (delayed cost). The teacher feels it daily (broken diagnostic). The institution feels it structurally (broken credential). Same disease, three symptoms.
-->

---

## The solution

# Milo is an AI tutor whose only output is questions.

It refuses to give answers, follows a pedagogical state machine,
and turns each conversation into a structured record of how the student thought.

<br>

|                  | **ChatGPT**            | **Milo**                       |
| ---------------- | ---------------------- | ------------------------------ |
| Optimizes for    | the artifact           | the trace                      |
| Replaces         | the student's thinking | the teacher's missing signal   |
| Output           | a correct answer       | a recorded reasoning process   |

<!--
Speaker note:
This slide is the pivot. Pause after the one-liner. The contrast table is the categorical difference — read it left to right. ChatGPT optimizes for the artifact. Milo optimizes for the trace. Don't soften this — it's what makes the thesis defensible against "isn't this just another AI tool."
-->

---

## How it works — end-to-end

Six steps, one continuous flow:

1. **Teacher** creates a reflection activity with a learning goal
2. **Student** opens the activity → Milo starts with a question
3. Each turn runs through a **policy engine** that decides what kind of question to ask
4. The LLM's draft response is **audited**: any answer is rewritten into a question
5. On submit, the full transcript is **scored** against a fixed rubric
6. **Teacher** opens an analytics view ranked by intervention urgency

<!--
Speaker note:
Set expectations: I'll walk through these six steps, each with what the user does, what Milo does, and what gets captured. The thread to follow: the conversation is not the product — the structured record is.
-->

---

## Step 1 — Teacher creates an activity

<span class="label">User action</span>
Teacher writes a learning goal: *"students should understand why a binary search needs a sorted input"* and assigns it to a course.

<span class="label">What Milo does</span>
Stores the goal and context that will frame every conversation.

<span class="label">What is captured</span>
The pedagogical intent — the rubric Milo will hold the student against.

<span class="label">Why this matters</span>
Milo's questioning is **goal-directed**, not generic.
It knows what the student is supposed to learn.

<!--
Speaker note:
The teacher is not abdicating control. The teacher writes the goal — Milo enforces it. This is the inversion of usual AI tutoring, where the AI decides what's important.
-->

---

## Step 2 — Student opens the activity

<span class="label">User action</span>
Student clicks an assigned activity. A WebSocket connects.

<span class="label">What Milo does</span>
Sends an unsolicited opening question grounded in the activity goal —
not *"how can I help?"* but *"how would you describe this problem in your own words?"*

<span class="label">What is captured</span>
The session begins in the **PLANNING** state of a finite state machine.

<span class="label">Why this matters</span>
The student is forced to externalize their starting point
**before any AI assistance is possible**.

<!--
Speaker note:
The opening question is not chitchat. It anchors the entire session. The student cannot defer thinking to Milo because Milo defers thinking back to the student from turn one.
-->

---

## Step 3 — Per-turn policy decision

<span class="label">User action</span>
Student types a reply.

<span class="label">What Milo does</span>
Extracts five signals from the text:
*attempt_present · hedging · confusion · miscalibration · affect_load*

A finite state machine then chooses a question family —
goal clarification, self-explanation, calibration probe,
discrepancy detection, transfer probe, or **stabilize** if overwhelmed.

<span class="label">What is captured</span>
A structured signature of this turn — what reasoning markers appeared,
what state the student is in.

<span class="label">Why this matters</span>
Milo is not free-styling. Every question belongs to a pedagogical family
**chosen by rules, not vibes**.

<!--
Speaker note:
This is the part that makes Milo defensible as a research artifact, not just an LLM wrapper. The five signals + FSM + question families are explicit, auditable, and grounded in metacognition literature. Walk through one: hedging detection routes to a calibration question. Confusion + affect_load triggers stabilization mode.
-->

---

## Step 4 — Output audit (the no-answer guarantee)

<span class="label">User action</span>
None — happens between LLM draft and the student's screen.

<span class="label">What Milo does</span>
Scores every sentence of the LLM's reply for **open-endedness**.
If no sentence is sufficiently open — i.e., the LLM tried to give an answer —
Milo **rewrites the response** by appending the planned Socratic question
and logs a `direct_answer_leakage` event.

<span class="label">What is captured</span>
A guarantee that what reaches the student contains a real question,
plus a metric showing how often the LLM tried to slip.

<span class="label">Why this matters</span>
"No answers" is **not a prompt instruction** — it is a deterministic output filter.
The system enforces the policy structurally.

<!--
Speaker note:
This is the most defensible slide. Every "AI safety via prompting" approach is brittle. Milo's no-answer policy is enforced by code, not by asking the model nicely. Anticipate the question: "what if the LLM is clever?" — answer: it doesn't matter, the filter runs after generation, every time.
-->

---

## Step 5 — Session evaluation

<span class="label">User action</span>
Student submits.

<span class="label">What Milo does</span>
Closes the session and runs a second LLM pass over the **full transcript**
against a fixed rubric.

<span class="label">What is captured</span>
Three labeled, evidence-backed dimensions:

- **Reflection Quality** — descriptive · basic · deep · exceptional
- **Calibration** — misaligned · partial · aligned
- **Contextual Transfer** — lacking · vague · meaningful

Each label is anchored to **quoted sentences from the student** as evidence.

<span class="label">Why this matters</span>
A free-text conversation becomes a **structured row in a database**.
Conversations become comparable across students, classes, and cohorts.

<!--
Speaker note:
The evaluation is the bridge from individual artifact to institutional metric. Without this step, Milo would be a nice tutoring tool. With it, Milo is an assessment instrument. The evidence-citation requirement is what makes the labels trustworthy — every grade has receipts.
-->

---

## Step 6 — Teacher analytics

<span class="label">User action</span>
Teacher opens an activity's results page.

<span class="label">What Milo does</span>
Plots every student on a **calibration quadrant** —
*well-calibrated · overconfident · underconfident · struggling* —
with the most at-risk students sorted to the top,
each annotated with reflection-quality flags and a recommended action.

<span class="label">What is captured</span>
A per-class diagnostic the teacher could not previously obtain from any artifact.

<span class="label">Why this matters</span>
The teacher walks into class **already knowing who needs which intervention**.

<!--
Speaker note:
Close the loop. Step 1 was the teacher setting intent. Step 6 is the teacher receiving evidence against that intent. Every step in between exists to make this final view trustworthy.
-->

---

## Why this actually solves the problem

| Problem                                        | Mechanism                                                  | Outcome                                            |
| ---------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------- |
| Students bypass thinking by asking for answers | Output filter deterministically rewrites answers into questions | The only path forward is to articulate reasoning   |
| AI fluency hides misconceptions                | Signal extractors + FSM probe for confusion, hedging, overconfidence | Misconceptions are *elicited on purpose*, not discovered post-hoc |
| Teachers lost the diagnostic signal of wrong answers | Post-session evaluator produces evidence-cited labels per dimension | Teachers regain a diagnostic — richer than wrong answers ever were |
| Learning is invisible at the cohort level       | Calibration quadrant ranked by urgency                     | Teacher attention is allocated by data, not intuition |

<br>

**The core argument:** Milo replaces a broken artifact (the answer)
with a new artifact (the scored reasoning trace).

<!--
Speaker note:
This is the slide tutors should remember. Walk it row by row — name the problem, name the mechanism, name the outcome. Don't generalize. Each row is a closed loop. End on the bold line: the artifact has changed.
-->

---

<!-- _class: title invert -->

# Demo

## Two paths, two minutes

A first-year CS student.
Activity: *Why does binary search require a sorted input?*

<!--
Speaker note:
Set the scene. Same student, same question, two different tools. Watch what each one produces.
-->

---

## Path A — ChatGPT (10 seconds)

> **Student:** *"why does binary search need a sorted array?"*

> **ChatGPT:** *"Binary search needs a sorted array because at each step it compares the target to the middle element and discards half the array based on the comparison. Without sorting, this comparison gives no information…"*

<br>

Student copy-pastes. Submits. Gets a good grade.

**Cannot explain it on the exam two weeks later.**
Teacher learns this in week 12.

<!--
Speaker note:
Read the ChatGPT response in a slightly bored, fluent voice — that's the point. It's correct. It's articulate. It's worthless as evidence of learning. The cost is invisible until the exam.
-->

---

## Path B — Milo (90 seconds, part 1)

> **Milo:** *"Before we look at why, can you tell me in your own words what binary search actually does, step by step?"*

> **Student:** *"It like, splits the array in half and looks at the middle?"*

> **Milo:** *"Good start. When you look at the middle element, what decision do you make next, and what information are you using to make it?"*

> **Student:** *"If the middle is bigger than what I'm looking for, I go left. If smaller, I go right."*

<!--
Speaker note:
Slow down on Milo's questions. They're not generic — they're scoped. "What information are you using" forces the student to surface the implicit assumption. The student doesn't know yet that this assumption is the whole point.
-->

---

## Path B — Milo (part 2)

> **Milo:** *"Now suppose the array were `[5, 1, 8, 3, 7]` and you're looking for `3`. The middle is `8`. What does 'go left' mean here?"*

> **Student:** *"Wait. The numbers on the left aren't smaller than 8. So 'go left' doesn't actually mean 'where smaller numbers are.'"*

> **Milo:** *"Exactly that. So what assumption was your rule silently relying on?"*

> **Student:** *"That the array is sorted. Otherwise the comparison doesn't tell me which half the answer is in."*

<!--
Speaker note:
This is the moment. The student rebuilt the mental model in real time. The misconception was named ("go left = where smaller numbers are"), and the invariant emerged as the student's own conclusion. Pause here — let the audience feel it.
-->

---

## Behind the scenes

- Turn 1 → `attempt_present = False` · FSM in **PLANNING** · question family = `ATTEMPT_ELICITATION`
- Turn 3 → `miscalibration` high (confident but incomplete rule) → `DISCREPANCY_DETECTION` → counterexample probe
- Turn 5 → student sentence logged as evidence for `Reflection Quality = deep`
- Throughout → `direct_answer_leakage = 0`

<br>

The dialogue you just saw was **not improvised**.
It was the output of a deterministic policy.

<!--
Speaker note:
Show the receipts. The Socratic dialogue looks like good teaching. It's actually the visible surface of an explicit policy decision at every turn. This is what makes the system reproducible and auditable — every choice has a label.
-->

---

## What the teacher sees

For this student:

> **Reflection Quality:** deep
> **Calibration:** aligned
> **Transfer:** vague
> **Evidence:** *"Otherwise the comparison doesn't tell me which half the answer is in."*
> **Recommended action:** Ready for transfer probe — try a follow-up activity on invariants in other divide-and-conquer algorithms.

Across the class of 40, **three students** show `Calibration: misaligned` (overconfident) on this exact concept.

The teacher opens tomorrow's class with that.

<!--
Speaker note:
This is what the teacher could never get before. Not "this student got 8/10" but "this student rebuilt their model at this sentence, and three classmates haven't yet." Granularity that previously required one-on-one tutoring, delivered at scale.
-->

---

## Why this changes everything

The ChatGPT version produced a correct paragraph and **zero learning evidence**.

Milo produced no paragraph and a **complete diagnostic record**.

<br>

# The artifact has changed.

<!--
Speaker note:
This is the punch line. Say it slowly. The artifact has changed. Everything we used to assess — papers, problem sets, exams — was built around a world where the artifact was the evidence. That world is over. Milo proposes what comes next.
-->

---

<!-- _class: title invert -->

# Value synthesis

---

## In one sentence

# Milo replaces the answer — the artifact AI made worthless — with a scored, evidence-backed record of how the student thought.

<!--
Speaker note:
If you remember nothing else, remember this sentence. It's the elevator version. The answer used to be evidence; AI broke that; Milo proposes a different evidence.
-->

---

## Three pillars

**It cannot give answers, by construction.**
Not a prompt instruction — a deterministic output filter that rewrites
any answer into a question.

**It produces structured cognitive evidence.**
Every session ends as a row with reflection quality, calibration,
and transfer — each cited with the student's own words.

**It restores the teacher's diagnostic surface.**
A class-level dashboard ranked by intervention urgency,
derived from how students reasoned, not from what they submitted.

<!--
Speaker note:
Three pillars, three guarantees. Each one stands alone. Together they're the system.
-->

---

<!-- _class: title invert -->

## The future of education is not protecting assessment from AI.

## It is using AI to assess the one thing AI cannot fake:

# the student's own reasoning, made visible.

<!--
Speaker note:
End. No further slides. Sit with it. Then invite questions.
-->

---

<!-- _class: title invert -->

# Thank you

## Questions?

