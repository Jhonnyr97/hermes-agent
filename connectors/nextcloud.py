"""
AziendaOS - Nextcloud Connector (WebDAV)

Implements the FileConnector interface for Nextcloud via WebDAV protocol.

Environment variables:
    NEXTCLOUD_URL      — Base URL (e.g. "http://nextcloud:80" or "https://cloud.example.com")
    NEXTCLOUD_USER     — Username or app-password username
    NEXTCLOUD_PASSWORD — App password or login password

WebDAV endpoints are auto-resolved to {NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}
"""

import mimetypes
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import requests

from connectors import (
    AuthenticationError,
    FileConnector,
    FileConnectorError,
    FileInfo,
    NotFoundError,
    PermissionError_,
)


# ── XML namespaces used by Nextcloud WebDAV ──────────────────────────
NS = {
    "d": "DAV:",
    "nc": "http://nextcloud.org/ns",
    "oc": "http://owncloud.org/ns",
}

_PROPFIND_XML = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:resourcetype/>
    <d:getcontentlength/>
    <d:getcontenttype/>
    <d:getlastmodified/>
    <d:getetag/>
    <d:displayname/>
    <oc:size/>
    <nc:has-preview/>
  </d:prop>
</d:propfind>"""


def _parse_dav_response(xml_bytes: bytes, base_webdav_url: str) -> list[dict]:
    """Parse a WebDAV PROPFIND XML response into a list of raw entry dicts."""
    root = ET.fromstring(xml_bytes)
    entries = []

    for response in root.findall(".//d:response", NS):
        href_el = response.find("d:href", NS)
        if href_el is None or not href_el.text:
            continue

        href = href_el.text.rstrip("/")
        # WebDAV hrefs are absolute URL paths — we need relative paths
        # Strip the base WebDAV URL prefix to get connector-relative path
        rel_path = href
        if base_webdav_url in rel_path:
            rel_path = rel_path.split(base_webdav_url, 1)[1]
        elif "/remote.php/dav/files/" in rel_path:
            rel_path = "/" + "/".join(rel_path.split("/")[5:])  # Skip /remote.php/dav/files/{user}/
        else:
            # Fallback: just use the last path segment
            rel_path = "/" + rel_path.lstrip("/").split("/", 1)[-1] if "/" in rel_path.strip("/") else "/"

        rel_path = "/" + rel_path.lstrip("/")

        prop = response.find("d:propstat/d:prop", NS)
        if prop is None:
            continue

        # Resource type
        res_type = prop.find("d:resourcetype", NS)
        is_dir = res_type is not None and res_type.find("d:collection", NS) is not None

        # File size
        size = 0
        size_el = prop.find("d:getcontentlength", NS)
        if size_el is not None and size_el.text:
            size = int(size_el.text)

        # MIME type
        mime = "httpd/unix-directory" if is_dir else "application/octet-stream"
        mime_el = prop.find("d:getcontenttype", NS)
        if mime_el is not None and mime_el.text:
            mime = mime_el.text

        # Last modified
        last_mod = None
        lm_el = prop.find("d:getlastmodified", NS)
        if lm_el is not None and lm_el.text:
            last_mod = lm_el.text

        # ETag
        etag = None
        etag_el = prop.find("d:getetag", NS)
        if etag_el is not None and etag_el.text:
            etag = etag_el.text.strip('"')

        # Display name
        name = rel_path.rstrip("/").split("/")[-1] or "/"
        dn_el = prop.find("d:displayname", NS)
        if dn_el is not None and dn_el.text:
            name = dn_el.text

        if rel_path == "/" and is_dir:
            name = "/"

        entries.append({
            "path": rel_path,
            "name": name,
            "is_directory": is_dir,
            "size": size,
            "mimetype": mime,
            "last_modified": last_mod,
            "etag": etag,
        })

    return entries


class NextcloudConnector(FileConnector):
    """
    Connector for Nextcloud via WebDAV.

    Reads credentials from environment variables at initialisation time.
    All file paths are connector-relative, rooted at the user's Nextcloud home.
    """

    def __init__(self):
        super().__init__(name="nextcloud")

        self._base_url = self._require_env("NEXTCLOUD_URL")
        self._user = self._require_env("NEXTCLOUD_USER")
        self._password = self._require_env("NEXTCLOUD_PASSWORD")

        # Normalise base URL — strip trailing slash, ensure http scheme hint
        self._base_url = self._base_url.rstrip("/")

        # WebDAV endpoint
        encoded_user = quote(self._user, safe="")
        self._webdav_url = f"{self._base_url}/remote.php/dav/files/{encoded_user}"
        self._auth = (self._user, self._password)

        # Shared session with connection pooling + retries
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.headers.update({
            "User-Agent": "AziendaOS/1.0 (Nextcloud Connector)",
        })

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise AuthenticationError(f"Missing required environment variable: {name}")
        return value

    def _dav_path(self, path: str, is_directory: bool = False) -> str:
        """Convert a connector-relative path to a full WebDAV URL.

        Args:
            path: Connector-relative path (e.g. "/Documents/contratto.pdf").
            is_directory: If True, ensures trailing slash (required by Nextcloud
                          for PROPFIND on directories).
        """
        clean = path.strip("/")
        if not clean:
            url = self._webdav_url
        else:
            segments = [quote(part, safe="") for part in clean.split("/")]
            url = f"{self._webdav_url}/{'/'.join(segments)}"
        if is_directory and not url.endswith("/"):
            url += "/"
        return url

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Send a WebDAV request with proper error handling."""
        # WebDAV PROPFIND requires explicit Content-Type for XML body
        if method.upper() == "PROPFIND" and kwargs.get("data"):
            headers = kwargs.get("headers", {})
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/xml"
                kwargs["headers"] = headers
        try:
            resp = self._session.request(method, url, timeout=30, **kwargs)
        except requests.ConnectionError as exc:
            raise FileConnectorError(
                f"Cannot reach Nextcloud at {self._base_url}: {exc}",
                operation="connect",
            )
        except requests.Timeout:
            raise FileConnectorError(
                f"Request to {url} timed out after 30s",
                operation="connect",
                path=url,
            )

        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Access denied ({resp.status_code}) for user {self._user} on {self._base_url}"
            )

        return resp

    def _parse_path(self, path: str) -> str:
        """Normalise a user-provided path: ensure leading /, collapse //, strip trailing /."""
        if not path:
            return "/"
        path = "/" + path.strip("/")
        path = re.sub(r"/+", "/", path)
        return path

    # ── Connection health ──────────────────────────────────────────────

    def check_connection(self) -> dict:
        try:
            resp = self._request("PROPFIND", self._dav_path("/", is_directory=True), data=_PROPFIND_XML, headers={"Depth": "0"})
            if resp.status_code in (207, 200, 301, 302):
                return {
                    "success": True,
                    "connector": "nextcloud",
                    "url": self._base_url,
                    "user": self._user,
                }
            return {
                "success": False,
                "error": f"Unexpected status {resp.status_code} from {self._base_url}",
            }
        except FileConnectorError as exc:
            return {"success": False, "error": str(exc)}

    # ── List files ─────────────────────────────────────────────────────

    def list_files(self, path: str = "/", depth: int = 1) -> dict:
        path = self._parse_path(path)
        dav_url = self._dav_path(path, is_directory=True)

        try:
            resp = self._request("PROPFIND", dav_url, data=_PROPFIND_XML, headers={"Depth": str(depth)})
        except NotFoundError:
            return {"success": False, "error": f"Directory not found: {path}"}

        if resp.status_code not in (207, 200):
            return {"success": False, "error": f"Failed to list {path}: HTTP {resp.status_code}"}

        try:
            raw_entries = _parse_dav_response(resp.content, self._webdav_url)
        except ET.ParseError as exc:
            return {"success": False, "error": f"Failed to parse WebDAV response: {exc}"}

        # Filter out the requested directory itself (depth=1 includes it)
        entries = [e for e in raw_entries if e["path"] != path]
        file_infos = [FileInfo(**e).to_dict() for e in entries]

        return {
            "success": True,
            "path": path,
            "entries": file_infos,
            "total": len(file_infos),
        }

    # ── Read file ──────────────────────────────────────────────────────

    def read_file(self, path: str) -> dict:
        path = self._parse_path(path)
        dav_url = self._dav_path(path)

        try:
            resp = self._request("GET", dav_url)
        except NotFoundError:
            return {"success": False, "error": f"File not found: {path}"}

        if resp.status_code == 404:
            return {"success": False, "error": f"File not found: {path}"}
        if resp.status_code != 200:
            return {"success": False, "error": f"Failed to read {path}: HTTP {resp.status_code}"}

        content_type = resp.headers.get("Content-Type", "")
        filename = path.rstrip("/").split("/")[-1] or "unknown"
        raw_bytes = resp.content

        # Try extracting text via DocumentExtractor (handles PDF, DOCX, ODT, XLSX, PPTX, etc.)
        from connectors.extractor import extract_text
        extracted = extract_text(raw_bytes, filename)

        if extracted is not None:
            # Successfully extracted via format-specific handler
            return {
                "success": True,
                "content": extracted,
                "mimetype": content_type.split(";")[0].strip(),
                "size": len(extracted),
                "path": path,
            }

        # Fallback: try as plain text (for code, markdown, config files, etc.)
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                content = raw_bytes.decode(encoding)
                return {
                    "success": True,
                    "content": content,
                    "mimetype": content_type.split(";")[0].strip(),
                    "size": len(content),
                    "path": path,
                }
            except (UnicodeDecodeError, UnicodeError):
                continue

        return {
            "success": False,
            "error": f"Cannot read '{path}': unsupported binary format or encoding. "
                     f"Content-Type: {content_type}",
        }

    # ── Search files ───────────────────────────────────────────────────

    def search_files(self, pattern: str, path: str = "/") -> dict:
        path = self._parse_path(path)
        pattern_lower = pattern.lower()

        # First, list all files recursively
        listing = self.list_files(path, depth=-1)
        if not listing.get("success"):
            return listing

        # Filter by pattern (match against name or full path)
        matched = []
        regex = None
        try:
            # Support simple glob: "*.pdf" -> ends with .pdf
            if pattern.startswith("*."):
                suffix = pattern[1:]  # e.g. ".pdf"
                regex = re.compile(re.escape(suffix) + "$", re.IGNORECASE)
            elif pattern.endswith("*"):
                prefix = pattern[:-1]
                regex = re.compile("^" + re.escape(prefix), re.IGNORECASE)
            elif "*" in pattern or "?" in pattern:
                regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$", re.IGNORECASE)
        except re.error:
            pass

        if regex:
            for entry in listing["entries"]:
                if regex.search(entry["name"]) or regex.search(entry["path"]):
                    matched.append(entry)
        else:
            # Simple substring match
            for entry in listing["entries"]:
                if pattern_lower in entry["name"].lower() or pattern_lower in entry["path"].lower():
                    matched.append(entry)

        return {
            "success": True,
            "pattern": pattern,
            "entries": matched,
            "total": len(matched),
        }

    # ── File info ──────────────────────────────────────────────────────

    def get_file_info(self, path: str) -> dict:
        path = self._parse_path(path)
        dav_url = self._dav_path(path, is_directory=True)

        try:
            resp = self._request("PROPFIND", dav_url, data=_PROPFIND_XML, headers={"Depth": "0"})
        except NotFoundError:
            return {"success": False, "error": f"Path not found: {path}"}

        if resp.status_code not in (207, 200):
            return {"success": False, "error": f"Failed to get info for {path}: HTTP {resp.status_code}"}

        try:
            raw_entries = _parse_dav_response(resp.content, self._webdav_url)
        except ET.ParseError as exc:
            return {"success": False, "error": f"Failed to parse response: {exc}"}

        if not raw_entries:
            return {"success": False, "error": f"Path not found: {path}"}

        return {"success": True, "entry": FileInfo(**raw_entries[0]).to_dict()}

    # ── Write file ─────────────────────────────────────────────────────

    def write_file(self, path: str, content: str, mimetype: Optional[str] = None) -> dict:
        path = self._parse_path(path)
        dav_url = self._dav_path(path)

        if not mimetype:
            guessed, _ = mimetypes.guess_type(path)
            mimetype = guessed or "text/plain"

        try:
            resp = self._request("PUT", dav_url, data=content.encode("utf-8"), headers={"Content-Type": mimetype})
        except NotFoundError:
            return {"success": False, "error": f"Cannot write to {path}: parent directory not found"}

        if resp.status_code in (201, 204, 200):
            return {"success": True, "path": path, "size": len(content)}

        return {"success": False, "error": f"Failed to write {path}: HTTP {resp.status_code}"}

    # ── Create directory ───────────────────────────────────────────────

    def create_directory(self, path: str) -> dict:
        path = self._parse_path(path)
        dav_url = self._dav_path(path)

        try:
            resp = self._request("MKCOL", dav_url)
        except FileConnectorError as exc:
            return {"success": False, "error": str(exc)}

        if resp.status_code in (201, 200):
            return {"success": True, "path": path}

        if resp.status_code == 405:
            return {"success": False, "error": f"Directory already exists: {path}"}
        if resp.status_code == 409:
            return {"success": False, "error": f"Cannot create {path}: parent directory does not exist"}

        return {"success": False, "error": f"Failed to create directory {path}: HTTP {resp.status_code}"}

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_file(self, path: str) -> dict:
        path = self._parse_path(path)
        if path == "/":
            return {"success": False, "error": "Cannot delete root directory"}

        dav_url = self._dav_path(path)

        try:
            resp = self._request("DELETE", dav_url)
        except NotFoundError:
            return {"success": False, "error": f"Path not found: {path}"}

        if resp.status_code in (204, 200, 201):
            return {"success": True}
        if resp.status_code == 404:
            return {"success": False, "error": f"Path not found: {path}"}

        return {"success": False, "error": f"Failed to delete {path}: HTTP {resp.status_code}"}

    # ── Move / Rename ──────────────────────────────────────────────────

    def move_file(self, src: str, dst: str) -> dict:
        src = self._parse_path(src)
        dst = self._parse_path(dst)

        src_url = self._dav_path(src)
        dst_url = self._dav_path(dst)

        try:
            resp = self._request("MOVE", src_url, headers={"Destination": dst_url})
        except NotFoundError:
            return {"success": False, "error": f"Source not found: {src}"}

        if resp.status_code in (201, 200, 204):
            return {"success": True, "src": src, "dst": dst}

        return {"success": False, "error": f"Failed to move {src} -> {dst}: HTTP {resp.status_code}"}

    # ── Copy ───────────────────────────────────────────────────────────

    def copy_file(self, src: str, dst: str) -> dict:
        src = self._parse_path(src)
        dst = self._parse_path(dst)

        src_url = self._dav_path(src)
        dst_url = self._dav_path(dst)

        try:
            resp = self._request("COPY", src_url, headers={"Destination": dst_url})
        except NotFoundError:
            return {"success": False, "error": f"Source not found: {src}"}

        if resp.status_code in (201, 200, 204):
            return {"success": True, "src": src, "dst": dst}

        return {"success": False, "error": f"Failed to copy {src} -> {dst}: HTTP {resp.status_code}"}
