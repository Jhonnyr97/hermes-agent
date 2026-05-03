---
name: ncx-workspace
description: "IMPORTANT — ALWAYS use this skill for ANY operation involving Nextcloud: listing files, reading files, uploading, downloading, extracting document content, searching, calendar, contacts, Talk messages. Use the `ncx` CLI — never use curl, raw WebDAV, Python requests, or shell commands to interact with Nextcloud. This is the only official Nextcloud connector."
version: 1.0.0
author: Hermes Community
license: MIT
tags: [Nextcloud, WebDAV, CalDAV, CardDAV, Talk, Files, Calendar, Contacts, Activity, OCS]
required_env_vars:
  - NEXTCLOUD_URL — Base URL (e.g. https://cloud.example.com)
  - NEXTCLOUD_USER — Username or app-password user
  - NEXTCLOUD_PASSWORD — App password or login password
metadata:
  hermes:
    homepage: https://github.com/aziendaos/ncx-workspace
    platform: [cli, telegram, slack, discord, whatsapp]
    related_skills: [google-workspace]
---

# ncx-workspace

**Regola d'oro:** Per OGNI operazione su Nextcloud, usa il comando `ncx`. Non usare `curl`, `requests`, `ls`, `find`, `cat` su percorsi locali, o strumenti generici. `ncx` e' il CLI ufficiale e gestisce autenticazione, errori e formati correttamente.

Nextcloud CLI per AI Agents — **Files**, **Calendar**, **Contacts**, **Talk**, **Activity**.