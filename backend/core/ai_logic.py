from google import genai
from google.genai import errors as genai_errors
import json
import os
import time
import re
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()

# ── Multi API Key support ─────────────────────────────────────────────────────
# รองรับทั้ง:
#   GEMINI_API_KEYS=key1,key2,key3   ← multi-key (แนะนำ)
#   GEMINI_API_KEY=key1              ← single (backward compat)
_keys_csv = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY", "")
API_KEYS = [k.strip() for k in _keys_csv.split(",") if k.strip()]
if not API_KEYS:
    raise Exception("ไม่พบ GEMINI_API_KEY/GEMINI_API_KEYS ใน .env")

print(f"🔑 Loaded {len(API_KEYS)} Gemini API key(s)")

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-3-flash-preview"
]

# Cache client ต่อ key เพื่อไม่ต้อง re-init ทุกครั้ง
_clients_cache: dict[str, "genai.Client"] = {}


def get_client(api_key: str) -> "genai.Client":
    if api_key not in _clients_cache:
        _clients_cache[api_key] = genai.Client(api_key=api_key)
    return _clients_cache[api_key]


_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import torch
        if torch.cuda.is_available():
            device, compute_type = "cuda", "float16"
            print(f"Loading Whisper on GPU: {torch.cuda.get_device_name(0)}")
        else:
            device, compute_type = "cpu", "int8"
            print("Loading Whisper on CPU (no CUDA detected)")
        try:
            _whisper_model = WhisperModel("small", device=device, compute_type=compute_type)
            print(f"Whisper model loaded ({device}/{compute_type}).")
        except Exception as e:
            # กัน model cache เสีย — clear ออกแล้วให้ครั้งหน้าโหลดใหม่
            _whisper_model = None
            raise Exception(f"Whisper model load failed: {e}")
    return _whisper_model


def reset_whisper_model():
    """ใช้ตอน error เพื่อบังคับโหลดใหม่ครั้งหน้า"""
    global _whisper_model
    _whisper_model = None


def transcribe_audio(audio_path: str) -> list[dict]:
    """
    แปลงเสียงเป็น transcript พร้อม timestamp ระดับประโยค
    คืนค่า: [{"start": 0.0, "end": 4.2, "text": "..."}]
    """
    model = get_whisper_model()
    print(f"Transcribing: {audio_path}")

    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        language=None,
        word_timestamps=True
    )

    print(f"Detected language: {info.language} (confidence: {info.language_probability:.2f})")

    transcript = []
    for seg in segments:
        words_data = []
        if getattr(seg, "words", None):
            for w in seg.words:
                token = (w.word or "").strip()
                if not token:
                    continue
                words_data.append({
                    "start": round(w.start, 2),
                    "end":   round(w.end, 2),
                    "text":  token,
                })
        transcript.append({
            "start": round(seg.start, 2),
            "end":   round(seg.end, 2),
            "text":  seg.text.strip(),
            "words": words_data,
        })

    print(f"Transcribed {len(transcript)} segments "
          f"({sum(len(s['words']) for s in transcript)} word tokens).")
    return transcript


def filter_transcript_by_vad(transcript: list[dict], voice_segments: list[dict]) -> list[dict]:
    """
    [PRE-FILTER] กรอง transcript ให้เหลือเฉพาะช่วงที่ VAD ยืนยันว่ามีเสียงพูดจริง
    ตัด silence / noise ออกก่อนส่งให้ AI — ประหยัด token + ลด hallucination

    transcript    : output จาก Whisper
    voice_segments: output จาก vad_logic.get_voice_activity()
    """
    if not voice_segments:
        print("⚠️ VAD returned no voice segments — using full transcript as fallback")
        return transcript

    filtered = []
    for seg in transcript:
        seg_mid = (seg["start"] + seg["end"]) / 2
        # เก็บ segment ถ้า midpoint อยู่ในช่วงที่ VAD บอกว่ามีเสียงพูด
        for v in voice_segments:
            if v["start"] <= seg_mid <= v["end"]:
                filtered.append(seg)
                break

    removed = len(transcript) - len(filtered)
    print(f"[VAD Pre-filter] Removed {removed} silent segments, kept {len(filtered)}/{len(transcript)}")
    return filtered


