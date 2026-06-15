import os
import re
import time
import uuid
import shutil
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from celery.result import AsyncResult

from tasks import process_video_task

# ── Constants / Limits ───────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 2048               # 2GB upload limit
MAX_PROMPT_LENGTH = 2000               # 2000 chars prompt
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ALLOWED_OUTPUT_MODES = {"standard", "tiktok"}
MIN_TARGET_LENGTH = 10
MAX_TARGET_LENGTH = 600
JOB_RETENTION_DAYS = 7                 # ลบ job เก่ากว่า 7 วัน
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]")

STORAGE_DIR = "storage"


def safe_filename(filename: str) -> str:
    """กรอง filename ให้ปลอดภัย — กัน path traversal และตัวอักษรพิเศษ"""
    name = os.path.basename(filename or "")    # strip directory
    name = name.lstrip(".")                     # strip leading dots
    name = SAFE_FILENAME_RE.sub("_", name)
    name = name[:200]                           # limit length
    return name or "upload.mp4"


def cleanup_old_jobs():
    """ลบ folder ใน storage/ ที่เก่ากว่า JOB_RETENTION_DAYS"""
    if not os.path.exists(STORAGE_DIR):
        return
    cutoff = time.time() - JOB_RETENTION_DAYS * 86400
    removed = 0
    for entry in os.listdir(STORAGE_DIR):
        path = os.path.join(STORAGE_DIR, entry)
        if not os.path.isdir(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except Exception as e:
            print(f"⚠️ cleanup failed for {path}: {e}")
    if removed:
        print(f"🧹 Cleaned up {removed} old job dir(s) (>{JOB_RETENTION_DAYS} days)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    os.makedirs(STORAGE_DIR, exist_ok=True)
    cleanup_old_jobs()
    yield
    # shutdown — no-op


app = FastAPI(lifespan=lifespan)

# ── CORS: lock ลงเฉพาะ localhost (กัน CSRF) ──────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:80",
        "http://127.0.0.1:80",
        "http://localhost:5173",   # vite dev
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")


@app.post("/upload")
async def upload_video(
    video: UploadFile = File(...),
    prompt: str = Form(...),
    output_mode: str = Form("standard"),
    target_length: int = Form(60),
    burn_subtitle: bool = Form(False),
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
            args=[job_id, video_path, prompt, output_mode, target_length, burn_subtitle],
            task_id=job_id,
        )

        return {"job_id": job_id}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Upload error: {e}")
        raise HTTPException(status_code=500, detail="อัปโหลดล้มเหลว กรุณาลองใหม่")


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    # ── Validate jobId format กัน frontend ติด PENDING บน id ไม่ถูกต้อง ─────
    if not UUID_PATTERN.match(job_id):
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


@app.get("/health")
async def health():
    """Simple health check"""
    return {"status": "ok"}
