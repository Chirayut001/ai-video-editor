import os
import re
import json
import uuid
import shutil
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from celery.result import AsyncResult

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from pydantic import BaseModel
from tasks import process_video_task, render_only_task, cleanup_old_jobs

# ── Constants / Limits ───────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 2048               # 2GB upload limit
MAX_PROMPT_LENGTH = 2000               # 2000 chars prompt
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ALLOWED_OUTPUT_MODES = {"standard", "tiktok"}
MIN_TARGET_LENGTH = 10
MAX_TARGET_LENGTH = 600
# NOTE: cleanup logic (JOB_RETENTION_DAYS, marker guard) ย้ายไป tasks.py แล้ว
#       เพื่อให้ทั้ง API startup และ celery beat ใช้ตัวเดียวกัน (DRY)
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# Render task_id format = "{uuid}-render" — รับด้วยใน /status (ของ /preview /render /download ใช้ UUID_PATTERN)
UUID_OR_TASK_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(-render)?$", re.I)
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]")

STORAGE_DIR = "storage"


def safe_filename(filename: str) -> str:
    """กรอง filename ให้ปลอดภัย — กัน path traversal และตัวอักษรพิเศษ"""
    name = os.path.basename(filename or "")    # strip directory
    name = name.lstrip(".")                     # strip leading dots
    name = SAFE_FILENAME_RE.sub("_", name)
    name = name[:200]                           # limit length
    return name or "upload.mp4"


