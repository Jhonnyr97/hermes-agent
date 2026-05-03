---
description: ALWAYS use this skill for ANY operation involving Nextcloud (files, calendar, contacts, Talk). Use `ncx` — the only Nextcloud CLI tool. Pre-installed in the container.
---

# ncx-workspace

`ncx` is the Nextcloud CLI tool pre-installed in the container. Structured JSON output.

## Available commands

### Files (`ncx files <command>`)

| What you want | Command |
|---------------|---------|
| List directory contents | `ncx files list --path /` |
| Read file text (stdout JSON) | `ncx files read /Documents/file.txt` |
| Get metadata (size, dates, type) | `ncx files info /path` |
| Write text to file | `ncx files write /path --content "text"` |
| Upload local file | `ncx files write /path --local ./local.txt` |
| Download remote file to disk | `ncx files download /remote/path --local ./local.txt` |
| Create directory | `ncx files mkdir /path` |
| Delete file or directory | `ncx files delete /path` |
| Move/rename | `ncx files move /src /dst` |
| Copy | `ncx files copy /src /dst` |
| Search files by name | `ncx files search "query"` |
| Extract document as Markdown | `ncx files extract /path/doc.docx` |

### Calendar (`ncx calendar <command>`)

| What you want | Command |
|---------------|---------|
| List calendars | `ncx calendar list` |
| List upcoming events | `ncx calendar events --days 7 --max 10` |
| Create event | `ncx calendar create --summary "Meeting" --start "2026-05-01T14:00" --duration 1h` |
| Delete event | `ncx calendar delete <event_href>` |

### Talk (`ncx talk <command>`)

| What you want | Command |
|---------------|---------|
| List rooms | `ncx talk rooms` |
| Send message | `ncx talk send <TOKEN> --message "Hello"` |
| List messages | `ncx talk messages <TOKEN> --limit 20` |
| Create room | `ncx talk create --name "room-name" --type 3` |

### Contacts (`ncx contacts <command>`)

| What you want | Command |
|---------------|---------|
| List address books | `ncx contacts books` |
| List contacts | `ncx contacts list --max 20` |

### Activity

| What you want | Command |
|---------------|---------|
| Recent activity | `ncx activity --limit 20` |

## When to activate

Triggers on: list files, upload, download, read, extract text, calendar, contacts, Talk, "find on nextcloud", "put in nextcloud", "search nextcloud", or any operation involving Nextcloud data.
