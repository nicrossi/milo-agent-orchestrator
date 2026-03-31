# Few-Shot Examples for Metrics Evaluation

The following are annotated examples of real student interactions with their correct metric classifications. Use these as reference when evaluating a new interaction.

---

## Example A — All Green

**Topic:** Linear functions as a relationship between variables

**Transcript:**
Milo: What did you understand today that you didn't understand before?
Student: I used to think a function was just a calculation with x and y. Now I understood that it's used to show how one thing depends on another.
Milo: Can you give me an example?
Student: Yes, for example the cost of a trip depends on the kilometers. If you travel more, you pay more.
Milo: What part is still hard for you?
Student: Sometimes I struggle going from the problem to the graph, but I did understand the general idea.

**Correct classification:**
- reflection_quality: green
- calibration: green
- contextual_transfer: green

**Why:**
- Reflection is green: she explicitly compares her understanding before and after, and identifies a specific remaining difficulty.
- Calibration is green: she says she understood the general idea, and she effectively demonstrates it with a correct explanation.
- Transfer is green: she connects the concept with a clear and coherent everyday situation and explains why it fits.

---

## Example B — All Red

**Topic:** Linear functions as a relationship between variables

**Transcript:**
Milo: What did you understand today that you didn't understand before?
Student: I honestly understood everything, it seemed easy.
Milo: What is a function, then?
Student: It's a formula… like when there's x and y.
Milo: And where could it be useful outside the classroom?
Student: I don't know… I guess in math or engineering.
Milo: What part was hard for you?
Student: Nothing, I think everything went fine.

**Correct classification:**
- reflection_quality: red
- calibration: red
- contextual_transfer: red

**Why:**
- Reflection is red: the student responds superficially and does not explain what changed in their understanding.
- Calibration is red: the student claims to have understood everything, but what they demonstrate is vague and generic — a strong mismatch between self-perception and actual performance.
- Transfer is red: the student cannot connect the concept to a real or meaningful context. "Math or engineering" is not a meaningful contextual connection.

---

## Example C — Mixed (Green / Yellow / Green) — Underconfidence case

**Topic:** Linear functions as a relationship between variables

**Transcript:**
Milo: What did you understand today that you didn't understand before?
Student: I think I understood a bit more, but I'm not very sure.
Milo: What is a function in your own words?
Student: It's when one quantity changes depending on another. For example, if I buy more tickets, I pay more money.
Milo: Do you think you understood that well?
Student: More or less… I feel like I could get confused.
Milo: What part would you like to keep practicing?
Student: How to write it with letters, because I did understand the idea better than before.

**Correct classification:**
- reflection_quality: green
- calibration: yellow
- contextual_transfer: green

**Why:**
- Reflection is green: the student articulates what they understood and what they still need to practice.
- Calibration is yellow: the student underestimates themselves. They demonstrate better understanding than they believe they have — their explanation and example are correct, but they express doubt about their own understanding. This mismatch (underconfidence) is a yellow signal, not green.
- Transfer is green: the student gives a correct and meaningful everyday example and explains the relationship.
