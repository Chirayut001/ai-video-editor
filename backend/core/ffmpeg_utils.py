import subprocess
import os
import shutil
from core.srt_utils import generate_srt_from_transcript

def extract_clean_audio(video_path, audio_output_path):
    """
    สกัดเสียงพร้อมลดเสียงรบกวน (Noise Reduction) 
    เพื่อให้ AI (Whisper/Gemini) วิเคราะห์เนื้อหาได้แม่นยำขึ้น
    """
    print(f"🎬 Extracting audio: {video_path} -> {audio_output_path}")
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vn',                         # ไม่เอาวิดีโอ
        '-af', 'afftdn',               # ใส่ฟิลเตอร์ลดเสียงรบกวน (สำคัญมากสำหรับคลิปเสียงนอกสถานที่)
        '-ar', '16000',                # Sample rate 16k (มาตรฐาน AI)
        '-ac', '1',                    # Mono (ลดขนาดไฟล์)
        audio_output_path
    ]
    subprocess.run(cmd, check=True)

def split_video(input_path, output_dir, segment_time=900):
    """
    แบ่งวิดีโอเป็นท่อนละ 15 นาที (900s) 
    รองรับวิดีโอ 3-5 ชั่วโมง เพื่อไม่ให้ RAM ของระบบระเบิด
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    print(f"🎬 Splitting large video into segments: {input_path}")
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-f', 'segment',
        '-segment_time', str(segment_time),
        '-reset_timestamps', '1',
        '-c', 'copy',                  # ใช้ copy เพื่อความเร็วสูงสุด (ไม่ต้องเข้ารหัสใหม่)
        os.path.join(output_dir, 'chunk_%03d.mp4')
    ]
    subprocess.run(cmd, check=True)

def edit_and_merge_video(video_path, highlights_json, output_path, job_dir,
                          transcript=None, burn_subtitle=False):
    """
    หัวใจหลัก: ตัดวิดีโอตามช่วงเวลาที่ AI เลือก และนำมารวมกันเป็นไฟล์เดียว
    ถ้า burn_subtitle=True + transcript: ใส่ subtitle ลงไปด้วย (style เล็กแบบมาตรฐาน 16:9)
    """
    print("\n" + "="*50)
    print(f"🎬 STARTING VIDEO EDITING PROCESS (burn_subtitle={burn_subtitle})")
    print(f"Target segments: {highlights_json}")
    print("="*50 + "\n")

    # 1. ตรวจสอบ: ถ้า AI ไม่ส่งอะไรมาเลย ให้ถือว่างานล้มเหลว (ไม่ก๊อปต้นฉบับไปมั่วๆ)
    if not highlights_json:
        print("⚠️ No highlights found. Process terminated.")
        return None

    temp_dir = os.path.abspath(os.path.join(job_dir, "temp_segments"))
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    segment_list = []

    # 2. ขั้นตอนการตัด (Cutting Phase)
    for i, segment in enumerate(highlights_json):
        start = float(segment.get('start', 0))
        end = float(segment.get('end', 0))
        duration = end - start

        if duration <= 0: continue

        part_filename = f"part_{i}.mp4"
        part_path = os.path.abspath(os.path.join(temp_dir, part_filename))

        print(f"✂️ Cutting Part {i}: {start}s -> {end}s ({duration:.2f}s)")

        # ใช้การ Re-encode ด้วย libx264 เพื่อความแม่นยำของรอยต่อ (Frame-accurate)
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start),         # ใส่ -ss ก่อน -i เพื่อความเร็ว (Fast Seeking)
            '-t', str(duration),
            '-i', video_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',    # ใช้ ultrafast เพื่อความไวสูงสุด
            '-crf', '23',              # ค่าคุณภาพมาตรฐาน (18=ดีมาก, 23=กลาง, 28=เริ่มไม่ชัด)
            '-c:a', 'aac', '-b:a', '128k',
            part_path
        ]
        subprocess.run(cmd, check=True)
        segment_list.append(f"file '{part_filename}'")

    # 3. ขั้นตอนการรวม (Finalizing Phase) → ไป intermediate file ก่อนเสมอ
    if not segment_list:
        print("❌ Error: No valid segments were created.")
        return None

    merged_path = os.path.abspath(os.path.join(temp_dir, "_merged.mp4"))

    if len(segment_list) == 1:
        print("✅ Single segment cut successfully.")
        shutil.move(os.path.join(temp_dir, "part_0.mp4"), merged_path)
    else:
        print(f"🔗 Merging {len(segment_list)} parts together...")
        list_file_path = os.path.join(temp_dir, "list.txt")
        with open(list_file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(segment_list))

        merge_cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', 'list.txt',
            '-c', 'copy',              # รวมไฟล์ที่ encode มาเหมือนกัน ใช้ copy ได้เลย (ไวมาก)
            merged_path,
        ]
        subprocess.run(merge_cmd, check=True, cwd=temp_dir)

    # 4. Burn subtitle (ถ้าเลือก) → re-encode ลง output_path
    if burn_subtitle and transcript:
        srt_path = os.path.abspath(os.path.join(temp_dir, "subs.srt"))
        entry_count = generate_srt_from_transcript(transcript, highlights_json, srt_path)

        if entry_count > 0:
            print(f"📝 [STANDARD] Burning {entry_count} subtitle entries...")
            # Style แบบมาตรฐาน 16:9 — ใหญ่พอ + ขอบบาง
            # FontSize=20 → ~75px @ 1080p (อ่านง่าย ไม่ใหญ่เกิน)
            # Outline=1 → ขอบบาง อ่านบนพื้น bright ได้
            style = (
                "FontName=Garuda,FontSize=20,"
                "PrimaryColour=&Hffffff,OutlineColour=&H000000,"
                "Outline=1,Shadow=0,"
                "Alignment=2,MarginV=25"
            )
            burn_cmd = [
                "ffmpeg", "-y",
                "-i", "_merged.mp4",
                "-vf", f"subtitles=subs.srt:force_style='{style}'",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy",
                os.path.abspath(output_path),
            ]
            subprocess.run(burn_cmd, check=True, cwd=temp_dir)
            print(f"🎉 MISSION SUCCESS (with subtitle): Saved to {output_path}")
            _cleanup_dir(temp_dir)
            return output_path
        else:
            print("⚠️ No subtitle entries generated, skipping burn-in.")

    # ไม่ burn → ย้าย merged → output_path
    shutil.move(merged_path, output_path)
    print(f"🎉 MISSION SUCCESS: Saved to {output_path}")
    _cleanup_dir(temp_dir)
    return output_path


def _cleanup_dir(path: str) -> None:
    """ลบ temp directory แบบ best-effort (ไม่ raise ถ้า fail)"""
    try:
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            print(f"🧹 Cleaned temp: {path}")
    except Exception as e:
        print(f"⚠️ Cleanup failed for {path}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TIKTOK MODE: 9:16 vertical + ≤ target_length + optional burn-in subtitle
# ─────────────────────────────────────────────────────────────────────────────

# Output resolution (TikTok/Reels standard)
TIKTOK_W = 1080
TIKTOK_H = 1920

# Center crop ให้เต็มจอ 1080x1920 (TikTok-style cover — ไม่มีขอบดำ)
# force_original_aspect_ratio=increase: scale UP จนเต็ม canvas (อาจล้น)
# crop: ตัดส่วนล้นทิ้ง — เนื้อหากลางอยู่ครบ
# setsar=1: บังคับ display = storage (กัน anamorphic SAR)
_TIKTOK_SCALE_PAD = (
    f"scale=w={TIKTOK_W}:h={TIKTOK_H}:force_original_aspect_ratio=increase:flags=lanczos,"
    f"crop={TIKTOK_W}:{TIKTOK_H},"
    f"setsar=1"
)


def verify_output_dimensions(video_path: str) -> tuple[int, int, str]:
    """ตรวจ dimensions + SAR ของ output ด้วย ffprobe → log + return"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,sample_aspect_ratio",
            "-of", "csv=p=0", video_path,
        ],
        capture_output=True, text=True, check=True,
    )
    parts = result.stdout.strip().split(",")
    w = int(parts[0])
    h = int(parts[1])
    sar = parts[2] if len(parts) > 2 and parts[2] else "1:1"
    aspect = "9:16 ✅" if h > w else ("16:9 ❌" if w > h else "1:1")
    print(f"📐 [VERIFY] {os.path.basename(video_path)}: {w}x{h}, SAR={sar} → {aspect}")
    return w, h, sar


