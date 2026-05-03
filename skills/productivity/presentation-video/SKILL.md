---
name: presentation-video
description: Create professional narrated video presentations from source documents using HyperFrames — HTML/GSAP animated slides with synchronized TTS audio narration.
version: 1.0.0
tags: [hyperframes, video, presentation, tts, animation, gsap, slides]
---

# Presentation Video with HyperFrames

Create a professional, boardroom-quality animated video from source content (document, summary, slides, or notes). Uses HyperFrames (HTML/GSAP video framework) for animated slides with synchronized TTS audio narration.

## Prerequisites

Load these skills before starting:
- `hyperframes` — core composition authoring
- `hyperframes-cli` — CLI commands (init, lint, inspect, preview, render)
- `gsap` — animation engine reference
- `audio-tts` — text-to-speech generation (Hermes native Edge TTS)

## Trigger

Use this skill when the user asks to:
- "Make a video presentation from this document/paper/report"
- "Create a professional video for the board/executives/CDA"
- "Make an animated video with narration from these slides"
- "Turn the summary into a narrated video"
- Any request combining source content + animated video + narrated audio

## Audio Sync: Per-Slide Strategy (Mandatory)

**Always use per-slide audio clips** — single-track audio with estimated durations WILL go out of sync. Strategy:

| Strategy | How it works | Sync quality |
|----------|-------------|-------------|
| A: Single audio + estimated timing | One long audio file, slides timed by estimating duration per topic | Drifts out of sync |
| B: Per-slide audio clips | Separate audio clip per slide, exact durations calculated from actual clip length | Frame-perfect sync (USE THIS) |

## Pipeline

```
Source Content (document/summary/slides)
  → Step 1: Write per-slide narration scripts
  → Step 2: Generate per-slide TTS audio with Hermes TTS
  → Step 3: Calculate exact durations from actual audio clips
  → Step 4: Create HyperFrames project with per-slide HTML compositions
  → Step 5: Wire GSAP timelines to audio durations
  → Step 6: Render MP4 with frame-perfect sync
  → Step 7: Upload to storage (Nextcloud, Drive, etc.)
```

## Step 1: Per-Slide Audio Narration

### 1a. Write per-slide scripts

Chunk the content into slide-length narration segments. Each script should be:
- 15-25 seconds of spoken content (40-60 words)
- Self-contained (works without the other slides)
- Written for TTS: clear enunciation, no punctuation tricks

### 1b. Generate per-slide TTS

Use Hermes native TTS (Edge TTS). Example for each slide:

```
text-to-speech "script_slide_1.txt" --voice "en-US-JennyNeural" --output "assets/audio/slide1.mp3"
```

Always use same voice across all slides.

## Step 2: Calculate Exact Frame Durations

After generating all audio clips, measure their true durations:

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 assets/audio/slide1.mp3
```

This gives you the exact duration in seconds. Calculate frames at 30fps:
`frames = round(duration_seconds * 30)`

## Step 3: Create HyperFrames Project

```bash
npx hyperframes init presentation --example blank --non-interactive
cd presentation
```

## Step 4: Build Slide Compositions

Each slide is a HyperFrames composition using HTML + GSAP. Create one composition per slide.

### Basic slide template

```html
<template id="slide1-template">
  <div data-composition-id="slide1" data-width="1920" data-height="1080">
    <div class="slide-bg"></div>
    <h1 class="title">Slide Title Here</h1>
    <p class="body">Supporting content for this slide.</p>

    <audio src="assets/audio/slide1.mp3" preload="auto"></audio>

    <style>
      .slide-bg {
        position: absolute; inset: 0;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      }
      .title {
        position: absolute; top: 240px; left: 120px;
        font-family: 'Inter', sans-serif; font-size: 64px;
        color: #ffffff; opacity: 0;
      }
      .body {
        position: absolute; top: 380px; left: 120px; width: 1200px;
        font-family: 'Inter', sans-serif; font-size: 32px;
        color: rgba(255,255,255,0.85); opacity: 0; line-height: 1.5;
      }
    </style>

    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      const DURATION = X.XX; // Replace with ffprobe duration

      tl.from(".title", { y: 48, opacity: 0, duration: 0.5, ease: "power2.out" }, 0);
      tl.from(".body", { y: 24, opacity: 0, duration: 0.5, ease: "power2.out" }, 0.25);
      // Hold until audio finishes
      tl.to({}, { duration: DURATION - 0.75 });

      window.__timelines["slide1"] = tl;
    </script>
  </div>
</template>
```

## Step 5: Wire Compositions in index.html

Edit `index.html` to wire all slides in sequence:

```html
<!-- Slide 1 -->
<div
  data-composition-id="slide1"
  data-composition-src="compositions/slide1.html"
  data-start="0"
  data-duration="5.5"
  data-track-index="1"
  data-width="1920"
  data-height="1080"
></div>

<!-- Slide 2 -->
<div
  data-composition-id="slide2"
  data-composition-src="compositions/slide2.html"
  data-start="5.5"
  data-duration="4.2"
  data-track-index="1"
  data-width="1920"
  data-height="1080"
></div>
```

Each `data-start` = cumulative duration of all previous slides.

## Step 6: Render

```bash
npx hyperframes lint
npx hyperframes inspect
npx hyperframes render --output presentation_YYYYMMDD_HHMMSS.mp4
```

## Step 7: Transitions Between Slides

For professional transitions between slides, use GSAP crossfades. Add a transition composition:

```html
<template id="transition-template">
  <div data-composition-id="crossfade" data-width="1920" data-height="1080">
    <style>
      .xfade-bg { position: absolute; inset: 0; background: #000; opacity: 0; }
    </style>
    <div class="xfade-bg"></div>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      tl.to(".xfade-bg", { opacity: 1, duration: 0.3, ease: "power2.inOut" });
      tl.to(".xfade-bg", { opacity: 0, duration: 0.3, ease: "power2.inOut" });
      window.__timelines["crossfade"] = tl;
    </script>
  </div>
</template>
```

Wire between slides with `data-duration="0.6"` and track them at higher `data-track-index`.

## Quality Checklist

- [ ] Each audio clip measured via ffprobe, durations written into timelines
- [ ] `hyperframes lint` passes with zero errors
- [ ] `hyperframes inspect` shows no text overflow issues
- [ ] GSAP timelines created `{ paused: true }`
- [ ] No `Math.random()`, `Date.now()`, or `repeat: -1` anywhere
- [ ] Each slide has unique `data-composition-id`
- [ ] Cumulative `data-start` values are correct
- [ ] Render completes successfully
