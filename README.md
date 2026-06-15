# 🎬 AI Video Smart Editor

ระบบตัดต่อวิดีโออัตโนมัติด้วย AI — อัปโหลดวิดีโอ → AI ฟัง+เข้าใจเนื้อหา → ตัดเฉพาะส่วนสำคัญ → ได้วิดีโอที่สั้นลง พร้อม subtitle อัตโนมัติ

> Powered by Whisper + Gemini · GPU CUDA · Real-time processing

---

## 🚀 Features

- 🎯 **9 Preset** สำหรับการตัดต่อหลายรูปแบบ (ตัดความเงียบ, สาระสำคัญ, TikTok, Podcast, ฯลฯ)
- 🎬 **2 Output Modes**: Standard 16:9 และ TikTok/Reels 9:16 vertical
- 📝 **Subtitle อัตโนมัติ** ใช้ PyThaiNLP ตัดคำไทยถูกต้อง
- 🇹🇭 **รองรับภาษาไทย** เต็มรูปแบบ
- ⚡ **GPU Acceleration** เร็วกว่า CPU 15-25 เท่า
- 🔑 **Multi API Key** fallback อัตโนมัติเมื่อ quota หมด
- 🛡️ **Production-ready** security (path traversal, file validation, CORS lockdown)

---

## 🏗️ Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + Vite + Tailwind CSS |
| Backend | FastAPI (Python 3.11) |
| Queue | Celery + Redis |
| Transcription | OpenAI Whisper (faster-whisper) |
| AI Analysis | Google Gemini API |
| Video Processing | FFmpeg (libx264) |
| GPU | NVIDIA CUDA 12.4 + PyTorch + Float16 |
| Container | Docker Compose (4 services) |

---

## 📋 Requirements

- **OS**: Windows 10/11 (พร้อม WSL2) หรือ Linux
- **Docker Desktop** + Docker Compose v2
- **NVIDIA GPU** (RTX 20-series ขึ้นไป) + NVIDIA Container Toolkit
- **Disk**: ≥ 30 GB ว่าง
- **Gemini API Key** — ขอฟรีจาก [Google AI Studio](https://aistudio.google.com/app/apikey)

---

## 🛠️ Installation

### 1. Clone repo
```bash
git clone https://github.com/<your-username>/ai-video-editor.git
cd ai-video-editor
```

### 2. ตั้งค่า API Key
```bash
cp backend/.env.example backend/.env
# เปิดไฟล์ backend/.env แล้วใส่ Gemini API key
```

ใน `backend/.env`:
```env
GEMINI_API_KEYS=AIzaSy...your_key_here...
REDIS_URL=redis://redis:6379/0
```

> 💡 สามารถใส่หลาย key ได้ (คั่นด้วย `,`) ระบบจะ fallback อัตโนมัติเมื่อ key หนึ่ง quota หมด

### 3. Build + Start
```bash
docker compose up -d --build
```

รอ ~5-15 นาที (ครั้งแรก) สำหรับ download CUDA image + torch

### 4. เปิดใช้งาน

| Service | URL |
|---|---|
| Frontend | http://127.0.0.1 |
| Backend API | http://127.0.0.1:8000 |
| API Docs (Swagger) | http://127.0.0.1:8000/docs |

---

## 🎬 Pipeline

```
Upload Video
    ↓
[1] FFmpeg extract audio (~15s)
    ↓
[2] Silero VAD detect speech (~30s)
    ↓
[3] Whisper transcribe on GPU (~1-3 min)
    ↓
[4] Gemini AI analyze + select segments (~30s)
    ↓
[5] FFmpeg cut + concat + burn subtitle (~2-3 min)
    ↓
Output: Edited Video
```

**ประมาณการเวลา:** วิดีโอ 20 นาที → ตัดเสร็จใน ~5-10 นาที

---

## 📂 Project Structure

```
ai-video-editor/
├── docker-compose.yml         # 4 services config
├── backend/
│   ├── Dockerfile             # CUDA 12.4 + torch+cu124
│   ├── requirements.txt       # FastAPI, Celery, faster-whisper, pythainlp
│   ├── .env.example           # Template (copy → .env)
│   ├── main.py                # FastAPI endpoints + validation
│   ├── tasks.py               # Celery task pipeline
│   └── core/
│       ├── ai_logic.py        # Whisper + Gemini (multi-key)
│       ├── ffmpeg_utils.py    # Cut/concat/render
│       ├── vad_logic.py       # Silero VAD
│       └── srt_utils.py       # SRT generation + Thai tokenizer
└── frontend/
    ├── Dockerfile             # Multi-stage (Node + Nginx)
    ├── nginx.conf
    └── src/
        ├── App.jsx            # State + result page
        ├── config.js          # API URL (env-based)
        └── components/
            ├── UploadScreen.jsx
            └── Processing.jsx
```

---

## 🎯 Usage

### หน้า Upload
1. Drag & drop หรือ คลิกเลือกไฟล์วิดีโอ
2. เลือก **Preset** (ตัดความเงียบ / TikTok / ฯลฯ) หรือพิมพ์คำสั่งเอง
3. (Optional) เปิด **Subtitle** ฝังในวิดีโอ
4. (TikTok mode) เลือกความยาว 30/60/90 วินาที
5. กด **เริ่มประมวลผลด้วย AI**

### หน้า Processing
- เห็น progress + step indicator (4 ขั้นตอน)
- ระบบจะแสดงผลให้อัตโนมัติเมื่อเสร็จ

### หน้า Result
- ดูวิดีโอผลลัพธ์
- ดาวน์โหลด
- ตัดต่อใหม่

---

## 🔧 Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Logs
docker compose logs -f worker

# Restart only worker (e.g., หลังเปลี่ยน API key)
docker compose up -d --force-recreate worker backend

# Cleanup
docker compose down -v
```

---

## 🛡️ Security

- ✅ Path traversal protection (`safe_filename`)
- ✅ File size limit (2GB)
- ✅ Extension whitelist
- ✅ Input validation (prompt length, target_length range)
- ✅ CORS lockdown (localhost only)
- ✅ UUID format validation
- ✅ Auto storage cleanup (>7 days)
- ✅ Error message sanitization

---

## 📊 Performance

| วิดีโอ | CPU only | GPU (RTX 3050) |
|---|---|---|
| 5 นาที | ~10 นาที | ~3 นาที |
| 20 นาที | ~40 นาที | ~10 นาที |
| 1 ชม. | ~2 ชม. | ~25 นาที |

---

## 🤝 Contributing

โปรเจคนี้พัฒนาสำหรับโปรเจคจบมหาวิทยาลัย — feature ใหม่และ bug fix ยินดีรับ

---

## 📜 License

MIT License

---

## 🙏 Credits

- [OpenAI Whisper](https://github.com/openai/whisper) — speech-to-text
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — optimized inference
- [Silero VAD](https://github.com/snakers4/silero-vad) — voice activity detection
- [Google Gemini](https://ai.google.dev/) — AI content analysis
- [PyThaiNLP](https://pythainlp.org/) — Thai language processing
- [FFmpeg](https://ffmpeg.org/) — video processing
