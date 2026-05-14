"""Clarify gateway — blocking notify/resolve mechanism for API server.

Lighter than tools/approval.py — no session-level approve/deny state, no
pattern keys, no plugin hooks.  Just a threading.Event-based rendezvous:

    agent_thread:  clarify_web_tool()
                     └─ _notify_cb(question_data)  → SSE event
                     └─ entry.event.wait()          ← BLOCK
                     └─ return entry.response

    http_handler:  resolve_clarify(session_key, response)
                     └─ entry.response = response
                     └─ entry.event.set()            ← UNBLOCK
"""

import contextvars
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Per-session identity (thread-safe via contextvars)
# ---------------------------------------------------------------------------

_clarify_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarify_session_key",
    default="",
)


def set_current_session_key(session_key: str) -> contextvars.Token[str]:
    """Set the current thread's clarify session key.

    Returns the previous token so callers can restore it with
    ``_clarify_session_key.reset(token)``.
    """
    return _clarify_session_key.set(session_key)


# ---------------------------------------------------------------------------
# Blocking entry
# ---------------------------------------------------------------------------


class _ClarifyEntry:
    """One pending clarify question inside a gateway session."""

    __slots__ = ("event", "question", "choices", "response")

    def __init__(self, question: str, choices: Optional[list[str]]):
        self.event = threading.Event()
        self.question = question
        self.choices = choices or []
        self.response: Optional[str] = None


_clarify_entries: dict[str, _ClarifyEntry] = {}   # session_key → _ClarifyEntry
_clarify_notify_cbs: dict[str, object] = {}         # session_key → callable


# ---------------------------------------------------------------------------
# Notify registration
# ---------------------------------------------------------------------------


def register_clarify_notify(session_key: str, cb) -> None:
    """Register a per-session callback for sending clarify requests to the user.

    The callback signature is ``cb(question: str, choices: list[str]) -> None``.
    The callback bridges sync→async (runs in the agent thread, must schedule
    the actual send on the event loop).
    """
    with _lock:
        _clarify_notify_cbs[session_key] = cb


def unregister_clarify_notify(session_key: str) -> None:
    """Unregister the per-session clarify notify callback.

    Unblocks any waiting thread so it doesn't hang forever.
    """
    with _lock:
        _clarify_notify_cbs.pop(session_key, None)
        entry = _clarify_entries.pop(session_key, None)
    if entry is not None:
        entry.event.set()


def get_clarify_notify(session_key: str):
    """Return the notify callback for *session_key*, or None."""
    with _lock:
        return _clarify_notify_cbs.get(session_key)


# ---------------------------------------------------------------------------
# Block & resolve
# ---------------------------------------------------------------------------


def has_pending_clarify(session_key: str) -> bool:
    """Check if a session has a pending clarify question."""
    with _lock:
        entry = _clarify_entries.get(session_key)
        return entry is not None and not entry.event.is_set()


def resolve_clarify(session_key: str, response: str) -> bool:
    """Unblock the waiting agent thread with the user's response.

    Returns True if a pending entry was resolved, False if nothing was pending.
    """
    with _lock:
        entry = _clarify_entries.pop(session_key, None)
    if entry is None:
        return False
    entry.response = response
    entry.event.set()
    return True


def wait_for_clarify(
    session_key: str,
    question: str,
    choices: Optional[list[str]],
    timeout: float = 300.0,
) -> str:
    """Called from the agent thread inside clarify_web_tool().

    Registers a pending entry, fires the notify callback so the gateway can
    push an SSE event, then blocks until resolve_clarify() is called or the
    timeout expires.

    Returns the user's response string.
    """
    entry = _ClarifyEntry(question, choices)

    with _lock:
        _clarify_entries[session_key] = entry
        notify_cb = _clarify_notify_cbs.get(session_key)

    if notify_cb is not None:
        try:
            notify_cb(question, choices)
        except Exception:
            logger.exception("clarify notify callback failed for session %s", session_key)

    # Block until resolved or timeout
    resolved = entry.event.wait(timeout=timeout)

    with _lock:
        _clarify_entries.pop(session_key, None)

    if not resolved:
        return (
            "The user did not provide a response within the time limit. "
            "Use your best judgement to proceed."
        )

    return entry.response or ""