def render_tiktok_video(video_path, keep_segments, transcript, output_path, job_dir,
                        target_length=60, burn_subtitle=True):
    """
    TikTok rendering pipeline:
    1. Cut each segment + scale-and-pad to 1080x1920 (9:16) ใน pass เดียว
    2. Concat ทุก parts
    3. (option) Generate SRT จาก transcript + burn-in
    """
    print("\n" + "=" * 50)
    print(f"🎬 STARTING TIKTOK RENDER (target ≤ {target_length}s, subtitle={burn_subtitle})")
    print(f"Segments: {keep_segments}")
    print("=" * 50 + "\n")

    if not keep_segments:
        print("⚠️ No segments to render. Aborted.")
        return None

    temp_dir = os.path.abspath(os.path.join(job_dir, "tiktok_temp"))
    os.makedirs(temp_dir, exist_ok=True)

    # ── Step 1: Cut + scale+pad ทีละ part ─────────────────────────────────────
    part_files = []
    for i, segment in enumerate(keep_segments):
        start = float(segment["start"])
        end = float(segment["end"])
        duration = end - start
        if duration <= 0:
            continue

        part_name = f"part_{i}.mp4"
        part_path = os.path.abspath(os.path.join(temp_dir, part_name))

        print(f"✂️ [TIKTOK] Cutting Part {i}: {start}s → {end}s ({duration:.2f}s)")
        cmd = [
            "ffmpeg", "-y",
            "-noautorotate",                 # ignore source rotation metadata
            "-ss", str(start),
            "-t", str(duration),
            "-i", video_path,
            "-vf", _TIKTOK_SCALE_PAD,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30",
            "-metadata:s:v:0", "rotate=0",   # clear rotation metadata in output
            "-movflags", "+faststart",
            part_path,
        ]
        subprocess.run(cmd, check=True)
        part_files.append(part_name)

    if not part_files:
        print("❌ No valid parts created.")
        return None

    # ── Step 2: Concat parts → intermediate video ─────────────────────────────
    list_path = os.path.join(temp_dir, "list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(f"file '{p}'" for p in part_files))

    concat_output = os.path.abspath(os.path.join(temp_dir, "_concat.mp4"))
    print(f"🔗 [TIKTOK] Concatenating {len(part_files)} parts...")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", "list.txt",
        "-c", "copy",
        concat_output,
    ], check=True, cwd=temp_dir)

    # ── Step 3: Burn subtitle (optional) ─────────────────────────────────────
    if burn_subtitle and transcript:
        srt_path = os.path.abspath(os.path.join(temp_dir, "subs.srt"))
        entry_count = generate_srt_from_transcript(transcript, keep_segments, srt_path)

        if entry_count == 0:
            print("⚠️ No subtitle entries generated, skipping burn-in.")
            shutil.move(concat_output, output_path)
        else:
            print(f"📝 [TIKTOK] Burning {entry_count} subtitle entries...")
            # libass ใช้ default PlayResY=288 — FontSize/MarginV ใน units นี้ จะถูก scale ขึ้น 1920/288 = ×6.67
            # FontName=Garuda: Thai+Latin glyphs ขนาดสมดุล
            # Outline=1: ขอบบาง ๆ อ่านง่ายบนทุก background (กัน contrast loss ฉาก bright)
            style = (
                "FontName=Garuda,FontSize=18,"
                "PrimaryColour=&Hffffff,OutlineColour=&H000000,"
                "Outline=1,Shadow=0,"
                "Alignment=2,MarginV=30"
            )
            burn_cmd = [
                "ffmpeg", "-y",
                "-i", "_concat.mp4",
                "-vf", (
                    f"scale={TIKTOK_W}:{TIKTOK_H}:flags=lanczos,"
                    f"setsar=1,"
                    f"subtitles=subs.srt:force_style='{style}'"
                ),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy",
                "-metadata:s:v:0", "rotate=0",
                os.path.abspath(output_path),
            ]
            subprocess.run(burn_cmd, check=True, cwd=temp_dir)
    else:
        # No subtitle → ย้าย concat output เป็น final
        shutil.move(concat_output, output_path)

    # Verify output dimensions เพื่อความมั่นใจ
    try:
        verify_output_dimensions(output_path)
    except Exception as e:
        print(f"⚠️ Verify dimensions failed: {e}")

    print(f"🎉 TIKTOK RENDER SUCCESS: {output_path}")
    _cleanup_dir(temp_dir)
    return output_path