def snap_to_sentence_boundary(ai_end: float, transcript: list[dict], tolerance: float = 2.0) -> float:
    """ขยับ end timestamp ให้ตรงกับจุดจบประโยคจริงใน transcript"""
    closest = min(transcript, key=lambda s: abs(s["end"] - ai_end))
    if abs(closest["end"] - ai_end) <= tolerance:
        return closest["end"]
    return ai_end


def merge_close_segments(segments: list[dict], gap_threshold: float = 2.0) -> list[dict]:
    """รวม segment ที่ห่างกันน้อยกว่า gap_threshold วินาที"""
    if not segments:
        return []

    segments.sort(key=lambda x: x['start'])
    merged = [segments[0].copy()]
    for current in segments[1:]:
        last = merged[-1]
        if current["start"] - last["end"] <= gap_threshold:
            last["end"] = current["end"]
        else:
            merged.append(current.copy())
    return merged


def invert_segments(keep_segments: list[dict], total_duration: float) -> list[dict]:
    """
    แปลง "ช่วงที่ควรลบ" → "ช่วงที่ควรเก็บ"
    หรือ แปลง "ช่วงที่ควรเก็บ" → "ช่วงที่ควรลบ" (ใช้ได้สองทาง)
    """
    if not keep_segments:
        return [{"start": 0.0, "end": total_duration}]

    keep_segments = sorted(keep_segments, key=lambda x: x["start"])
    inverted = []
    cursor = 0.0

    for seg in keep_segments:
        if seg["start"] > cursor + 0.1:
            inverted.append({"start": round(cursor, 2), "end": round(seg["start"], 2)})
        cursor = seg["end"]

    if cursor < total_duration - 0.1:
        inverted.append({"start": round(cursor, 2), "end": round(total_duration, 2)})

    return inverted