# ── Security config (auth / rate limit / CORS) — เปิด/ปิดผ่าน env ─────────────
# API key: API_KEYS ว่าง = ปิด auth (dev). ตั้งค่า = /upload,/render ต้องมี X-API-Key ที่ถูกต้อง
API_KEYS = {k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()}
# CORS: ตั้ง ALLOWED_ORIGINS (คั่นด้วย ,) สำหรับ deploy โดเมนจริง — ไม่ตั้ง = localhost
_DEFAULT_ORIGINS = ("http://localhost,http://127.0.0.1,http://localhost:80,"
                    "http://127.0.0.1:80,http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]
# Rate limit ต่อ IP (ปรับผ่าน env) — กันยิงถล่ม /upload,/render (ที่กิน GPU+โควต้า AI)
UPLOAD_RATE_LIMIT = os.getenv("UPLOAD_RATE_LIMIT", "20/hour")
RENDER_RATE_LIMIT = os.getenv("RENDER_RATE_LIMIT", "60/hour")


def _client_ip(request: Request) -> str:
    """ดึง IP จริง — รองรับหลัง reverse proxy (Caddy ใส่ X-Forwarded-For)"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=_client_ip,
    storage_uri=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)


async def require_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    """บังคับ API key เฉพาะเมื่อ API_KEYS ถูกตั้งค่า (ไม่งั้น no-op สำหรับ dev)"""
    if not API_KEYS:
        return
    if not x_api_key or x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="ต้องมี X-API-Key ที่ถูกต้อง")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    os.makedirs(STORAGE_DIR, exist_ok=True)
    cleanup_old_jobs()
    yield
    # shutdown — no-op


app = FastAPI(lifespan=lifespan)

# ── Rate limiter (slowapi + Redis) ───────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS: ตั้งจาก env (ALLOWED_ORIGINS) — deploy โดเมนจริงได้ ไม่ hardcode ──────
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")


@app.post("/upload", dependencies=[Depends(require_api_key)])
@limiter.limit(UPLOAD_RATE_LIMIT)
async def upload_video(
    request: Request,
    video: UploadFile = File(...),
    prompt: str = Form(...),
    output_mode: str = Form("standard"),
    target_length: int = Form(60),
    burn_subtitle: bool = Form(False),
    preview_mode: bool = Form(False),
    preset_id: str = Form(""),
):
    # ── Input validation ────────────────────────────────────────────────────
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt ต้องไม่ว่าง")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(status_code=400, detail=f"prompt ยาวเกิน {MAX_PROMPT_LENGTH} ตัวอักษร")

    if output_mode not in ALLOWED_OUTPUT_MODES:
        raise HTTPException(status_code=400, detail=f"output_mode ต้องเป็น {ALLOWED_OUTPUT_MODES}")
    if not (MIN_TARGET_LENGTH <= target_length <= MAX_TARGET_LENGTH):
        raise HTTPException(
            status_code=400,
            detail=f"target_length ต้องอยู่ระหว่าง {MIN_TARGET_LENGTH}-{MAX_TARGET_LENGTH} วินาที",
        )

    fname = safe_filename(video.filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"ไฟล์ {ext} ไม่รองรับ (ต้องเป็น {ALLOWED_VIDEO_EXTS})",
        )

    print(f"DEBUG: Upload received: file={fname}, mode={output_mode}, "
          f"target_length={target_length}s, burn_subtitle={burn_subtitle}")

    try:
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(STORAGE_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)

        video_path = os.path.join(job_dir, fname)

        # ── Streamed write + size check (กัน DoS upload ใหญ่เกิน) ───────────
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        written = 0
        with open(video_path, "wb") as buffer:
            while True:
                chunk = await video.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    buffer.close()
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"ไฟล์ใหญ่เกิน {MAX_FILE_SIZE_MB} MB",
                    )
                buffer.write(chunk)

        print(f"✅ File saved: {video_path} ({written / 1024 / 1024:.1f} MB)")

        process_video_task.apply_async(
            args=[job_id, video_path, prompt, output_mode, target_length, burn_subtitle, preview_mode, preset_id],
            task_id=job_id,
        )

        return {"job_id": job_id, "preview_mode": preview_mode}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Upload error: {e}")
        raise HTTPException(status_code=500, detail="อัปโหลดล้มเหลว กรุณาลองใหม่")


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    # ── Validate jobId format กัน frontend ติด PENDING บน id ไม่ถูกต้อง ─────
    # รองรับทั้ง UUID เดิม + "{uuid}-render" สำหรับ render task
    if not UUID_OR_TASK_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="job_id ผิดรูปแบบ")

    try:
        result = AsyncResult(job_id)
        try:
            status = result.status
        except Exception:
            status = "UNKNOWN"

        response = {"status": status, "progress": 0, "result": None}

        if status == "SUCCESS":
            response["progress"] = 100
            response["result"] = result.result

        elif status == "FAILURE":
            response["progress"] = 0
            try:
                error_info = result.info
                if isinstance(error_info, Exception):
                    msg = str(error_info)
                elif isinstance(error_info, dict):
                    msg = error_info.get("status", "เกิดข้อผิดพลาด")
                else:
                    msg = "เกิดข้อผิดพลาด กรุณาลองใหม่"
            except Exception:
                msg = "เกิดข้อผิดพลาด กรุณาลองใหม่"
            # ตัด stack/Gemini detail ที่อาจมี API key หลุดได้
            if len(msg) > 300:
                msg = msg[:300] + "..."
            response["result"] = msg

        else:
            try:
                if isinstance(result.info, dict):
                    response["progress"] = result.info.get("progress", 0)
                    response["status"] = result.info.get("status", status)
            except Exception:
                pass

        return response

    except HTTPException:
        raise
    except Exception as e:
        return {"status": "FAILURE", "progress": 0, "result": f"เกิดข้อผิดพลาด: {str(e)[:200]}"}


@app.get("/download/{job_id}")
async def download_output(job_id: str):
    if not UUID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="job_id ผิดรูปแบบ")
    target_file = "final_summary.mp4"
    file_path = os.path.join(STORAGE_DIR, job_id, target_file)
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename=target_file, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="ไม่พบไฟล์วิดีโอผลลัพธ์")


MAX_SUBTITLE_PHRASES = 2000
MAX_PHRASE_TEXT_LEN = 200


class RenderRequest(BaseModel):
    segments: list[dict]   # [{"start": float, "end": float, ...}]
    edited_phrases: list[dict] | None = None  # [{"start": float, "end": float, "text": str}] (optional)


def _validate_phrases(phrases: list[dict]) -> list[dict]:
    """Validate + sanitize phrases ที่ user ส่งมาจาก /subtitle หรือ /render"""
    if len(phrases) > MAX_SUBTITLE_PHRASES:
        raise HTTPException(status_code=400, detail=f"subtitle phrases เกิน {MAX_SUBTITLE_PHRASES}")
    out = []
    for p in phrases:
        try:
            start = float(p.get("start", 0))
            end = float(p.get("end", 0))
        except (TypeError, ValueError):
            continue
        text = str(p.get("text") or "").strip()
        if not text or end <= start:
            continue
        if len(text) > MAX_PHRASE_TEXT_LEN:
            text = text[:MAX_PHRASE_TEXT_LEN]
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        })
    return out


@app.post("/render/{job_id}", dependencies=[Depends(require_api_key)])
@limiter.limit(RENDER_RATE_LIMIT)
async def render_preview(request: Request, job_id: str, body: RenderRequest):
    """Render วิดีโอจาก preview ที่ user เลือก segments แล้ว"""
    if not UUID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="job_id ผิดรูปแบบ")

    preview_file = os.path.join(STORAGE_DIR, job_id, "preview.json")
    if not os.path.exists(preview_file):
        raise HTTPException(status_code=404, detail="ไม่พบ preview — ต้องเรียก /upload?preview_mode=true ก่อน")

    if not body.segments:
        raise HTTPException(status_code=400, detail="ต้องเลือก segments อย่างน้อย 1 ช่วง")

    # Validate segments — ครอบ float() กัน 500 ถ้า start/end ไม่ใช่ตัวเลข (คืน 400 แทน)
    cleaned = []
    for s in body.segments:
        try:
            start = float(s.get("start", 0))
            end = float(s.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        cleaned.append({"start": round(start, 2), "end": round(end, 2)})

    if not cleaned:
        raise HTTPException(status_code=400, detail="ไม่มี segment ที่ valid")

    # Validate edited_phrases (optional)
    edited_phrases = None
    if body.edited_phrases is not None:
        edited_phrases = _validate_phrases(body.edited_phrases)

    # ใช้ job_id เดิมเป็น task_id ใหม่ → frontend poll endpoint เดิมได้
    new_task_id = f"{job_id}-render"
    render_only_task.apply_async(
        args=[job_id, cleaned, edited_phrases],
        task_id=new_task_id,
    )
    return {"task_id": new_task_id, "job_id": job_id}


@app.get("/subtitle/{job_id}")
async def get_subtitle(job_id: str):
    """ดึง subtitle phrases ที่ pre-generate ไว้ (frontend โหลดไปให้ user แก้ก่อน render)"""
    if not UUID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="job_id ผิดรูปแบบ")
    preview_file = os.path.join(STORAGE_DIR, job_id, "preview.json")
    if not os.path.exists(preview_file):
        raise HTTPException(status_code=404, detail="ไม่พบ preview")
    with open(preview_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"phrases": data.get("subtitle_phrases", [])}


# NOTE: subtitle ที่ user แก้ถูกส่งตรงเข้า POST /render (edited_phrases) ไม่ต้อง persist
# ก่อน render — จึงไม่มี POST /subtitle (เคยเป็น dead code ที่ frontend ไม่เรียก)


@app.get("/preview/{job_id}")
async def get_preview(job_id: str):
    """ดึงข้อมูล preview ที่ AI วิเคราะห์แล้ว"""
    if not UUID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="job_id ผิดรูปแบบ")

    preview_file = os.path.join(STORAGE_DIR, job_id, "preview.json")
    if not os.path.exists(preview_file):
        raise HTTPException(status_code=404, detail="ไม่พบ preview")

    with open(preview_file, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/health")
async def health():
    """Simple health check"""
    return {"status": "ok"}
