"""
AziendaOS - Nextcloud Tools for Hermes Agent

Registers a set of Nextcloud/WebDAV tools that the agent can call
to interact with a Nextcloud instance. Uses the NextcloudConnector
internally.

Supported operations:
    - nextcloud_list_files      List directory contents
    - nextcloud_read_file       Read a file's text content
    - nextcloud_search_files    Search files by name/pattern
    - nextcloud_get_file_info   Get file metadata
    - nextcloud_write_file      Create or overwrite a file
    - nextcloud_create_directory  Create a new directory
    - nextcloud_delete_file     Delete a file or directory
    - nextcloud_move_file       Move or rename a file
    - nextcloud_copy_file       Copy a file
    - nextcloud_check_connection  Verify connection to Nextcloud

Environment variables (set in Docker Compose or .env):
    NEXTCLOUD_URL
    NEXTCLOUD_USER
    NEXTCLOUD_PASSWORD
"""

import logging
import os
from typing import Optional

from connectors.nextcloud import NextcloudConnector

logger = logging.getLogger(__name__)

# ── Global singleton (lazy-init) ─────────────────────────────────────
_connector: Optional[NextcloudConnector] = None


def _get_connector() -> NextcloudConnector:
    """Return the singleton connector, initialised on first call."""
    global _connector
    if _connector is None:
        _connector = NextcloudConnector()
    return _connector


def check_nextcloud_requirements() -> bool:
    """Check that required environment variables are set."""
    return all(os.environ.get(v) for v in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_PASSWORD"))


# ── Tool handlers ────────────────────────────────────────────────────

def handle_nextcloud_list_files(args: dict, **kw) -> str:
    """List files and directories in a Nextcloud path."""
    path = args.get("path", "/")
    depth = int(args.get("depth", 1))
    connector = _get_connector()
    result = connector.list_files(path, depth=depth)
    if not result.get("success"):
        return f"Error listing {path}: {result.get('error', 'unknown error')}"

    entries = result.get("entries", [])
    if not entries:
        return f"Directory '{path}' is empty."

    lines = [f"📁 Contents of {path} ({result['total']} items):\n"]
    for e in entries:
        icon = "📁" if e["is_directory"] else "📄"
        size_str = f" ({e['size']} bytes)" if not e["is_directory"] else ""
        lines.append(f"  {icon} {e['name']}{size_str}")
    return "\n".join(lines)


def handle_nextcloud_read_file(args: dict, **kw) -> str:
    """Read the contents of a file from Nextcloud."""
    path = args.get("path", "")
    if not path:
        return "Error: 'path' parameter is required."

    connector = _get_connector()
    result = connector.read_file(path)
    if not result.get("success"):
        return f"Error reading '{path}': {result.get('error', 'unknown error')}"

    content = result.get("content", "")
    size = result.get("size", 0)
    mime = result.get("mimetype", "unknown")

    # Truncate very long content to avoid blowing the context window
    MAX_LEN = 50_000
    if len(content) > MAX_LEN:
        content = content[:MAX_LEN] + f"\n\n[... truncated at {MAX_LEN} characters ...]"

    return (
        f"📄 {path} ({size} bytes, {mime})\n"
        f"---\n"
        f"{content}"
    )


def handle_nextcloud_search_files(args: dict, **kw) -> str:
    """Search for files in Nextcloud by name pattern."""
    pattern = args.get("pattern", "")
    path = args.get("path", "/")
    if not pattern:
        return "Error: 'pattern' parameter is required."

    connector = _get_connector()
    result = connector.search_files(pattern, path=path)
    if not result.get("success"):
        return f"Error searching: {result.get('error', 'unknown error')}"

    entries = result.get("entries", [])
    if not entries:
        return f"No files matching '{pattern}' found under {path}."

    lines = [f"🔍 Search results for '{pattern}' ({result['total']} found):\n"]
    for e in entries:
        icon = "📁" if e["is_directory"] else "📄"
        lines.append(f"  {icon} {e['path']}" if e["is_directory"] else f"  {icon} {e['path']} ({e['size']} bytes)")
    return "\n".join(lines)


def handle_nextcloud_get_file_info(args: dict, **kw) -> str:
    """Get metadata for a file or directory in Nextcloud."""
    path = args.get("path", "")
    if not path:
        return "Error: 'path' parameter is required."

    connector = _get_connector()
    result = connector.get_file_info(path)
    if not result.get("success"):
        return f"Error getting info for '{path}': {result.get('error', 'unknown error')}"

    entry = result["entry"]
    icon = "📁" if entry["is_directory"] else "📄"
    return (
        f"{icon} {entry['path']}\n"
        f"  Name: {entry['name']}\n"
        f"  Type: {'Directory' if entry['is_directory'] else 'File'}\n"
        f"  Size: {entry['size']} bytes\n"
        f"  MIME: {entry['mimetype']}\n"
        f"  Modified: {entry['last_modified'] or 'N/A'}"
    )


def handle_nextcloud_write_file(args: dict, **kw) -> str:
    """Create or overwrite a file in Nextcloud."""
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return "Error: 'path' parameter is required."
    if not content:
        return "Error: 'content' parameter is required."

    connector = _get_connector()
    result = connector.write_file(path, content)
    if not result.get("success"):
        return f"Error writing '{path}': {result.get('error', 'unknown error')}"

    return f"✅ File written: {path} ({result.get('size', 0)} bytes)"


def handle_nextcloud_create_directory(args: dict, **kw) -> str:
    """Create a new directory in Nextcloud."""
    path = args.get("path", "")
    if not path:
        return "Error: 'path' parameter is required."

    connector = _get_connector()
    result = connector.create_directory(path)
    if not result.get("success"):
        return f"Error creating directory '{path}': {result.get('error', 'unknown error')}"

    return f"✅ Directory created: {path}"


def handle_nextcloud_delete_file(args: dict, **kw) -> str:
    """Delete a file or empty directory in Nextcloud."""
    path = args.get("path", "")
    if not path:
        return "Error: 'path' parameter is required."

    connector = _get_connector()
    result = connector.delete_file(path)
    if not result.get("success"):
        return f"Error deleting '{path}': {result.get('error', 'unknown error')}"

    return f"✅ Deleted: {path}"


def handle_nextcloud_move_file(args: dict, **kw) -> str:
    """Move or rename a file/directory in Nextcloud."""
    src = args.get("src", "")
    dst = args.get("dst", "")
    if not src or not dst:
        return "Error: both 'src' and 'dst' parameters are required."

    connector = _get_connector()
    result = connector.move_file(src, dst)
    if not result.get("success"):
        return f"Error moving '{src}' to '{dst}': {result.get('error', 'unknown error')}"

    return f"✅ Moved: {src} → {dst}"


def handle_nextcloud_copy_file(args: dict, **kw) -> str:
    """Copy a file in Nextcloud."""
    src = args.get("src", "")
    dst = args.get("dst", "")
    if not src or not dst:
        return "Error: both 'src' and 'dst' parameters are required."

    connector = _get_connector()
    result = connector.copy_file(src, dst)
    if not result.get("success"):
        return f"Error copying '{src}' to '{dst}': {result.get('error', 'unknown error')}"

    return f"✅ Copied: {src} → {dst}"


def handle_nextcloud_check_connection(args: dict, **kw) -> str:
    """Verify the connection to Nextcloud."""
    connector = _get_connector()
    result = connector.check_connection()
    if result.get("success"):
        return (
            f"✅ Connected to Nextcloud\n"
            f"  URL: {result.get('url', 'N/A')}\n"
            f"  User: {result.get('user', 'N/A')}"
        )
    return f"❌ Connection failed: {result.get('error', 'unknown error')}"


# ── Tool schemas ─────────────────────────────────────────────────────

NEXTCLOUD_LIST_FILES_SCHEMA = {
    "name": "nextcloud_list_files",
    "description": "List files and directories in a Nextcloud path. Returns names, sizes, and types of all items.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path in Nextcloud (default: '/'). Example: '/Documents', '/Photos/2024'",
                "default": "/",
            },
            "depth": {
                "type": "integer",
                "description": "How deep to recurse. 1 = immediate children only, 2 = one level deeper, -1 = all.",
                "default": 1,
            },
        },
        "required": [],
    },
}