def call_gemini_with_retry(full_prompt: str, max_attempts_per_model: int = 2) -> str:
    """
    เรียก Gemini พร้อม fallback chain:
    1. วน KEY (outer) — ถ้า quota หมดบน key 1 → switch ไป key 2
    2. วน MODEL (middle) — fallback gemini-2.5-flash → 2.0-flash → 3-flash-preview
    3. วน ATTEMPT (inner) — retry ถ้า 503 server overload
    """
    last_error = None

    for key_idx, api_key in enumerate(API_KEYS, start=1):
        client = get_client(api_key)
        key_label = f"key#{key_idx}/{len(API_KEYS)}"
        print(f"🔑 [{key_label}] Trying with API key …{api_key[-5:]}")

        key_quota_exhausted = False

        for model_name in FALLBACK_MODELS:
            if key_quota_exhausted:
                # ถ้า quota หมดทั้ง key — ไม่ต้องลอง model อื่นใน key นี้
                break

            for attempt in range(max_attempts_per_model):
                try:
                    print(f"🚀 [{key_label}] Trying {model_name} (Attempt {attempt + 1}/{max_attempts_per_model})...")
                    response = client.models.generate_content(
                        model=model_name,
                        contents=full_prompt,
                    )
                    return response.text

                except genai_errors.ServerError as e:
                    last_error = e
                    is_busy = "503" in str(e) or "UNAVAILABLE" in str(e)
                    if is_busy:
                        if attempt < max_attempts_per_model - 1:
                            wait = 10 * (attempt + 1)
                            print(f"⚠️ {model_name} overloaded. Waiting {wait}s before retry...")
                            time.sleep(wait)
                        else:
                            print(f"🔄 {model_name} still busy. Switching model...")
                            break
                    else:
                        print(f"❌ {model_name} internal error. Switching model...")
                        break

                except genai_errors.ClientError as e:
                    last_error = e
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        print(f"📉 {model_name} quota exceeded on {key_label}.")
                        # quota หมดทั้ง key — switch ทั้งคีย์ ไม่ใช่แค่ model
                        # (เพราะ Gemini ใช้ quota ระดับ project/account)
                        if "PROJECT" in err_str.upper() or "DAILY" in err_str.upper():
                            key_quota_exhausted = True
                            break
                        else:
                            # quota แค่ model นี้ → ลอง model อื่นใน key เดิม
                            break
                    elif "401" in err_str or "UNAUTHENTICATED" in err_str:
                        print(f"🚫 {key_label} invalid key, switching key...")
                        key_quota_exhausted = True
                        break
                    else:
                        print(f"🚫 Critical Client Error: {e}")
                        raise

                except Exception as e:
                    print(f"❓ Unexpected Error with {model_name}: {e}")
                    last_error = e
                    break

        print(f"🔁 [{key_label}] exhausted, trying next key...")

    raise Exception(
        f"💥 All {len(API_KEYS)} key(s) × {len(FALLBACK_MODELS)} model(s) exhausted. "
        f"Final reason: {last_error}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def analyze_video_content(
    audio_path: str,
    user_prompt: str,
    voice_segments: list[dict] = None,  # ← รับ VAD output จาก tasks.py
    output_mode: str = "standard",       # "standard" หรือ "tiktok"
    target_length: int = 60,             # ใช้เมื่อ output_mode == "tiktok"
) -> tuple[list[dict], list[dict]]:
    """
    Pipeline:
    1. Whisper → transcript (timestamp แม่นยำ)
    2. VAD pre-filter → ตัด silence ออกก่อนส่ง AI
    3. Gemini → ระบุช่วงที่ "ควรเก็บ"
       - standard: Deletion mode (ระบุช่วงลบ แล้ว invert)
       - tiktok: Peak extraction mode (เลือก best segments ≤ target_length)
    4. Post-process → snap + merge / priority trim

    คืนค่า: (keep_segments, transcript)
        keep_segments: [{"start": 10.0, "end": 45.2}]
        transcript:    [{"start": 1.2, "end": 4.5, "text": "..."}]
    """

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    try:
        transcript = transcribe_audio(audio_path)
    except Exception as e:
        # Reset model cache กัน corrupt — ครั้งหน้าจะโหลดใหม่
        reset_whisper_model()
        raise Exception(f"Transcription failed: {e}")

    if not transcript:
        raise Exception("Whisper ไม่สามารถถอดเสียงได้ — ตรวจสอบไฟล์เสียง")

    # Guard: ใช้ค่า end ที่มีจริงในทุก segment (เผื่อ Whisper ส่ง [-1].end ว่าง)
    valid_ends = [s.get("end", 0) for s in transcript if s.get("end")]
    if not valid_ends:
        raise Exception("Transcript ไม่มี timestamp ที่ใช้ได้")
    total_duration = max(valid_ends)
    print(f"Total duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")

    # ── Step 2: VAD Pre-filter ────────────────────────────────────────────────
    # ถ้า caller ส่ง voice_segments มา ให้ filter ก่อนส่ง AI
    # ถ้าไม่ส่งมา (backward compat) ใช้ transcript เต็ม
    filtered_transcript = (
        filter_transcript_by_vad(transcript, voice_segments)
        if voice_segments
        else transcript
    )

    # ── Step 3: Gemini วิเคราะห์ transcript ──────────────────────────────────
    ai_json_data = json.dumps(filtered_transcript, ensure_ascii=False)

    # Debug: แสดงใน terminal
    debug_json = json.dumps(filtered_transcript, ensure_ascii=False, indent=2)
    print(f"\n--- [DEBUG] Filtered Transcript ({len(filtered_transcript)} segments) ---")
    print(debug_json[:2000] + ("..." if len(debug_json) > 2000 else ""))
    print("----------------------------------------------------------------------\n")

    # ── TIKTOK MODE: ใช้ prompt + post-process แยก ────────────────────────────
    if output_mode == "tiktok":
        return _analyze_tiktok_mode(
            user_prompt, ai_json_data, transcript, total_duration, target_length
        )

    full_prompt = f"""
คุณคือ บรรณาธิการวิดีโอมืออาชีพ (Senior Video Editor)
งานของคุณคือ **ระบุช่วงที่ควรตัดออก** จากวิดีโอต้นฉบับ เพื่อให้วิดีโอที่เหลือดูรู้เรื่องโดยไม่ต้องดูต้นฉบับเต็ม

คำสั่งจากผู้ใช้: "{user_prompt}"
ความยาววิดีโอทั้งหมด: {total_duration/60:.1f} นาที

นี่คือ Transcript (ผ่าน VAD filter แล้ว ตัด silence ออกไปบ้างแล้ว):
{ai_json_data}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[หลักการ: ลบเฉพาะสิ่งที่ไม่มีคุณค่า]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ ตัดออก เมื่อพบสิ่งเหล่านี้:
  - Filler / วนซ้ำ: พูดซ้ำความคิดเดิมโดยไม่เพิ่มข้อมูลใหม่
  - Off-topic tangent: เรื่องเล่าที่ไม่ได้ช่วย illustrate ประเด็นหลักเลย
  - Technical issues: เสียงหาย, รอ load, แก้ปัญหาหน้ากล้อง
  - Unnecessary small talk: คุยนอกเรื่องที่ไม่เชื่อมกับเนื้อหา

✅ เก็บไว้เสมอ:
  - Intro: แนะนำตัว, บอกว่าวันนี้จะพูดเรื่องอะไร (สำคัญมาก)
  - Core content: ทุกประเด็นหลักและการอธิบาย
  - Examples / Stories: ถ้าช่วย illustrate ประเด็นหลัก → เก็บ
  - Transitions: ประโยคที่เชื่อมระหว่างประเด็น
  - Outro: สรุป, บทสรุป, call to action

⚖️ กฎสำคัญ:
  - ถ้าไม่แน่ใจ → เก็บไว้ก่อน (อย่าตัดของดีออก)
  - ห้ามตัดกลางประเด็น (ต้องรอให้ประเด็นนั้นจบก่อน)
  - แต่ละช่วงที่จะลบต้องยาวอย่างน้อย 5 วินาที (ไม่ตัดสั้นๆ จนกระโดด)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[รูปแบบผลลัพธ์]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

คืนค่า JSON Array ของช่วงที่ "ควรลบ" เท่านั้น (ห้ามมี Markdown, ห้ามมีข้อความอื่น):
[
  {{
    "start": 12.5,
    "end": 18.0,
    "reason": "อธิบายว่าทำไมถึงลบ",
    "confidence": "high"
  }}
]

confidence: "high" = มั่นใจว่าลบได้เลย | "medium" = ลบได้แต่ควร review | "low" = ไม่แน่ใจ ให้คน review ก่อน
ถ้าไม่มีช่วงที่ควรลบเลย ให้คืนค่า []
"""

    print("Sending filtered transcript to Gemini (Deletion mode)...")
    response_text = call_gemini_with_retry(full_prompt)

    # ── Step 4: Parse AI response ─────────────────────────────────────────────
    json_match = re.search(r'\[\s*(\{.*?\}\s*,?\s*)*\]', response_text, re.DOTALL)

    if json_match:
        clean_text = json_match.group(0)
    else:
        clean_text = response_text.replace("```json", "").replace("```", "").strip()

    try:
        segments_to_delete = json.loads(clean_text)
        segments_to_delete.sort(key=lambda x: float(x.get("start", 0)))
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}\nRaw response:\n{response_text}")
        raise Exception(f"Gemini คืนค่า JSON ไม่ถูกต้อง: {e}")

    # Log สิ่งที่ AI จะลบ พร้อม confidence
    print(f"\n📋 AI Suggested Cuts ({len(segments_to_delete)} segments to delete):")
    total_cut = 0.0
    for seg in segments_to_delete:
        duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
        total_cut += duration
        conf = seg.get("confidence", "?")
        conf_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(conf, "⚪")
        print(f"  {conf_icon} [{conf}] {seg.get('start')}s → {seg.get('end')}s "
              f"({duration:.1f}s) — {seg.get('reason', '')}")
    print(f"  Total to cut: {total_cut:.1f}s ({total_cut/60:.1f} min)")
    print(f"  Will keep: {total_duration - total_cut:.1f}s ({(total_duration - total_cut)/60:.1f} min)\n")

    # ── Step 5: Snap + Invert → ได้ช่วงที่ควรเก็บ ─────────────────────────────
    snapped_deletes = []
    for seg in segments_to_delete:
        start = float(seg.get("start", 0))
        end   = float(seg.get("end", 0))
        if end <= start + 5.0:  # ไม่ตัดถ้าสั้นกว่า 5 วินาที
            continue
        snapped_end = snap_to_sentence_boundary(end, transcript)
        snapped_deletes.append({"start": start, "end": snapped_end})

    # แปลง "ช่วงที่ลบ" → "ช่วงที่เก็บ"
    keep_segments = invert_segments(snapped_deletes, total_duration)

    # รวม segment ที่เก็บที่อยู่ติดกัน
    final_segments = merge_close_segments(keep_segments, gap_threshold=2.0)

    print(f"✅ Final keep segments ({len(final_segments)} total):")
    for s in final_segments:
        print(f"  {s['start']}s → {s['end']}s  ({s['end'] - s['start']:.1f}s)")

    return final_segments, transcript


