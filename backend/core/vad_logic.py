import torch
import torchaudio
import os

# กำหนด cache dir ให้ชัดเจน (ป้องกัน permission issue ใน Docker)
os.environ.setdefault("TORCH_HOME", "/tmp/torch_hub")

# โหลดโมเดล Silero VAD พร้อม trust_repo=True
# (จำเป็นใน Docker เพราะไม่มี interactive terminal ให้ตอบ y/N)
model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,
    trust_repo=True          # ← แก้ EOFError ใน Docker
)
(get_speech_timestamps, _, read_audio, _, _) = utils


def load_audio_with_torchaudio(audio_path: str, sampling_rate: int = 16000) -> torch.Tensor:
    """
    โหลดไฟล์เสียงใช้ torchaudio แทน read_audio
    (torchaudio มี dependency ครบใน Docker ✅)
    
    Args:
        audio_path: Path to audio file
        sampling_rate: Target sampling rate (default 16000 for Silero VAD)
    
    Returns:
        torch.Tensor: Waveform (1D tensor)
    """
    try:
        # โหลดเสียงและ sample rate
        waveform, sr = torchaudio.load(audio_path)
        
        # แปลงเป็น mono ถ้าเป็น stereo
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)
        
        # Resample ถ้าจำเป็น
        if sr != sampling_rate:
            resampler = torchaudio.transforms.Resample(sr, sampling_rate)
            waveform = resampler(waveform)
        
        # Squeeze เพื่อให้เป็น 1D tensor (ตามที่ Silero VAD คาดหวัง)
        waveform = waveform.squeeze(0).float()
        
        print(f"[VAD] Audio loaded: {audio_path}")
        print(f"[VAD] Duration: {waveform.shape[0] / sampling_rate:.1f}s")
        
        return waveform
        
    except Exception as e:
        print(f"❌ Failed to load audio: {e}")
        raise


def get_voice_activity(audio_path: str, min_silence_gap: float = 2.0) -> list[dict]:
    """
    วิเคราะห์ไฟล์เสียงเพื่อหาช่วงเวลาที่มีการพูดจริง
    และ merge ช่วงที่ห่างกันน้อยกว่า min_silence_gap วินาทีเข้าด้วยกัน
    (เพื่อไม่ให้วิดีโอกระโดดถี่เกินไปตอน FFmpeg ตัด)

    คืนค่า: [{"start": 0.0, "end": 12.5}, ...]
    """
    # ✅ แก้: ใช้ torchaudio.load แทน read_audio
    wav = load_audio_with_torchaudio(audio_path, sampling_rate=16000)

    speech_timestamps = get_speech_timestamps(
        wav, model,
        sampling_rate=16000,
        threshold=0.5,
        min_silence_duration_ms=500,   # ไม่นับ pause เล็กๆ ระหว่างประโยคว่า silence
        min_speech_duration_ms=250,    # ไม่นับ noise สั้นๆ ว่าเป็นเสียงพูด
    )

    # แปลงเป็นวินาที
    raw_segments = [
        {"start": round(ts['start'] / 16000, 2), "end": round(ts['end'] / 16000, 2)}
        for ts in speech_timestamps
    ]

    if not raw_segments:
        print("⚠️ VAD: No speech detected in audio.")
        return []

    # Merge ช่วงที่ห่างกันน้อยกว่า min_silence_gap
    merged = [raw_segments[0].copy()]
    for current in raw_segments[1:]:
        last = merged[-1]
        gap = current["start"] - last["end"]
        if gap <= min_silence_gap:
            last["end"] = current["end"]   # ขยายช่วงปัจจุบัน
        else:
            merged.append(current.copy())  # เพิ่มช่วงใหม่ (มี silence จริงๆ)

    total_speech = sum(s["end"] - s["start"] for s in merged)
    total_silence = sum(
        merged[i]["start"] - merged[i-1]["end"]
        for i in range(1, len(merged))
    )

    print(f"[VAD] Found {len(raw_segments)} raw → {len(merged)} merged voice segments")
    print(f"[VAD] Speech: {total_speech:.1f}s | Silence gaps to cut: {total_silence:.1f}s")

    return merged


def get_silence_gaps(voice_segments: list[dict], total_duration: float, min_gap: float = 2.0) -> list[dict]:
    """
    คำนวณช่วง silence จาก voice_segments
    ใช้สำหรับ debug หรือแสดงผลให้ user เห็น

    คืนค่า: [{"start": ..., "end": ..., "duration": ...}]
    """
    gaps = []
    cursor = 0.0

    for seg in sorted(voice_segments, key=lambda x: x["start"]):
        gap_duration = seg["start"] - cursor
        if gap_duration >= min_gap:
            gaps.append({
                "start": round(cursor, 2),
                "end": round(seg["start"], 2),
                "duration": round(gap_duration, 2)
            })
        cursor = seg["end"]

    # Tail silence (หลังจบเสียงพูดล่าสุด)
    if total_duration - cursor >= min_gap:
        gaps.append({
            "start": round(cursor, 2),
            "end": round(total_duration, 2),
            "duration": round(total_duration - cursor, 2)
        })

    return gaps