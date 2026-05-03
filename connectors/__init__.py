"""
AziendaOS - File Connector Interface

Defines the abstract base class for all file storage connectors.
Each connector (Nextcloud, Google Drive, OneDrive, etc.) extends
FileConnector and implements the same set of operations.

Usage:
    class MyCloudConnector(FileConnector):
        def list_files(self, path="/", depth=1): ...
        def read_file(self, path): ...
        def write_file(self, path, content, mimetype=None): ...
        def search_files(self, pattern, path="/"): ...
        def delete_file(self, path): ...
        def get_file_info(self, path): ...
        def move_file(self, src, dst): ...

Design principles:
    - All paths are relative to the connector root
    - Methods return plain dicts (serializable, LLM-friendly)
    - Errors are raised as FileConnectorError with structured context
    - No credentials in code — read from environment at init
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class FileConnectorError(Exception):
    """Base exception for all file connector operations."""

    def __init__(self, message: str, operation: str, path: str = "", details: Optional[dict] = None):
        self.operation = operation
        self.path = path
        self.details = details or {}
        super().__init__(f"[{operation}] {message}")


class AuthenticationError(FileConnectorError):
    """Raised when credentials are missing or invalid."""

    def __init__(self, message: str = "Authentication failed. Check credentials."):
        super().__init__(message, operation="auth", details={"credential_hint": "Check env vars"})


class NotFoundError(FileConnectorError):
    """Raised when a file or path does not exist."""

    def __init__(self, path: str):
        super().__init__(f"Path not found: {path}", operation="read", path=path)


class PermissionError_(FileConnectorError):
    """Raised when the user lacks permission for an operation."""

    def __init__(self, path: str, operation: str):
        super().__init__(f"Permission denied for {operation} on {path}", operation=operation, path=path)


@dataclass
class FileInfo:
    """Normalised metadata for a file or directory."""

    path: str
    name: str
    is_directory: bool
    size: int = 0
    mimetype: str = "application/octet-stream"
    last_modified: Optional[str] = None  # ISO-8601
    etag: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "is_directory": self.is_directory,
            "size": self.size,
            "mimetype": self.mimetype,
            "last_modified": self.last_modified,
            "etag": self.etag,
        }


class FileConnector(ABC):
    """
    Abstract interface for file storage backends.

    All paths are connector-relative strings (e.g. "/Documents/contract.pdf").
    The root "/" corresponds to the user's home or root folder in the service.

    Subclasses MUST implement all abstract methods.
    Subclasses MUST read credentials from environment variables, never from code.
    """

    def __init__(self, name: str = "generic"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    # ── Read operations ────────────────────────────────────────────────

    @abstractmethod
    def list_files(self, path: str = "/", depth: int = 1) -> dict:
        """
        List files and directories under *path*.

        Args:
            path: Directory to list (connector-relative).
            depth: 1 = immediate children only, -1 = recursive.

        Returns:
            {"success": True, "entries": [FileInfo.to_dict(), ...]}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def read_file(self, path: str) -> dict:
        """
        Read the full content of a file.

        Args:
            path: Path to the file.

        Returns:
            {"success": True, "content": "...", "mimetype": "...", "size": N}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def search_files(self, pattern: str, path: str = "/") -> dict:
        """
        Search for files by name pattern (glob or substring).

        Args:
            pattern: Search pattern (e.g. "*.pdf", "budget*", "contratto").
            path: Root path to search under.

        Returns:
            {"success": True, "entries": [FileInfo.to_dict(), ...]}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def get_file_info(self, path: str) -> dict:
        """
        Get metadata for a single file or directory.

        Returns:
            {"success": True, "entry": FileInfo.to_dict()}
            {"success": False, "error": "..."}
        """
        ...

    # ── Write operations ───────────────────────────────────────────────

    @abstractmethod
    def write_file(self, path: str, content: str, mimetype: Optional[str] = None) -> dict:
        """
        Create or overwrite a file.

        Args:
            path: Destination path.
            content: Text content.
            mimetype: Optional MIME type override.

        Returns:
            {"success": True, "path": "..."}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def create_directory(self, path: str) -> dict:
        """
        Create a directory (and parents if needed).

        Returns:
            {"success": True, "path": "..."}
            {"success": False, "error": "..."}
        """
        ...

    # ── Mutate operations ──────────────────────────────────────────────

    @abstractmethod
    def delete_file(self, path: str) -> dict:
        """
        Delete a file or empty directory.

        Returns:
            {"success": True}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def move_file(self, src: str, dst: str) -> dict:
        """
        Move or rename a file/directory.

        Returns:
            {"success": True, "src": src, "dst": dst}
            {"success": False, "error": "..."}
        """
        ...

    @abstractmethod
    def copy_file(self, src: str, dst: str) -> dict:
        """
        Copy a file.

        Returns:
            {"success": True, "src": src, "dst": dst}
            {"success": False, "error": "..."}
        """
        ...

    # ── Connector health ───────────────────────────────────────────────

    @abstractmethod
    def check_connection(self) -> dict:
        """
        Verify the connector can reach the remote service.

        Returns:
            {"success": True, "connector": "...", "user": "..."}
            {"success": False, "error": "..."}
        """
        ...
