<div align="center">

# 🎬 AI Video Smart Editor

**Automated video editor powered by Whisper + Gemini · GPU-accelerated · Thai/English bilingual**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![CUDA](https://img.shields.io/badge/CUDA-12.4-76B900.svg)](https://developer.nvidia.com/cuda-zone)

อัปโหลดวิดีโอ → AI ฟัง+เข้าใจเนื้อหา → ตัดเฉพาะส่วนสำคัญ → ได้วิดีโอที่กระชับขึ้น พร้อม subtitle อัตโนมัติ

</div>

---

## ✨ Features

### Core
- 🎯 **9 Preset modes** — ตัดความเงียบ / สาระสำคัญ / Podcast / Tutorial / Meeting / Gaming / TikTok / Custom
- 🎬 **2 Output formats** — Standard 16:9 และ TikTok/Reels 9:16 vertical
- 📝 **Subtitle อัตโนมัติ** — PyThaiNLP word tokenizer + smart sentence boundary
- 🇹🇭 **Bilingual support** — Thai-native + English embedded → auto-translate
- ⚡ **GPU Acceleration** — CUDA + Whisper float16 + BatchedInferencePipeline
- 🛡️ **Multi API Key fallback** — auto-switch เมื่อ quota หมด

### Advanced
- 👁️ **Preview Mode** — ดู AI วิเคราะห์ + เลือก/ยกเลิก segments ก่อน render
- ✏️ **Subtitle Editor** — แก้ subtitle ทีละบรรทัดในเบราว์เซอร์ก่อน burn-in
- 🤖 **AI Post-correction** — Gemini แก้ชื่อเฉพาะ + แปลประโยค English ↔ Thai
- 🎙️ **Topic-aware Whisper** — initial_prompt ตาม preset ช่วยให้ accuracy +10-15%
- 🔧 **Whisper Gap-fill** — Pass 2 รับฟัง English embedded ที่ Pass 1 ข้าม
- 💾 **Audio hash cache** — re-upload วิดีโอเดิม = instant result (<10s)

---

## ⚡ Performance

ทดสอบกับวิดีโอ podcast 19 นาที (Warren Buffett Investment Talk):

| Stage | Original | Optimized (Pack X+Y) |
|---|---|---|
| Audio extract + VAD | 1 min | 1 min |
| 🎙️ Whisper transcribe | 13 min | **2 min** (BatchedInference x4) |
| 🤖 AI Correction | 3 min | **1.5 min** (parallel 2 keys) |
| 🎯 AI Deletion | 1.5 min | parallel with correction |
| 🎞️ FFmpeg render | 2 min | 2 min |
| **Total (first run)** | **22 min** | **~7 min** ⚡ |
| **Repeat upload** | 22 min | **<10s** 🚀 (cache hit) |

---

## 🏗️ Architecture

```
┌─────────────────┐
│   Frontend      │   React 19 + Vite + Tailwind
│   (port 80)     │   Browser → Preview / Edit subtitle
└────────┬────────┘
         │ HTTP/JSON
┌────────▼────────┐
│   Backend API   │   FastAPI + Pydantic validation
│   (port 8000)   │   Upload / Status / Render / Subtitle CRUD
└────────┬────────┘
         │
┌────────▼────────────────────────────────────────────┐
│              Celery Worker                          │
│  ┌───────────────────────────────────────────────┐  │
│  │ 1. extract_clean_audio (FFmpeg + loudnorm)    │  │
│  │ 2. VAD (Silero) — detect voice activity       │  │
│  │ 3. Whisper Pass 1 (BatchedInferencePipeline)  │  │
│  │ 4. Whisper Pass 2 (gap-fill English embed)    │  │
│  │ 5. AI Correction + Deletion (parallel)        │  │
│  │ 6. Phrase generation (sentence-aware)         │  │
│  │ 7. FFmpeg render (cut + concat + burn-in SRT) │  │
│  └───────────────────────────────────────────────┘  │
└─────┬───────────────────────────────┬───────────────┘
      │                               │
┌─────▼─────┐                  ┌──────▼──────┐
│   Redis   │                  │   GPU       │
│ (queue)   │                  │  (NVIDIA)   │
└───────────┘                  └─────────────┘
```

### Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 19 · Vite · Tailwind CSS · Axios |
| **Backend** | FastAPI · Pydantic · Python 3.11 |
| **Queue** | Celery + Redis |
| **Transcription** | faster-whisper 1.2 (BatchedInference) |
| **AI Analysis** | Google Gemini 2.5/2.0/3-flash |
| **Audio** | Silero VAD + FFmpeg afftdn + loudnorm |
| **Video** | FFmpeg libx264 + ASS subtitle burn-in |
| **Thai NLP** | PyThaiNLP newmm tokenizer |
| **GPU** | NVIDIA CUDA 12.4 + PyTorch + float16 |
| **Container** | Docker Compose (4 services) |

---

## 📋 Requirements

| Component | Min | Recommended |
|---|---|---|
| **OS** | Linux / Windows 11 + WSL2 | Linux |
| **GPU** | NVIDIA RTX 30-series 6GB | RTX 40-series 12GB+ |
| **VRAM** | 6 GB | 12+ GB (batch_size 8+) |
| **RAM** | 8 GB | 16 GB |
| **Disk** | 30 GB | 100 GB SSD |
| **Docker** | Docker Compose v2 + NVIDIA Container Toolkit | |
| **Gemini API Key** | 1 key (free tier) | 2+ keys (parallel calls) |

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/Chirayut001/ai-video-editor.git
cd ai-video-editor
```

### 2. Configure API keys

```bash
cp backend/.env.example backend/.env
```

แก้ `backend/.env`:

```env
# 1 key (basic)
GEMINI_API_KEYS=AIzaSy...your_key...

# 2+ keys (parallel calls — 2x faster on AI Correction)
GEMINI_API_KEYS=AIzaSy...key1...,AIzaSy...key2...

REDIS_URL=redis://redis:6379/0
```

> 💡 ขอ Gemini API key ฟรีจาก [Google AI Studio](https://aistudio.google.com/app/apikey)

### 3. Build + Start

```bash
docker compose up -d --build
```

> ⏱️ ครั้งแรกใช้เวลา 5-15 นาที (download CUDA image + PyTorch + Whisper model)

### 4. Open

| Service | URL |
|---|---|
| 🌐 **Web UI** | http://127.0.0.1 |
| 🔌 API docs | http://127.0.0.1:8000/docs |
| ❤️ Health | http://127.0.0.1:8000/health |

---

## 📖 Usage

### Standard Cut Mode

1. Upload วิดีโอ (.mp4, .mov, .mkv) ขนาด ≤ 2GB
2. กรอก prompt เช่น `"เก็บประเด็นสำคัญ ตัด filler"`
3. เลือก preset (Podcast / Tutorial / Meeting / etc.)
4. ☑ **Preview Mode** (แนะนำ — ตรวจก่อน render)
5. ☑ **ใส่ subtitle อัตโนมัติ**
6. กด **Submit**
7. รอ ~5-7 นาที (วิดีโอ 10-20 นาที)
8. **Preview** → เลือก segments
9. **Edit Subtitle** → แก้คำที่ผิด (optional)
10. **Render** → ดาวน์โหลด

### TikTok / Reels Mode

1. เลือก preset **🔥 TikTok/Reels**
2. กำหนด `target_length` (30s / 60s / 90s)
3. AI จะเลือก best moments + crop 9:16
4. Subtitle ขนาดใหญ่ TikTok-style

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEYS` | required | Gemini key(s), comma-separated |
| `REDIS_URL` | `redis://redis:6379/0` | Redis broker URL |
| `WHISPER_BATCH_SIZE` | `4` | Batched inference batch size (↑ = faster, ↑ VRAM) |
| `TRANSCRIPT_CACHE_DIR` | `/app/transcript_cache` | Audio hash → transcript cache |

### Tuning Performance

ใน `docker-compose.yml` ปรับ `WHISPER_BATCH_SIZE` ตาม VRAM:

| GPU | Recommended |
|---|---|
| RTX 3050 6GB | `4` (default) |
| RTX 3060 12GB | `8` |
| RTX 4070+ | `16` |
| A100/H100 | `32+` |

### Subtitle Lead Time

ใน `backend/core/srt_utils.py` (line ~31):

```python
SUBTITLE_LEAD_TIME = 0.18   # 180ms — subtitle เริ่มก่อนเสียง
# 0.10 = ตามเสียงเป๊ะ ๆ
# 0.20 = subtitle ก่อนเสียงเยอะ (ดูสบายตา)
```

---

## 🗂️ Project Structure

```
ai-video-editor/
├── backend/
│   ├── core/
│   │   ├── ai_logic.py        # Whisper + Gemini + cache + parallel calls
│   │   ├── ffmpeg_utils.py    # Audio extract + cut + concat + burn subtitle
│   │   ├── srt_utils.py       # SRT generation + phrase splitting + lead time
│   │   └── vad_logic.py       # Silero VAD wrapper
│   ├── main.py                # FastAPI app (endpoints)
│   ├── tasks.py               # Celery tasks (process_video + render_only)
│   ├── requirements.txt
│   ├── Dockerfile             # CUDA base + Python deps
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx            # State machine: processing/preview/editing/done
│   │   ├── components/
│   │   │   ├── UploadScreen.jsx
│   │   │   ├── Processing.jsx
│   │   │   ├── PreviewScreen.jsx
│   │   │   └── SubtitleEditScreen.jsx
│   │   └── config.js
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml          # 4 services (frontend / backend / worker / redis)
```

---

## 🔬 Pipeline Details

### Whisper Optimization
- **medium model** บน CUDA float16 → ~3.5GB VRAM
- **BatchedInferencePipeline** → 3-4x faster (parallel chunks)
- **Topic-aware initial_prompt** → +10-15% accuracy
- **Pass 2 gap-fill** → catch English embedded ที่ Pass 1 ข้าม
- **Audio hash cache** → instant repeat for same audio

### AI Correction (Gemini)
- แก้ชื่อเฉพาะ (Andrew Huberman, Python, React)
- แปลประโยค English embedded → Thai
- คงชื่อเฉพาะเป็นอังกฤษ
- Parallel chunks ระหว่าง API keys (2x speedup)
- Auto-skip ถ้า transcript เป็นไทยล้วน

### Sentence Boundary
- PyThaiNLP word tokenization (Thai)
- 4-level break priority: punctuation > end particle > conjunction > script transition
- Merge phrases <9 chars เข้ากับ neighbor
- Soft/hard char limits: 14/28

### Subtitle Burn-in
- Garuda font (Thai + Latin)
- Standard 16:9: FontSize 20, Outline 1, white text
- TikTok 9:16: FontSize 18, MarginV 30 (TikTok-style)
- 180ms lead time (subtitle ปรากฏก่อนเสียง)

---

## 🐳 Docker Commands

```bash
# Start
docker compose up -d

# Build + start (after code change)
docker compose up -d --build

# Watch logs
docker compose logs -f worker

# Rebuild single service
docker compose build worker
docker compose up -d --force-recreate worker

# Stop
docker compose down

# Stop + remove volumes (delete cache)
docker compose down -v
```

---

## 🧪 Testing

```bash
# Inside worker container — run core integration test
docker compose exec worker python test_core.py
```

---

## 🛣️ Roadmap

### Planned
- [ ] Multi-language subtitle (Thai-only / English-only / Bilingual)
- [ ] Subtitle-only mode (ไม่ตัด — แค่ใส่ subtitle)
- [ ] Custom dictionary (user-provided proper nouns)
- [ ] NVENC hardware encoder (5x faster render)
- [ ] WebRTC live stream input

### Done ✅
- [x] BatchedInferencePipeline (Whisper 3-4x)
- [x] Audio hash cache
- [x] Parallel Gemini calls
- [x] Pipeline parallelism
- [x] Whisper gap-fill Pass 2
- [x] AI auto-translate English embedded
- [x] Subtitle editor UI
- [x] Sentence boundary improvements
- [x] Subtitle lead time
- [x] Preview mode

---

## 🔐 Security

- ✅ Path traversal protection (filename sanitization)
- ✅ File size limit (2GB upload max)
- ✅ Extension whitelist (only video formats)
- ✅ CORS lockdown (localhost only by default)
- ✅ API keys via env (never committed)
- ✅ Storage cleanup (auto-delete jobs > 7 days)

---

## 🤝 Contributing

PRs welcome! สำหรับ feature ใหญ่ ๆ เปิด issue ก่อนเพื่อ discuss

```bash
git checkout -b feature/your-feature
# ทำงาน...
git commit -m "feat: your feature description"
git push origin feature/your-feature
# → open PR
```

---

## 📜 License

[MIT](LICENSE) © 2026

---

## 🙏 Acknowledgements

- [OpenAI Whisper](https://github.com/openai/whisper) — speech recognition
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — optimized inference
- [Google Gemini](https://ai.google.dev/) — content analysis & translation
- [Silero VAD](https://github.com/snakers4/silero-vad) — voice activity detection
- [PyThaiNLP](https://github.com/PyThaiNLP/pythainlp) — Thai NLP toolkit
- [FFmpeg](https://ffmpeg.org/) — video/audio processing

---

<div align="center">

**Made with ❤️ for content creators**

[Report Bug](https://github.com/Chirayut001/ai-video-editor/issues) · [Request Feature](https://github.com/Chirayut001/ai-video-editor/issues)

</div>
