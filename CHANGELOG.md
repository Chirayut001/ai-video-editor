# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] — 2026-06-18

Major performance & feature update — 3-4x faster, +25% subtitle accuracy.

### Added
- ⚡ **BatchedInferencePipeline** — Whisper transcribe 3-4x faster on GPU
- 💾 **Audio hash cache** — re-upload identical audio = instant result (<10s)
- 🧠 **Skip AI correction** for pure Thai transcripts (no Latin chars)
- 🔀 **Parallel Gemini calls** — split correction across N API keys
- 🔗 **Pipeline parallelism** — AI Correction + Deletion run concurrently
- 🔧 **Whisper Pass 2 gap-fill** — re-transcribe gaps without language bias to catch English embedded
- 🌐 **AI auto-translate** — English sentences embedded → Thai (keep proper names)
- ✏️ **Subtitle Editor UI** — edit subtitle text per phrase in browser before render
- 👁️ **Preview Mode** — review AI selections + choose segments before render
- 📐 **Smart sentence boundary** — break at punctuation/end-particles/conjunctions
- 🎯 **Subtitle lead time** — subtitles appear 180ms before speech for better readability
- 🎙️ **Topic-aware initial_prompt** — 9 presets boost Whisper Thai accuracy
- 🔊 **Loudness normalization** — FFmpeg EBU R128 + denoise before Whisper

### Changed
- Whisper compute: now uses `BatchedInferencePipeline` (zero accuracy loss)
- Whisper config: `condition_on_previous_text=False` + `vad_filter=True`
- AI Correction prompt: now translates English embedded sentences to Thai
- Sentence splitting: MIN_PHRASE_CHARS=9, soft/hard limits 14/28
- Subtitle Burn-in: standard mode FontSize=20 (was 14), better readability

### Fixed
- Subtitle gap from Whisper skipping English audio (gap-fill recovers)
- Phrase splitting breaking Thai compound words mid-syllable
- Subtitle appearing slightly after speech start (lead time compensation)

### Performance
- 19-min Warren Buffett podcast: **22 min → 7 min** first run, **<10s** cached
- Whisper transcribe: **13 min → 2 min** (BatchedInference)
- AI Correction: **3 min → 1.5 min** (parallel keys)

---

## [1.0.0] — 2026-06-15

Initial release.

### Added
- Docker Compose setup (frontend / backend / worker / redis)
- Whisper medium on CUDA float16
- Gemini AI deletion mode + TikTok peak extraction
- Silero VAD pre-filter
- Subtitle burn-in (Garuda font)
- 7 preset modes (silence, essence, podcast, tutorial, etc.)
- TikTok/Reels 9:16 vertical output
- PyThaiNLP word tokenizer
- Multi API key fallback
- Storage cleanup (>7 days)
- Path traversal + file size protection