NEXTCLOUD_READ_FILE_SCHEMA = {
    "name": "nextcloud_read_file",
    "description": "Read the full text content of a file from Nextcloud. Supports text files, markdown, code files, and plaintext documents.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Full path to the file in Nextcloud. Example: '/Documents/contratto.pdf', '/Notes/riunione.md'",
            },
        },
        "required": ["path"],
    },
}

NEXTCLOUD_SEARCH_FILES_SCHEMA = {
    "name": "nextcloud_search_files",
    "description": "Search for files in Nextcloud by name. Supports exact name, substring, and glob patterns like '*.pdf', 'report*', '*budget*'.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern. Examples: 'contratto', '*.pdf', 'report*', 'budget*'",
            },
            "path": {
                "type": "string",
                "description": "Root path to search under (default: '/').",
                "default": "/",
            },
        },
        "required": ["pattern"],
    },
}

NEXTCLOUD_GET_FILE_INFO_SCHEMA = {
    "name": "nextcloud_get_file_info",
    "description": "Get detailed metadata about a file or directory in Nextcloud: name, size, type, MIME, and last modified date.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file or directory. Example: '/Documents/contratto.pdf'",
            },
        },
        "required": ["path"],
    },
}

NEXTCLOUD_WRITE_FILE_SCHEMA = {
    "name": "nextcloud_write_file",
    "description": "Create a new file or overwrite an existing one in Nextcloud. Content must be plain text.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Destination path. Example: '/Documents/appunti.txt'",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
}

