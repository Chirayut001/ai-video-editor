import os
import json
import time
from celery import Celery
from celery.exceptions import Ignore
from core.ffmpeg_utils import extract_clean_audio, edit_and_merge_video, render_tiktok_video
from core.ai_logic import analyze_video_content
from core.vad_logic import get_voice_activity
from core.srt_utils import generate_phrases_from_transcript
from dotenv import load_dotenv

load_dotenv()

celery_app = Celery('video_tasks', broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
celery_app.conf.update(
    result_backend=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    task_track_started=True,
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
)


PREVIEW_FILENAME = "preview.json"
FINAL_VIDEO_NAME = "final_summary.mp4"
# marker บอกว่า job dir นี้กำลังถูกประมวลผล — main.py cleanup จะข้าม dir ที่มี marker สด
# (กัน race ที่ cleanup ลบ dir กลางคันขณะ worker ทำงาน)
PROCESSING_MARKER = ".processing"


def _mark_processing(job_dir: str) -> None:
    """เขียน marker .processing (best-effort) ตอนเริ่ม task"""
    try:
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, PROCESSING_MARKER), "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        print(f"⚠️ mark_processing failed for {job_dir}: {e}")


def _clear_processing(job_dir: str) -> None:
    """ลบ marker .processing ตอน task จบ (สำเร็จหรือ fail ก็ตาม)"""
    try:
        os.remove(os.path.join(job_dir, PROCESSING_MARKER))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ clear_processing failed for {job_dir}: {e}")


def _preview_path(job_dir: str) -> str:
    return os.path.join(job_dir, PREVIEW_FILENAME)


