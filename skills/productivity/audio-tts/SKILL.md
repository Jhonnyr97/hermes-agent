---
name: audio-tts
description: "Use this skill whenever the user asks to create, generate, or produce audio content — spoken narration, voiceovers, audio summaries, podcast clips, or any text-to-speech output. This skill handles script preparation, TTS generation via the built-in Hermes text_to_speech tool (Edge TTS by default), and transcript delivery for human pronunciation review. The skill produces LOCAL files; uploading to storage (Nextcloud, Google Drive, etc.) is handled by the appropriate connector skill."
version: 1.0.0
author: Hermes Community
license: MIT
tags: [audio, tts, text-to-speech, edge-tts, voice, narration, transcript]
metadata:
  hermes:
    platform: [cli]
    related_skills: [ncx-workspace, google-workspace]
---

# Audio TTS Skill

## Flow

```
1. Write narrative script  →  /tmp/audio-tts/script_raw.txt
2. text_to_speech(script)   →  /tmp/audio-tts/output.mp3
3. Copy script as transcript → /tmp/audio-tts/transcript.txt
4. Show human:               output.mp3 + transcript.txt
5. Human corrects pronunciation → regenerate with fixes
6. Deliver final:            output_final.mp3 + transcript_final.txt
                             (agent uploads to storage afterwards)
```

## Golden Rule

**Audio is NEVER delivered without its transcript.** The transcript is the human's debugging tool — without it, they cannot fix pronunciation. The deliverable is always a pair (audio + transcript).

## Procedure

**IMPORTANT**: TTS is a built-in Hermes tool (`text_to_speech`), NOT a shell command. Do not look for `edge-tts`, `gtts`, `python` or other executables — use the Hermes tool directly.

### Step 1 — Prepare script

Write the narrative text to a file:

```
Save as /tmp/audio-tts/script_raw.txt
```

### Step 2 — Generate audio

Call the Hermes `text_to_speech` tool:

```
text_to_speech(text="script text")
```

The tool handles everything: picks the provider (Edge TTS by default), produces the MP3 file, saves it to cache.

**Do NOT** run `python3`, `gtts-cli` or other shell commands for TTS. Use the `text_to_speech` Hermes tool first.

**⚠️ Limit**: The tool does not expose parameters for voice/language selection. For non-English text, Edge TTS may auto-detect the wrong voice and pronounce everything with an English accent. If the output has foreign pronunciation:

1. **Check** that `edge-tts` is available and has voices for your language:
   ```bash
   edge-tts --list-voices | grep -i it-  # for Italian
   ```

2. **Regenerate** with `edge-tts` CLI directly, specifying the voice:
   ```bash
   edge-tts --voice it-IT-IsabellaNeural --file /tmp/audio-tts/script_raw.txt --write-media /tmp/audio-tts/output.mp3
   ```

3. **Other languages**: search with `edge-tts --list-voices | grep -i "LANG-"` (e.g. `fr-FR-`, `de-DE-`, `es-ES-`, `pt-BR-`, `zh-CN-`, `ja-JP-`).

### Step 3 — Create transcript

Copy the text used as transcript:

```
cp /tmp/audio-tts/script_raw.txt /tmp/audio-tts/transcript.txt
```

### Step 4 — Show the human

Present to the human:
- Audio file: `/tmp/audio-tts/output.mp3`
- Transcript: contents of `/tmp/audio-tts/transcript.txt`

Ask: "Is the pronunciation correct? Point out any words to fix."

### Step 5 — Correct and regenerate

If the human reports pronunciation errors (e.g. "MBI should be M.B.I.", "API should be A-P-I"):
1. Edit `script_raw.txt` with corrections
2. Regenerate with `text_to_speech` → `output_final.mp3`
3. Copy new transcript → `transcript_final.txt`

### Step 6 — Deliver

Final files are in `/tmp/audio-tts/`. The agent uploads them to the configured storage (Nextcloud, Google Drive, etc.).

## Pronunciation notes

| Situation | Write | Why |
|-----------|-------|-----|
| Initialisms | "M.B.I.", "A.P.I." | TTS reads letter by letter |
| Common acronyms | "NASA", "FBI" | TTS recognizes them |
| Proper names | they read fine in the right voice language | TTS pronounces correctly |
| Numbers | "15 percent" better than "15%" | TTS reads symbols poorly |
| URLs | "example dot com" instead of "example.com" | TTS reads "dot" in English |
| Foreign words in native language | adapt spelling: "server", "cloud" | TTS pronounces them natively |

## TTS Providers (configurable)

The `text_to_speech` tool supports 7 providers. Default: **Edge TTS** (free, no API key).

| Provider | Cost | Quality | API Key |
|----------|------|---------|---------|
| Edge TTS (default) | Free | High | No |
| OpenAI TTS | Paid | High | OPENAI_API_KEY |
| ElevenLabs | Paid | Very high | ELEVENLABS_API_KEY |
| Google Gemini TTS | Paid | High | GEMINI_API_KEY |
| MiniMax | Paid | High | MINIMAX_API_KEY |
| Mistral (Voxtral) | Paid | High | MISTRAL_API_KEY |
| NeuTTS (local) | Free | Medium | No (neutts installed) |