# ─────────────────────────────────────────────────────────────────────────────
# TIKTOK MODE — Peak moment extraction (≤ target_length)
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_tiktok_mode(
    user_prompt: str,
    transcript_json: str,
    transcript: list[dict],
    total_duration: float,
    target_length: int,
) -> tuple[list[dict], list[dict]]:
    """ดึง peak moments จากวิดีโอ ให้รวมไม่เกิน target_length วินาที"""

    tiktok_prompt = f"""
คุณเป็น TikTok/Reels editor มืออาชีพ
งาน: เลือก segments ที่ดีที่สุดมารวมเป็นคลิปสั้น **ไม่เกิน {target_length} วินาที**

คำสั่งผู้ใช้: "{user_prompt}"
ความยาววิดีโอต้นฉบับ: {total_duration/60:.1f} นาที

Transcript (start, end, text):
{transcript_json}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
กฎสำคัญ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Segment แรก (priority=1) ต้องเป็น **hook** ที่ดึงความสนใจใน 3 วินาที (น่าตื่นเต้น/ตลก/น่าสงสัย)
2. ผลรวม (end - start) ของทุก segments **ต้องไม่เกิน {target_length} วินาที**
3. เลือก peak moment: ตลก/น่าตื่นเต้น/น่าจดจำ/educational value สูง
4. แต่ละ segment ต้อง snap ที่ประโยคจบ (ไม่ตัดกลางคำ)
5. เลือก 2-5 segments — ไม่เลือกมากเกินไป

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
รูปแบบผลลัพธ์ (JSON array เท่านั้น ห้ามมี markdown)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[
  {{"start": 12.5, "end": 25.0, "priority": 1, "reason": "hook ที่..."}},
  {{"start": 45.0, "end": 60.0, "priority": 2, "reason": "main content..."}}
]

priority: 1=hook (จำเป็น), 2=main content, 3+=supporting (ตัดทิ้งก่อนถ้าเกินเวลา)
"""

    print(f"[TIKTOK] Sending to Gemini (target: {target_length}s)...")
    response_text = call_gemini_with_retry(tiktok_prompt)

    # Parse JSON
    json_match = re.search(r'\[\s*(\{.*?\}\s*,?\s*)*\]', response_text, re.DOTALL)
    clean_text = json_match.group(0) if json_match else \
                 response_text.replace("```json", "").replace("```", "").strip()
    try:
        raw_segments = json.loads(clean_text)
    except json.JSONDecodeError as e:
        print(f"❌ TikTok JSON parse error: {e}\nRaw:\n{response_text}")
        raise Exception(f"Gemini คืนค่า JSON ไม่ถูกต้อง: {e}")

    # Normalize + sort by priority (priority 1 first), then snap to sentence
    normalized = []
    for seg in raw_segments:
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        if end <= start:
            continue
        snapped_end = snap_to_sentence_boundary(end, transcript)
        normalized.append({
            "start": round(start, 2),
            "end": round(snapped_end, 2),
            "priority": int(seg.get("priority", 99)),
            "reason": seg.get("reason", ""),
        })

    # Sort by priority ascending (priority 1 = most important = keep first)
    normalized.sort(key=lambda x: x["priority"])

    # Drop low-priority segments until total ≤ target_length
    kept = []
    total = 0.0
    for seg in normalized:
        dur = seg["end"] - seg["start"]
        if total + dur <= target_length:
            kept.append(seg)
            total += dur
        else:
            # Try to truncate last segment to fit
            remaining = target_length - total
            if remaining >= 3.0 and seg["priority"] <= 2:  # ต้องเหลือ ≥3s
                seg["end"] = round(seg["start"] + remaining, 2)
                kept.append(seg)
                total += remaining
            break

    # Sort by chronological order for FFmpeg
    kept.sort(key=lambda x: x["start"])

    # Strip metadata, keep only start/end
    final = [{"start": s["start"], "end": s["end"]} for s in kept]

    print(f"✅ [TIKTOK] Final segments ({len(final)}, total {total:.1f}s):")
    for s in kept:
        print(f"  p{s['priority']} {s['start']}s → {s['end']}s  ({s['end']-s['start']:.1f}s) — {s['reason'][:60]}")

    return final, transcript