NEXTCLOUD_CREATE_DIRECTORY_SCHEMA = {
    "name": "nextcloud_create_directory",
    "description": "Create a new directory in Nextcloud. Parent directories must already exist.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path for the new directory. Example: '/NuovoProgetto/Documenti'",
            },
        },
        "required": ["path"],
    },
}

NEXTCLOUD_DELETE_FILE_SCHEMA = {
    "name": "nextcloud_delete_file",
    "description": "Delete a file or empty directory from Nextcloud. Cannot delete non-empty directories.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to delete. Example: '/Vecchi/file.txt'",
            },
        },
        "required": ["path"],
    },
}

NEXTCLOUD_MOVE_FILE_SCHEMA = {
    "name": "nextcloud_move_file",
    "description": "Move or rename a file or directory in Nextcloud.",
    "parameters": {
        "type": "object",
        "properties": {
            "src": {
                "type": "string",
                "description": "Source path. Example: '/Vecchi/documento.txt'",
            },
            "dst": {
                "type": "string",
                "description": "Destination path. Example: '/Nuovi/documento.txt'",
            },
        },
        "required": ["src", "dst"],
    },
}

NEXTCLOUD_COPY_FILE_SCHEMA = {
    "name": "nextcloud_copy_file",
    "description": "Copy a file to a new location in Nextcloud.",
    "parameters": {
        "type": "object",
        "properties": {
            "src": {
                "type": "string",
                "description": "Source path.",
            },
            "dst": {
                "type": "string",
                "description": "Destination path.",
            },
        },
        "required": ["src", "dst"],
    },
}

NEXTCLOUD_CHECK_CONNECTION_SCHEMA = {
    "name": "nextcloud_check_connection",
    "description": "Test the connection to the Nextcloud server. Returns status, URL, and authenticated user.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


# ── Registry ─────────────────────────────────────────────────────────
from tools.registry import registry, tool_error  # noqa: E402

registry.register(
    name="nextcloud_check_connection",
    toolset="nextcloud",
    schema=NEXTCLOUD_CHECK_CONNECTION_SCHEMA,
    handler=handle_nextcloud_check_connection,
    check_fn=check_nextcloud_requirements,
    emoji="🔌",
)

registry.register(
    name="nextcloud_list_files",
    toolset="nextcloud",
    schema=NEXTCLOUD_LIST_FILES_SCHEMA,
    handler=handle_nextcloud_list_files,
    check_fn=check_nextcloud_requirements,
    emoji="📁",
)

registry.register(
    name="nextcloud_read_file",
    toolset="nextcloud",
    schema=NEXTCLOUD_READ_FILE_SCHEMA,
    handler=handle_nextcloud_read_file,
    check_fn=check_nextcloud_requirements,
    emoji="📄",
)

registry.register(
    name="nextcloud_search_files",
    toolset="nextcloud",
    schema=NEXTCLOUD_SEARCH_FILES_SCHEMA,
    handler=handle_nextcloud_search_files,
    check_fn=check_nextcloud_requirements,
    emoji="🔍",
)

registry.register(
    name="nextcloud_get_file_info",
    toolset="nextcloud",
    schema=NEXTCLOUD_GET_FILE_INFO_SCHEMA,
    handler=handle_nextcloud_get_file_info,
    check_fn=check_nextcloud_requirements,
    emoji="ℹ️",
)

registry.register(
    name="nextcloud_write_file",
    toolset="nextcloud",
    schema=NEXTCLOUD_WRITE_FILE_SCHEMA,
    handler=handle_nextcloud_write_file,
    check_fn=check_nextcloud_requirements,
    emoji="✏️",
)

registry.register(
    name="nextcloud_create_directory",
    toolset="nextcloud",
    schema=NEXTCLOUD_CREATE_DIRECTORY_SCHEMA,
    handler=handle_nextcloud_create_directory,
    check_fn=check_nextcloud_requirements,
    emoji="📁",
)

registry.register(
    name="nextcloud_delete_file",
    toolset="nextcloud",
    schema=NEXTCLOUD_DELETE_FILE_SCHEMA,
    handler=handle_nextcloud_delete_file,
    check_fn=check_nextcloud_requirements,
    emoji="🗑️",
)

registry.register(
    name="nextcloud_move_file",
    toolset="nextcloud",
    schema=NEXTCLOUD_MOVE_FILE_SCHEMA,
    handler=handle_nextcloud_move_file,
    check_fn=check_nextcloud_requirements,
    emoji="🚚",
)

registry.register(
    name="nextcloud_copy_file",
    toolset="nextcloud",
    schema=NEXTCLOUD_COPY_FILE_SCHEMA,
    handler=handle_nextcloud_copy_file,
    check_fn=check_nextcloud_requirements,
    emoji="📋",
)