def _save_preview(job_dir: str, data: dict) -> None:
    with open(_preview_path(job_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_preview(job_dir: str) -> dict:
    with open(_preview_path(job_dir), "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1: Analyze pipeline (Whisper + Gemini)
#         - ถ้า preview_mode=True → save result + return (ไม่ render)
#         - ถ้า preview_mode=False → render ต่อทันที
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3)
def process_video_task(self, job_id, video_path, user_prompt,
                       output_mode="standard", target_length=60, burn_subtitle=False,
                       preview_mode=False, preset_id=""):
    job_dir = os.path.dirname(video_path)
    audio_path = os.path.join(job_dir, "full_audio.wav")
    final_output = os.path.join(job_dir, FINAL_VIDEO_NAME)

    _mark_processing(job_dir)   # กัน cleanup ลบ dir กลางคัน
    try:
        # ── Step 1: Extract audio ────────────────────────────────────────────
        if os.path.exists(audio_path):
            print(f"[SKIP] Audio already exists")
        else:
            self.update_state(state='PROGRESS', meta={
                'status': 'Step 1/4: Extracting audio...', 'progress': 10
            })
            extract_clean_audio(video_path, audio_path)

        # ── Step 2: VAD ───────────────────────────────────────────────────────
        self.update_state(state='PROGRESS', meta={
            'status': 'Step 2/4: Detecting voice activity...', 'progress': 25
        })
        try:
            voice_segments = get_voice_activity(audio_path, min_silence_gap=2.0)
        except Exception as vad_err:
            print(f"⚠️ VAD failed ({vad_err}), falling back")
            voice_segments = None

        # ── Step 3: Whisper + Gemini ──────────────────────────────────────────
        self.update_state(state='PROGRESS', meta={
            'status': 'Step 3/4: Transcribing & AI analyzing...', 'progress': 45
        })
        ai_result, transcript = analyze_video_content(
            audio_path=audio_path,
            user_prompt=user_prompt,
            voice_segments=voice_segments,
            output_mode=output_mode,
            target_length=target_length,
            preset_id=preset_id,
        )
        if not ai_result:
            raise Exception("AI ไม่สามารถระบุช่วงที่ควรเก็บได้ — กรุณาลองใหม่")

        total_keep = sum(s["end"] - s["start"] for s in ai_result)
        print(f"\n📊 Edit Summary ({output_mode}): {len(ai_result)} segments, {total_keep:.1f}s")

        # ── PREVIEW MODE: save แล้ว return ────────────────────────────────────
        if preview_mode:
            # Pre-generate subtitle phrases (สำหรับให้ user แก้ก่อน render ถ้าต้องการ)
            try:
                phrases = generate_phrases_from_transcript(transcript or [], ai_result)
                print(f"📝 Pre-generated {len(phrases)} subtitle phrases for editing")
            except Exception as ph_err:
                print(f"⚠️ Phrase generation failed: {ph_err}")
                phrases = []

            preview_data = {
                "job_id": job_id,
                "video_path": video_path,
                "user_prompt": user_prompt,
                "output_mode": output_mode,
                "target_length": target_length,
                "burn_subtitle": burn_subtitle,
                "segments": ai_result,
                "transcript": transcript,    # ← เก็บ transcript reuse ใน render
                "subtitle_phrases": phrases, # ← phrases ที่ user จะแก้ได้
                "total_keep_seconds": round(total_keep, 1),
            }
            _save_preview(job_dir, preview_data)
            return {
                "status": "SUCCESS",
                "progress": 100,
                "mode": "preview",
                "message": "วิเคราะห์เสร็จ — กรุณา review",
                "segments": ai_result,
                "edit_summary": {
                    "segments_kept": len(ai_result),
                    "duration_kept_seconds": round(total_keep, 1),
                }
            }

        # ── Step 4: Render ────────────────────────────────────────────────────
        self.update_state(state='PROGRESS', meta={
            'status': f'Step 4/4: Rendering ({output_mode})...', 'progress': 80
        })
        _render(video_path, ai_result, transcript, final_output, job_dir,
                output_mode, target_length, burn_subtitle)

        return {
            "status": "SUCCESS",
            "progress": 100,
            "mode": "final",
            "output_url": f"{job_id}/{FINAL_VIDEO_NAME}",
            "message": "ตัดต่อเสร็จเรียบร้อย!",
            "edit_summary": {
                "segments_kept": len(ai_result),
                "duration_kept_seconds": round(total_keep, 1),
            }
        }

    except Exception as e:
        error_msg = str(e)
        is_503 = "503" in error_msg or "UNAVAILABLE" in error_msg
        if is_503 and self.request.retries < self.max_retries:
            wait_seconds = 30 * (2 ** self.request.retries)
            print(f"[RETRY {self.request.retries + 1}] Gemini 503 — wait {wait_seconds}s")
            raise self.retry(exc=e, countdown=wait_seconds)

        print(f"[FAILURE] {error_msg}")
        self.update_state(state='FAILURE', meta={
            'status': f'Error: {error_msg}', 'progress': 0,
            'exc_type': type(e).__name__, 'exc_message': error_msg,
        })
        raise Ignore()

    finally:
        _clear_processing(job_dir)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2: Render-only (ใช้ปริ่ม preview เพื่อ render ด้วย segments ที่ user เลือก)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True)
def render_only_task(self, job_id, selected_segments, edited_phrases=None):
    """
    Render video จาก preview ที่ save ไว้ โดยใช้ segments ที่ user เลือก
    selected_segments: [{"start": .., "end": ..}, ...] ที่ user approve
    edited_phrases:    [{"start": .., "end": .., "text": ...}, ...] ถ้า user แก้ subtitle (optional)
    """
    job_dir = f"storage/{job_id}"
    _mark_processing(job_dir)   # กัน cleanup ลบ dir กลางคัน
    try:
        preview = _load_preview(job_dir)

        video_path = preview["video_path"]
        output_mode = preview["output_mode"]
        target_length = preview["target_length"]
        burn_subtitle = preview["burn_subtitle"]
        final_output = os.path.join(job_dir, FINAL_VIDEO_NAME)

        if not selected_segments:
            raise Exception("ไม่มี segments ที่เลือก")

        self.update_state(state='PROGRESS', meta={
            'status': 'กำลัง render วิดีโอ...', 'progress': 50
        })

        # ใช้ transcript จาก preview (ไม่ต้อง re-transcribe = ประหยัดเวลา 3-5 นาที!)
        transcript = preview.get("transcript") or []

        # Clean segments (start/end only ที่ FFmpeg ต้องการ)
        clean_segs = [{"start": s["start"], "end": s["end"]} for s in selected_segments]

        # ถ้า user แก้ subtitle → ใช้ edited phrases, ไม่งั้น regenerate
        final_phrases = None
        if burn_subtitle:
            if edited_phrases is not None:
                final_phrases = edited_phrases
                print(f"📝 Using {len(edited_phrases)} user-edited subtitle phrases")
            else:
                # ไม่มี edit → regenerate จาก transcript + clean_segs
                # (เผื่อ user แก้ segments selection แต่ไม่แก้ subtitle text)
                final_phrases = generate_phrases_from_transcript(transcript, clean_segs)
                print(f"📝 Auto-generated {len(final_phrases)} subtitle phrases")

        _render(video_path, clean_segs, transcript or [], final_output, job_dir,
                output_mode, target_length, burn_subtitle, edited_phrases=final_phrases)

        total_keep = sum(s["end"] - s["start"] for s in clean_segs)
        return {
            "status": "SUCCESS",
            "progress": 100,
            "mode": "final",
            "output_url": f"{job_id}/{FINAL_VIDEO_NAME}",
            "message": "ตัดต่อเสร็จเรียบร้อย!",
            "edit_summary": {
                "segments_kept": len(clean_segs),
                "duration_kept_seconds": round(total_keep, 1),
            }
        }

    except Exception as e:
        error_msg = str(e)
        print(f"[RENDER FAILURE] {error_msg}")
        self.update_state(state='FAILURE', meta={
            'status': f'Error: {error_msg}', 'progress': 0,
            'exc_type': type(e).__name__, 'exc_message': error_msg,
        })
        raise Ignore()

    finally:
        _clear_processing(job_dir)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Render branch (standard vs tiktok)
# ─────────────────────────────────────────────────────────────────────────────

def _render(video_path, segments, transcript, final_output, job_dir,
            output_mode, target_length, burn_subtitle, edited_phrases=None):
    if output_mode == "tiktok":
        render_tiktok_video(
            video_path, segments, transcript, final_output, job_dir,
            target_length=target_length, burn_subtitle=burn_subtitle,
            edited_phrases=edited_phrases,
        )
    else:
        edit_and_merge_video(
            video_path, segments, final_output, job_dir,
            transcript=transcript, burn_subtitle=burn_subtitle,
            edited_phrases=edited_phrases,
        )
