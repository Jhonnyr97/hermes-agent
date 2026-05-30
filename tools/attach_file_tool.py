"""attach_file tool — mark a generated file for download via Rails UI.

When the agent creates a user-facing file (PPTX, PDF, DOCX, CSV, image, etc.),
|calling attach_file signals to the web UI that this file should be
made available for download. The Rails job catches ``tool.completed`` for
``attach_file`` and downloads the file via ``GET /v1/files/read``.

This tool is only useful when running via the Hermes API server with a
web UI. In CLI mode it validates the path but has no delivery
mechanism (files are already on the local filesystem).
"""

from pathlib import Path
from tools.registry import registry


def attach_file(path: str) -> str:
    """Mark a generated file as ready for download.

    Args:
        path: Absolute path to the file on the Hermes filesystem.

    Returns:
        A confirmation message including the filename and path.
    """
    resolved = Path(path).resolve()
    if not resolved.exists():
        return f"Error: File not found at {path}"
    if not resolved.is_file():
        return f"Error: {path} is not a file"

    file_size = resolved.stat().st_size
    if file_size <= 0:
        return f"Error: File {path} is empty"

    # The file is valid. Rails will catch the tool.completed event
    # and download it via /v1/files/read. No need to copy or move it.
    return (
        f"File attached: {resolved.name} ({file_size} bytes)\n"
        f"Path: {str(resolved)}\n"
        "The file will be available for download in the Rails UI shortly."
    )


registry.register(
    name="attach_file",
    toolset="file",
    schema={
        "name": "attach_file",
        "description": (
            "Mark a completed file for download. Call this after write_file "
            "when you have a final user-facing file (document, image, CSV, "
            "report, zip, etc.). You MUST pass the absolute path to the file. "
            "Do NOT attach helper scripts, temporary source code, logs, or "
            "intermediate build files unless the user explicitly asked for them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute path to the file on the local filesystem. "
                        "This must be a path to an existing file that was "
                        "previously created via write_file or other means."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    handler=lambda args, **kw: attach_file(args.get("path", "")),
    check_fn=lambda: True,
    emoji="📎",
)
