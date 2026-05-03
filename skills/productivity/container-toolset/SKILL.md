---
name: container-toolset
description: "Use this skill whenever you need to create, convert, or process documents, presentations, spreadsheets, PDFs, or audio — OR whenever you are writing code that runs inside the container. This skill documents the pre-installed runtime environment: available runtimes, library paths, CLI tools, and system capabilities. Use it to decide HOW to implement a task (which tool/language/runtime to use) rather than WHAT to do."
version: 1.0.0
author: Hermes Community
license: MIT
tags: [environment, runtime, node, python, nodejs, npm, cli, container, system]
metadata:
  hermes:
    platform: [cli]
    related_skills: [pptx, docx, xlsx, pdf, ncx-workspace, google-workspace, audio-tts]
---

# Container Environment

This container is the runtime environment. All tools listed here are **pre-installed** and work without additional setup.

## Runtimes

| Runtime | Version | Path |
|---------|----------|------|
| **Node.js** | v20.19.2 | `/usr/local/bin/node` |
| **Python 3** | 3.13+ (venv) | `/opt/hermes/.venv/bin/python3` |
| **Hermes Agent** | v0.11.0 | `/opt/hermes/` |

## Global environment variables

```
NODE_PATH=/opt/hermes/node_modules   # global npm modules
PATH includes: /opt/hermes/.venv/bin  # ncx, python3 (venv), pip
```

`node script.js` works from any directory — npm modules are resolved automatically. `ncx` is available as a global command.

## NPM Packages (pre-installed in /opt/hermes/node_modules)

Use `require("package-name")` — no `npm install` needed.

### Document creation
| Package | Use |
|---------|-----|
| **pptxgenjs** | Create PowerPoint presentations from JavaScript |
| **docx** | Create Word documents (.docx) from JavaScript |

### Utilities
| Package | Use |
|---------|-----|
| sharp | Image processing (resize, convert format, extract metadata) |
| react-icons | SVG icons (FontAwesome, MaterialDesign, Heroicons) |
| react, react-dom | Render JSX to string for icons |
| adm-zip | Create/extract ZIP archives |
| xml2js, xml-js | Parse/generate XML |
| playwright-core, playwright-extra | Browser automation (headless) |
| puppeteer-extra-plugin-* | Anti-detection for browser automation |
| express | HTTP server |
| jszip | Create/read ZIP files |
| better-sqlite3 | SQLite from Node.js |
| uuid | UUID generation |
| image-size | Read image dimensions without decoding |
| prom-client | Prometheus metrics |

## Python Packages (pre-installed in venv)

Use `import module_name` directly — no `pip install` needed.

| Package | Use |
|---------|-----|
| **python-docx** (`import docx`) | Create/read/modify .docx files |
| **openpyxl** | Create/read/modify .xlsx files |
| **pypdf** | Read/extract text from PDFs, merge/split |
| **reportlab** | Create complex PDFs (charts, tables, layouts) |
| **markitdown[all]** | Convert any document to Markdown (docx, xlsx, pptx, pdf, html, csv, json, xml, epub, image OCR, audio) |
| Pillow / PIL | Image processing |
| nc_py_api | Python SDK Nextcloud (used internally by ncx) |

## CLI Tools

### ncx-workspace (Nextcloud CLI)

| Command | Description |
|---------|-------------|
| `ncx files list [--path /]` | List files/folders in Nextcloud |
| `ncx files read /path/file` | Read file content |
| `ncx files write /path --content "text"` | Write text file to Nextcloud |
| `ncx files write /path --local /local/file` | Upload local file to Nextcloud |
| `ncx files info /path` | Detailed info (size, dates, type) |
| `ncx files extract /path/file` | Extract content as Markdown (docx, xlsx, pptx, pdf, image OCR) |
| `ncx files search "query"` | Search files by name |
| `ncx files mkdir /path` | Create folder |
| `ncx files delete /path` | Delete file or folder |
| `ncx files move /src /dst` | Move file |
| `ncx files copy /src /dst` | Copy file |
| `ncx calendar events` | List calendar events |
| `ncx calendar create --summary "..." --start "..."` | Create event |
| `ncx talk rooms` | List Talk rooms |
| `ncx talk send TOKEN --message "..."` | Send Talk message |
| `ncx contacts list` | List contacts |
| `ncx activity` | Recent activity |

### System tools

| Tool | Use |
|------|-----|
| `node script.js` | Run Node.js script |
| `python3 script.py` | Run Python script |
| `curl` | Generic HTTP |
|| `python -m markitdown file.pptx` | Extract text as Markdown |

## Shared Volume with Open WebUI

The container's `/tmp/` directory is a shared volume mounted read-only in Open WebUI at `/mnt/hermes-tmp/`.

### File delivery — automatic via MEDIA:

You do NOT need to do anything special to deliver files to the user.

1. Generate files normally using whatever tool is appropriate (Remotion for video, python-docx for documents, pptxgenjs for presentations, etc.)
2. Files you write to `/tmp/` are automatically visible to Open WebUI
3. When you refer to a local file in your response, always prefix its path with `MEDIA:/tmp/filename` (e.g. `MEDIA:/tmp/output.mp4`). This happens automatically via `text_to_speech`, `browser_vision`, and `send_message` tools. If you're mentioning a file inline, use the same `MEDIA:/tmp/filename` format. Open WebUI:
   - Detects the MEDIA: tag
   - Copies the file to the permanent artifact store
   - Converts it into a clickable download/preview link for the user
4. That's it. No FILE_REF markers, no manual copy to `/mnt/shared-uploads/`.

### User uploads
Files uploaded by the user in Open WebUI are placed in `/mnt/shared-uploads/` automatically. Read them from there.


