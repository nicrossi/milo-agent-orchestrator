"""
Signals aggregator — orchestrates the per-turn extractors into a single
UserSignals payload.

The aggregator receives:
- The current user message text.
- The rolling history (already-built signals_window of past UserSignals).
- The timestamp of the previous Milo response (for latency computation).
- The current timestamp.

It returns a fully-populated UserSignals for this turn. It does NOT mutate the
window — appending to the window is the caller's responsibility (so the engine
stays stateless).
"""
from __future__ import annotations

from typing import Optional, Sequence

from src.policy.signals.extractors import (
    extract_attempt_presence,
    extract_confusion_keywords,
    extract_direct_answer_request,
    extract_hedging,
    extract_latency_z,
    extract_message_length_z,
    extract_revision_markers,
)
from src.policy.types import UserSignals


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])


def build_user_signals(
    user_message: str,
    signals_window: Sequence[UserSignals],
    prev_milo_response_ts: Optional[float],
    now_ts: float,
    length_window: Optional[Sequence[int]] = None,
    latency_window: Optional[Sequence[float]] = None,
) -> UserSignals:
    """Build a UserSignals payload for the current turn.

    Args:
        user_message: raw text of the latest user turn.
        signals_window: prior UserSignals (most recent last). Used for backfill /
            ordering, but z-score windows are passed explicitly via length_window
            / latency_window so the aggregator stays decoupled from how the
            caller stores raw measurements.
        prev_milo_response_ts: server timestamp (epoch seconds) when the most
            recent Milo response finished streaming. None on first turn.
        now_ts: current server timestamp (epoch seconds) when the user message
            arrived.
        length_window: rolling window of prior message lengths (in words). If
            None, derived from signals_window using a placeholder of 0 (so
            z-score returns 0.0 — neutral).
        latency_window: rolling window of prior turn latencies (in seconds).

    Returns:
        UserSignals — fully populated payload for this turn.
    """
    # Latency: now - prev_milo_response_ts. None / negative => 0.0 (neutral).
    if prev_milo_response_ts is None:
        latency = -1.0  # signals "no measurement available"
    else:
        latency = max(0.0, now_ts - prev_milo_response_ts)

    length_w = list(length_window) if length_window is not None else []
    latency_w = list(latency_window) if latency_window is not None else []

    return UserSignals(
        hedging=extract_hedging(user_message),
        confusion=extract_confusion_keywords(user_message),
        attempt_present=extract_attempt_presence(user_message),
        direct_answer_request=extract_direct_answer_request(user_message),
        latency_z=extract_latency_z(latency, latency_w),
        length_z=extract_message_length_z(user_message, length_w),
        revisions=extract_revision_markers(user_message),
    )


def message_word_count(text: str) -> int:
    """Public helper for callers that need to maintain length_window externally."""
    return _word_count(text)
