import os
from celery import Celery
from celery.exceptions import Ignore
from core.ffmpeg_utils import extract_clean_audio, edit_and_merge_video, render_tiktok_video
from core.ai_logic import analyze_video_content
from core.vad_logic import get_voice_activity, get_silence_gaps
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


@celery_app.task(bind=True, max_retries=3)
def process_video_task(self, job_id, video_path, user_prompt,
                       output_mode="standard", target_length=60, burn_subtitle=False):
    """
    Pipeline (อัปเกรด):
    1. Extract audio (FFmpeg)          ← skip ถ้าไฟล์มีอยู่แล้ว
    2. VAD — หา voice segments         ← ตัด silence ก่อน AI
    3. Transcribe + AI analyze         ← ส่ง voice_segments ให้ AI ด้วย
    4. Render — แยก path:
       - standard: edit_and_merge_video (เหมือนเดิม)
       - tiktok:   render_tiktok_video (9:16 + subtitle)
    """
    job_dir = os.path.dirname(video_path)
    audio_path = os.path.join(job_dir, "full_audio.wav")
    final_video_name = "final_summary.mp4"
    final_output = os.path.join(job_dir, final_video_name)

    try:
        # ── Step 1: Extract audio ─────────────────────────────────────────────
        if os.path.exists(audio_path):
            print(f"[SKIP] Audio already exists, skipping FFmpeg extract.")
        else:
            self.update_state(state='PROGRESS', meta={
                'status': 'Step 1/4: Extracting audio...',
                'progress': 10
            })
            extract_clean_audio(video_path, audio_path)

        # ── Step 2: VAD — หา voice segments ──────────────────────────────────
        self.update_state(state='PROGRESS', meta={
            'status': 'Step 2/4: Detecting voice activity...',
            'progress': 25
        })

        try:
            voice_segments = get_voice_activity(audio_path, min_silence_gap=2.0)
            print(f"[VAD] {len(voice_segments)} voice segments detected")
        except Exception as vad_err:
            # VAD ล้มเหลว → ไม่หยุด pipeline แค่ข้ามไป (ใช้ transcript เต็ม)
            print(f"⚠️ VAD failed ({vad_err}), falling back to full transcript")
            voice_segments = None

        # ── Step 3: Whisper + Gemini (Deletion mode) ──────────────────────────
        self.update_state(state='PROGRESS', meta={
            'status': 'Step 3/4: Transcribing & AI analyzing...',
            'progress': 45
        })

        # ส่ง voice_segments ให้ ai_logic กรอง transcript ก่อนส่ง Gemini
        ai_result, transcript = analyze_video_content(
            audio_path=audio_path,
            user_prompt=user_prompt,
            voice_segments=voice_segments,
            output_mode=output_mode,
            target_length=target_length,
        )

        if not ai_result:
            raise Exception("AI ไม่สามารถระบุช่วงที่ควรเก็บได้ — กรุณาลองใหม่")

        # Log summary ให้ดูใน terminal
        total_keep = sum(s["end"] - s["start"] for s in ai_result)
        print(f"\n📊 Edit Summary ({output_mode} mode):")
        print(f"   Segments to keep : {len(ai_result)}")
        print(f"   Total keep duration: {total_keep:.1f}s ({total_keep/60:.1f} min)")

        # ── Step 4: ตัดและรวมวิดีโอ (แยก path ตาม output_mode) ──────────────
        self.update_state(state='PROGRESS', meta={
            'status': f'Step 4/4: Rendering ({output_mode})...',
            'progress': 80
        })

        if output_mode == "tiktok":
            render_tiktok_video(
                video_path, ai_result, transcript, final_output, job_dir,
                target_length=target_length, burn_subtitle=burn_subtitle,
            )
        else:
            edit_and_merge_video(
                video_path, ai_result, final_output, job_dir,
                transcript=transcript, burn_subtitle=burn_subtitle,
            )

        output_url = f"{job_id}/{final_video_name}"
        return {
            "status": "SUCCESS",
            "progress": 100,
            "output_url": output_url,
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
            retry_num = self.request.retries + 1
            print(f"[RETRY {retry_num}/{self.max_retries}] Gemini 503 — waiting {wait_seconds}s...")
            self.update_state(state='PROGRESS', meta={
                'status': f'Gemini overloaded, retrying in {wait_seconds}s... ({retry_num}/{self.max_retries})',
                'progress': 35
            })
            raise self.retry(exc=e, countdown=wait_seconds)

        print(f"[FAILURE] Task failed: {error_msg}")
        self.update_state(
            state='FAILURE',
            meta={
                'status': f'Error: {error_msg}',
                'progress': 0,
                'exc_type': type(e).__name__,
                'exc_message': error_msg,
            }
        )
        raise Ignore()