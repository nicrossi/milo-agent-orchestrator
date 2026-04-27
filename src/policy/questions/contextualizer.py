"""
Contextualizer — pure string ops to anchor questions to the active activity.

Behavior:
  - If the question text contains "{topic}" AND we have an ActivityRef with a
    non-empty title, substitute the title.
  - Otherwise, leave the text unchanged.
  - When activity is None or title is empty, "{topic}" is replaced with a
    safe fallback ("este tema") rather than left as a literal placeholder.

No LLM calls. No regex parsing of the context_description. The selector is
expected to skip questions with placeholders when no activity is bound, but
this module is robust to that being skipped.
"""
from typing import Optional

from src.policy.types import ActivityRef

_FALLBACK_TOPIC = "este tema"
_PLACEHOLDER = "{topic}"


def contextualize(question_text: str, activity: Optional[ActivityRef]) -> str:
    if _PLACEHOLDER not in question_text:
        return question_text

    topic = (activity.title.strip() if activity and activity.title else "") or _FALLBACK_TOPIC
    return question_text.replace(_PLACEHOLDER, topic)


def has_topic_placeholder(text: str) -> bool:
    return _PLACEHOLDER in text
