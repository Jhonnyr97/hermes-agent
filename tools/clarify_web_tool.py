"""clarify_web tool — ask the user a question via the Web UI (Rails).

Works like the CLI ``clarify`` tool, but emits ``clarify.requested`` SSE events
and blocks the agent thread until the user responds in the Rails UI.

The CLI counterpart stays at ``tools/clarify_tool.py`` — both tools share the
same schema shape but use different transport (CLI callback vs SSE + HTTP).

Usage from agent:
    clarify_web(question="Which approach?", choices=["Option A", "Option B"])
"""

import json
from typing import List, Optional

from tools.clarify_gateway import (
    get_clarify_notify,
    set_current_session_key,
    wait_for_clarify,
)
from tools.registry import registry


MAX_CHOICES = 4


def clarify_web_tool(
    question: str,
    choices: Optional[List[str]] = None,
    session_key: str = "",
    timeout: float = 300.0,
) -> str:
    """Ask a clarifying question via the Rails Web UI.

    Args:
        question: The question to present.
        choices: Up to 4 predefined answer choices. Omit for open-ended.
        session_key: Gateway session key (set by api_server.py).
        timeout: Max seconds to wait for user response.

    Returns:
        JSON string with the user's response.
    """
    if not question or not question.strip():
        return json.dumps(
            {"error": "Question text is required."},
            ensure_ascii=False,
        )

    question = question.strip()

    # Validate and trim choices
    if choices is not None:
        if not isinstance(choices, list):
            return json.dumps(
                {"error": "choices must be a list of strings."},
                ensure_ascii=False,
            )
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None  # empty list → open-ended

    if not session_key:
        return json.dumps(
            {"error": "Clarify Web is not available in this execution context."},
            ensure_ascii=False,
        )

    # Set thread-local session key so the gateway module finds it.
    token = set_current_session_key(session_key)

    try:
        user_response = wait_for_clarify(
            session_key=session_key,
            question=question,
            choices=choices,
            timeout=timeout,
        )
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to get user input: {exc}"},
            ensure_ascii=False,
        )
    finally:
        # Restore previous context var value
        import contextvars as _cv
        _cv.copy_context().run(lambda: None)

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": str(user_response).strip(),
    }, ensure_ascii=False)


def check_clarify_web_requirements() -> bool:
    """clarify_web has no external requirements — always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

CLARIFY_WEB_SCHEMA = {
    "name": "clarify_web",
    "description": (
        "Ask the user a question when you need clarification, feedback, or a "
        "decision before proceeding. Supports two modes:\n\n"
        "1. **Multiple choice** — provide up to 4 choices. The user picks one "
        "or types their own answer via a text input.\n"
        "2. **Open-ended** — omit choices entirely. The user types a free-form "
        "response.\n\n"
        "Use this tool when:\n"
        "- The task is ambiguous and you need the user to choose an approach\n"
        "- You want post-task feedback ('How did that work out?')\n"
        "- A decision has meaningful trade-offs the user should weigh in on\n\n"
        "Do NOT use this for simple yes/no confirmation of dangerous "
        "commands (the approval system handles that). Prefer making a "
        "reasonable default choice yourself when the decision is low-stakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to present to the user.",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": (
                    "Up to 4 answer choices. Omit this parameter entirely to "
                    "ask an open-ended question. When provided, the UI "
                    "automatically shows a text input as an 'Other' option."
                ),
            },
        },
        "required": ["question"],
    },
}


# --- Registry ---
registry.register(
    name="clarify_web",
    toolset="clarify_web",
    schema=CLARIFY_WEB_SCHEMA,
    handler=lambda args, **kw: clarify_web_tool(
        question=args.get("question", ""),
        choices=args.get("choices"),
        session_key=kw.get("session_key", ""),
    ),
    check_fn=check_clarify_web_requirements,
    emoji="❓",
)
