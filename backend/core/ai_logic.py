from google import genai
from google.genai import errors as genai_errors
import json
import os
import time
import re
import hashlib
from faster_whisper import WhisperModel, BatchedInferencePipeline
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
_whisper_batched = None

# batch_size ผ่าน env (default = 4 สำหรับ RTX 3050 6GB)
WHISPER_BATCH_SIZE = int(os.getenv("WHISPER_BATCH_SIZE", "4"))


def get_whisper_model():
    """โหลด underlying WhisperModel (ใช้กับ gap-fill ที่ต้อง clip_timestamps)"""
    global _whisper_model
    if _whisper_model is None:
        import torch
        if torch.cuda.is_available():
            device, compute_type = "cuda", "float16"
            print(f"Loading Whisper on GPU: {torch.cuda.get_device_name(0)}")
        else:
            device, compute_type = "cpu", "int8"
            print("Loading Whisper on CPU (no CUDA detected)")
        # medium (1.5GB, ~3.5GB VRAM) → แม่นกว่า small ~10-15% สำหรับไทย
        # RTX 3050 6GB รับได้ + Silero VAD ~200MB
        model_size = "medium" if device == "cuda" else "small"
        try:
            _whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
            print(f"Whisper model loaded ({model_size}/{device}/{compute_type}).")
        except Exception as e:
            _whisper_model = None
            raise Exception(f"Whisper model load failed: {e}")
    return _whisper_model


def get_batched_pipeline():
    """
    Wrap WhisperModel ด้วย BatchedInferencePipeline → 3-4x เร็วขึ้น (zero accuracy loss)
    ใช้สำหรับ Pass 1 (full audio transcribe)
    """
    global _whisper_batched
    if _whisper_batched is None:
        model = get_whisper_model()
        _whisper_batched = BatchedInferencePipeline(model=model)
        print(f"BatchedInferencePipeline ready (batch_size={WHISPER_BATCH_SIZE})")
    return _whisper_batched


def reset_whisper_model():
    """ใช้ตอน error เพื่อบังคับโหลดใหม่ครั้งหน้า"""
    global _whisper_model, _whisper_batched
    _whisper_model = None
    _whisper_batched = None


# ─────────────────────────────────────────────────────────────────────────────
# Audio hash cache — อัปวิดีโอเดิม → skip Whisper + AI (instant)
# ─────────────────────────────────────────────────────────────────────────────
TRANSCRIPT_CACHE_DIR = os.getenv("TRANSCRIPT_CACHE_DIR", "/app/transcript_cache")


def audio_file_hash(path: str) -> str:
    """SHA-256 ของ audio file → cache key"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _cache_path(hash_id: str, suffix: str = "transcript") -> str:
    return os.path.join(TRANSCRIPT_CACHE_DIR, f"{suffix}_{hash_id}.json")


def load_cached_transcript(audio_path: str) -> tuple[list[dict] | None, str | None]:
    """Return (transcript, hash) — transcript=None ถ้า miss"""
    try:
        hash_id = audio_file_hash(audio_path)
        cache_file = _cache_path(hash_id)
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"⚡ [Cache HIT] transcript loaded for hash={hash_id} ({len(data)} segments)")
            return data, hash_id
        return None, hash_id
    except Exception as e:
        print(f"[Cache] read failed: {e}")
        return None, None


def save_transcript_cache(hash_id: str, transcript: list[dict]) -> None:
    if not hash_id:
        return
    try:
        os.makedirs(TRANSCRIPT_CACHE_DIR, exist_ok=True)
        cache_file = _cache_path(hash_id)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False)
        print(f"💾 [Cache] saved transcript hash={hash_id} ({len(transcript)} segments)")
    except Exception as e:
        print(f"[Cache] write failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Skip AI correction ถ้า transcript ไม่มี Latin → ไม่ต้องเรียก Gemini (-3 min)
# ─────────────────────────────────────────────────────────────────────────────
_LATIN_RE = re.compile(r"[a-zA-Z]{2,}")


def needs_ai_correction(transcript: list[dict]) -> bool:
    """True ถ้ามี Latin chars ≥2 ตัวอย่างน้อย 1 segment → ต้องเรียก AI แก้/แปล"""
    if not transcript:
        return False
    for s in transcript:
        if _LATIN_RE.search(s.get("text", "")):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Topic-aware initial_prompt — ช่วย Whisper รู้บริบทคำศัพท์
# ─────────────────────────────────────────────────────────────────────────────
TOPIC_PROMPTS = {
    "silence":  "สวัสดีครับ ยินดีต้อนรับ",
    "essence":  "ขอบคุณครับ หลักการ แนวคิด ตัวอย่าง บทสรุป",
    "shortest": "สรุป ประเด็นสำคัญ key point",
    "tutorial": "บทเรียน สอน Python JavaScript React code function class library tutorial",
    "podcast":  "พูดคุย สัมภาษณ์ guest host podcast Andrew Huberman Dr.",
    "meeting":  "ประชุม action item deadline decision project KPI",
    "gaming":   "เกม level boss kill win react milestone gameplay streaming",
    "tiktok":   "สวัสดี hook น่าสนใจ TikTok Reels short",
    "custom":   "สวัสดี ขอบคุณ Python AI machine learning",
}

# fallback prompt — รวมศัพท์ทั่วไปที่อาจปรากฏในทุกวิดีโอ
DEFAULT_INITIAL_PROMPT = (
    "สวัสดีครับ ยินดีต้อนรับ ขอบคุณ "
    "AI machine learning Python JavaScript React Node "
    "Andrew Huberman podcast tutorial รีวิว"
)


def get_initial_prompt(preset_id: str | None) -> str:
    if not preset_id:
        return DEFAULT_INITIAL_PROMPT
    return TOPIC_PROMPTS.get(preset_id, DEFAULT_INITIAL_PROMPT)


# ─────────────────────────────────────────────────────────────────────────────
# Whisper transcribe + gap-fill (กันสลับภาษา Thai/English ทำ Whisper ข้าม)
# ─────────────────────────────────────────────────────────────────────────────
GAP_RETRANSCRIBE_THRESHOLD = 5.0   # gap > 5s ใน transcript → ลอง re-transcribe
MIN_RETRANSCRIBE_DUR = 1.5         # gap สั้นกว่านี้ไม่คุ้ม retranscribe


def _whisper_segments_to_dicts(segments) -> list[dict]:
    """Convert faster-whisper segment objects → JSON-friendly dicts."""
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
            "text":  (seg.text or "").strip(),
            "words": words_data,
        })
    return transcript


def _retranscribe_gap(audio_path: str, model, gap_start: float, gap_end: float) -> list[dict]:
    """
    Re-transcribe เฉพาะช่วง gap — ไม่ใช้ initial_prompt + condition_on_previous=False
    เพื่อให้ Whisper ไม่ติด context Thai → ตรวจจับ English embedded ได้
    """
    try:
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            language=None,                       # auto-detect ต่อ chunk
            word_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=None,
            clip_timestamps=[gap_start, gap_end],
        )
        gap_segs = _whisper_segments_to_dicts(segments)
        if gap_segs:
            print(f"   [gap-fill] {gap_start:.1f}s–{gap_end:.1f}s → recovered {len(gap_segs)} segs ({info.language})")
        return gap_segs
    except Exception as e:
        print(f"   [gap-fill] {gap_start:.1f}s–{gap_end:.1f}s failed: {e}")
        return []


def transcribe_audio(audio_path: str, initial_prompt: str | None = None,
                     audio_duration: float | None = None,
                     use_cache: bool = True) -> list[dict]:
    """
    แปลงเสียงเป็น transcript พร้อม timestamp ระดับประโยค
    Pass 0: Cache check — hit → return ทันที (0s)
    Pass 1: Whisper หลัก (BatchedInferencePipeline → 3-4x เร็ว)
    Pass 2: หา gap > 5s แล้ว re-transcribe เพื่อจับ English/silence segments ที่ pass 1 ข้าม
    """
    # Pass 0: cache check
    cache_hash = None
    if use_cache:
        cached, cache_hash = load_cached_transcript(audio_path)
        if cached is not None:
            return cached

    prompt = initial_prompt or DEFAULT_INITIAL_PROMPT
    print(f"Transcribing: {audio_path}")
    print(f"   Initial prompt: {prompt[:80]}...")

    # Pass 1: main transcription (BATCHED — 3-4x faster on GPU)
    pipeline = get_batched_pipeline()
    segments, info = pipeline.transcribe(
        audio_path,
        batch_size=WHISPER_BATCH_SIZE,        # ⚡ parallel chunks
        beam_size=5,
        language=None,
        word_timestamps=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,    # กัน language drift Thai→English ข้าม
        vad_filter=True,                      # built-in VAD ช่วย boundary
        vad_parameters={"min_silence_duration_ms": 500},
    )
    print(f"Detected language: {info.language} (confidence: {info.language_probability:.2f})")

    transcript = _whisper_segments_to_dicts(segments)
    print(f"Pass 1 (batched x{WHISPER_BATCH_SIZE}): {len(transcript)} segments")

    # ใช้ duration จาก Whisper info ถ้า caller ไม่ส่งมา
    if audio_duration is None:
        audio_duration = getattr(info, "duration", None)

    # Pass 2: gap-fill — re-transcribe ช่วงที่ Pass 1 ข้าม
    if transcript and audio_duration:
        gaps_to_fill: list[tuple[float, float]] = []

        # Gap ก่อน segment แรก
        first_start = transcript[0]["start"]
        if first_start >= GAP_RETRANSCRIBE_THRESHOLD:
            gaps_to_fill.append((0.0, first_start - 0.1))

        # Gap ระหว่าง segments
        for i in range(1, len(transcript)):
            prev_end = transcript[i - 1]["end"]
            curr_start = transcript[i]["start"]
            gap_dur = curr_start - prev_end
            if gap_dur > GAP_RETRANSCRIBE_THRESHOLD:
                gaps_to_fill.append((prev_end + 0.1, curr_start - 0.1))

        # Gap หลัง segment สุดท้าย
        last_end = transcript[-1]["end"]
        if audio_duration - last_end >= GAP_RETRANSCRIBE_THRESHOLD:
            gaps_to_fill.append((last_end + 0.1, audio_duration - 0.1))

        if gaps_to_fill:
            print(f"Pass 2: re-transcribing {len(gaps_to_fill)} gap(s)...")
            extra: list[dict] = []
            # gap-fill ใช้ underlying WhisperModel (รองรับ clip_timestamps)
            underlying = get_whisper_model()
            for gs, ge in gaps_to_fill:
                if ge - gs < MIN_RETRANSCRIBE_DUR:
                    continue
                extra.extend(_retranscribe_gap(audio_path, underlying, gs, ge))
            if extra:
                # Merge + sort by start
                transcript = sorted(transcript + extra, key=lambda s: s["start"])
                print(f"Pass 2: total {len(transcript)} segments after gap-fill")

    print(f"Transcribed {len(transcript)} segments "
          f"({sum(len(s['words']) for s in transcript)} word tokens).")

    # Save to cache (สำหรับ run ครั้งต่อไป)
    if use_cache and cache_hash:
        save_transcript_cache(cache_hash, transcript)

    return transcript


# ─────────────────────────────────────────────────────────────────────────────
# AI Post-correction — ให้ Gemini แก้ transcript ก่อนใช้ต่อ
# ─────────────────────────────────────────────────────────────────────────────

def correct_transcript_with_ai(transcript: list[dict], user_prompt: str = "") -> list[dict]:
    """
    ส่ง transcript ให้ Gemini แก้:
    - ชื่อเฉพาะภาษาอังกฤษ (Andrew Huberman, Python, React) → คงเป็นอังกฤษ
    - ศัพท์เฉพาะที่ Whisper ฟังผิด
    - สะกดผิดทั่วไป

    คืน transcript รูปแบบเดิม (เก็บ start/end/words) แต่ text ถูกแก้แล้ว
    """
    if not transcript:
        return transcript

    # รวม text ทั้งหมดเป็น list ที่มี index
    items = [{"i": i, "t": (s.get("text") or "").strip()} for i, s in enumerate(transcript)]
    items_json = json.dumps([{"i": x["i"], "t": x["t"]} for x in items], ensure_ascii=False)

    correction_prompt = f"""
คุณคือผู้ตรวจแก้ + แปล transcript ภาษาไทย
หน้าที่: แก้ไขข้อความที่ Whisper ฟังผิด + แปลประโยคภาษาอังกฤษ embed เป็นไทย

กฎสำคัญ:
1. **ชื่อเฉพาะ / Proper nouns** (เช่น Warren Buffett, Bill Gates, Andrew Huberman, Python, React, Dr., GDP, AI, CEO, Apple, Google) → **คงเป็นอังกฤษ**
2. **ประโยค/วลีภาษาอังกฤษเต็มประโยค** ที่ embed ในเนื้อหา → **แปลเป็นไทย**
   ตัวอย่าง:
     - "เขาบอกว่า If you're picking associates pick out those better than you"
       → "เขาบอกว่า ถ้าคุณจะเลือกคนคบ ให้เลือกคนที่ดีกว่าคุณ"
     - "เด็กถามว่า It's better to hang out with people better than you"
       → "เด็กถามว่า ดีกว่าที่จะใช้เวลากับคนที่เก่งกว่าคุณ"
3. **ศัพท์เทคนิค Latin สั้น ๆ** (เช่น Circle of Competence, Say No, machine learning) → **คงไว้** (อาจใส่ Thai ในวงเล็บถ้าช่วยความเข้าใจ)
4. ศัพท์เฉพาะที่ Whisper ฟังผิด → แก้เป็นคำที่ถูกต้อง
5. ห้ามเปลี่ยนความหมาย ห้ามเพิ่ม/ลด ใจความสำคัญ
6. คงโครงสร้างเดิม — แก้เฉพาะที่จำเป็น

บริบทวิดีโอ: "{user_prompt[:200]}"

Input (JSON array — แต่ละ item มี i=index, t=text):
{items_json}

Output: JSON array เดียวกัน รูปแบบเดียวกัน — เปลี่ยนเฉพาะ field "t"
ห้ามมี markdown ห้ามมีข้อความอื่น
[
  {{"i": 0, "t": "ข้อความที่แก้แล้ว/แปลแล้ว"}},
  ...
]
"""

    try:
        print(f"🔧 [AI-Correct] Sending {len(transcript)} segments to Gemini for correction...")
        response = call_gemini_with_retry(correction_prompt)

        # Parse JSON
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
        clean = json_match.group(0) if json_match else \
                response.replace("```json", "").replace("```", "").strip()
        corrected = json.loads(clean)

        # Map index → corrected text
        fixed_map = {int(c["i"]): str(c.get("t", "")).strip() for c in corrected if "i" in c}

        # Apply corrections
        result = []
        n_fixed = 0
        for i, seg in enumerate(transcript):
            new_text = fixed_map.get(i, seg.get("text", ""))
            if new_text and new_text != seg.get("text"):
                n_fixed += 1
            result.append({**seg, "text": new_text})

        print(f"✅ [AI-Correct] Fixed {n_fixed}/{len(transcript)} segments")
        return result

    except Exception as e:
        # ถ้า AI correction fail → ใช้ของเดิม (ไม่ทำให้ pipeline พัง)
        print(f"⚠️ [AI-Correct] failed: {e} — using original transcript")
        return transcript


# ─────────────────────────────────────────────────────────────────────────────
# Parallel AI Correction — split transcript ข้าม API keys (2x เร็ว)
# ─────────────────────────────────────────────────────────────────────────────
def _build_correction_prompt(items_json: str, user_prompt: str) -> str:
    """Build correction prompt (เหมือนกันกับ correct_transcript_with_ai)"""
    return f"""
คุณคือผู้ตรวจแก้ + แปล transcript ภาษาไทย
หน้าที่: แก้ไขข้อความที่ Whisper ฟังผิด + แปลประโยคภาษาอังกฤษ embed เป็นไทย

กฎสำคัญ:
1. **ชื่อเฉพาะ / Proper nouns** (เช่น Warren Buffett, Bill Gates, Andrew Huberman, Python, React, Dr., GDP, AI, CEO) → **คงเป็นอังกฤษ**
2. **ประโยค/วลีภาษาอังกฤษเต็มประโยค** ที่ embed ในเนื้อหา → **แปลเป็นไทย**
3. **ศัพท์เทคนิค Latin สั้น ๆ** (Circle of Competence, machine learning) → คงไว้
4. ศัพท์เฉพาะที่ Whisper ฟังผิด → แก้
5. ห้ามเปลี่ยนความหมาย ห้ามเพิ่ม/ลด ข้อความ
6. คงโครงสร้างเดิม

บริบทวิดีโอ: "{user_prompt[:200]}"

Input (JSON array — แต่ละ item มี i=index, t=text):
{items_json}

Output: JSON array เดียวกัน — เปลี่ยนเฉพาะ field "t"
ห้ามมี markdown ห้ามมีข้อความอื่น
[{{"i": 0, "t": "..."}}, ...]
"""


def call_gemini_with_specific_key(full_prompt: str, key_idx: int,
                                   max_attempts_per_model: int = 2) -> str:
    """
    เรียก Gemini ด้วย key ที่ระบุ (สำหรับ parallel calls)
    ถ้า key นี้ fail → raise (caller จัดการ fallback เอง)
    """
    if key_idx < 0 or key_idx >= len(API_KEYS):
        raise Exception(f"key_idx {key_idx} out of range (have {len(API_KEYS)} keys)")

    api_key = API_KEYS[key_idx]
    client = get_client(api_key)
    key_label = f"key#{key_idx+1}/{len(API_KEYS)}"

    last_error = None
    for model_name in FALLBACK_MODELS:
        for attempt in range(max_attempts_per_model):
            try:
                print(f"🚀 [{key_label}] {model_name} att {attempt+1}/{max_attempts_per_model}")
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                )
                return response.text
            except genai_errors.ServerError as e:
                last_error = e
                if "503" in str(e) and attempt < max_attempts_per_model - 1:
                    time.sleep(10 * (attempt + 1))
                else:
                    break
            except genai_errors.ClientError as e:
                last_error = e
                break
            except Exception as e:
                last_error = e
                break

    raise Exception(f"{key_label} exhausted: {last_error}")


def _correct_chunk(chunk: list[dict], user_prompt: str, key_idx: int) -> list[dict]:
    """แก้ chunk ของ transcript ด้วย Gemini key เฉพาะ (worker function)"""
    items_json = json.dumps(
        [{"i": i, "t": (s.get("text") or "").strip()} for i, s in enumerate(chunk)],
        ensure_ascii=False,
    )
    prompt = _build_correction_prompt(items_json, user_prompt)
    try:
        response = call_gemini_with_specific_key(prompt, key_idx)
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
        clean = json_match.group(0) if json_match else \
                response.replace("```json", "").replace("```", "").strip()
        corrected = json.loads(clean)
        fixed_map = {int(c["i"]): str(c.get("t", "")).strip() for c in corrected if "i" in c}
        result = []
        n_fixed = 0
        for i, seg in enumerate(chunk):
            new_text = fixed_map.get(i, seg.get("text", ""))
            if new_text and new_text != seg.get("text"):
                n_fixed += 1
            result.append({**seg, "text": new_text})
        print(f"   ✅ chunk@key#{key_idx+1}: {n_fixed}/{len(chunk)} fixed")
        return result
    except Exception as e:
        print(f"   ⚠️ chunk@key#{key_idx+1} fail: {e} → fallback to original")
        return [dict(s) for s in chunk]


def correct_transcript_with_ai_parallel(transcript: list[dict],
                                         user_prompt: str = "") -> list[dict]:
    """
    Parallel version — แบ่ง transcript เป็น N chunks ตามจำนวน keys → ส่งพร้อมกัน
    Fallback: ถ้า keys < 2 หรือ transcript เล็ก → ใช้ sequential
    """
    if not transcript:
        return transcript

    n_workers = min(len(API_KEYS), 2)   # parallel ได้สูงสุด 2 keys
    if n_workers < 2 or len(transcript) < 6:
        # ไม่คุ้มแบ่ง → ใช้ sequential เดิม
        return correct_transcript_with_ai(transcript, user_prompt)

    # Split transcript เป็น N chunks เท่า ๆ กัน
    chunk_size = (len(transcript) + n_workers - 1) // n_workers
    chunks = [transcript[i:i + chunk_size] for i in range(0, len(transcript), chunk_size)]

    print(f"🔧 [AI-Correct Parallel] Split {len(transcript)} segments → "
          f"{len(chunks)} chunks (1 per key)")

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        futures = [
            ex.submit(_correct_chunk, chunk, user_prompt, i)
            for i, chunk in enumerate(chunks)
        ]
        results = [f.result() for f in futures]

    merged = []
    for r in results:
        merged.extend(r)

    print(f"✅ [AI-Correct Parallel] Merged {len(merged)} segments from "
          f"{len(chunks)} parallel workers")
    return merged


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

    # sorted() แทน .sort() เพื่อไม่ mutate list ของ caller
    segments = sorted(segments, key=lambda x: x['start'])
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
    preset_id: str | None = None,        # ← topic-aware initial_prompt
    ai_correct: bool = True,             # ← AI post-correction toggle
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

    # ── Step 1: Transcribe (พร้อม topic-aware initial_prompt) ─────────────────
    try:
        initial_prompt = get_initial_prompt(preset_id)
        transcript = transcribe_audio(audio_path, initial_prompt=initial_prompt)
    except Exception as e:
        # Reset model cache กัน corrupt — ครั้งหน้าจะโหลดใหม่
        reset_whisper_model()
        raise Exception(f"Transcription failed: {e}")

    if not transcript:
        raise Exception("Whisper ไม่สามารถถอดเสียงได้ — ตรวจสอบไฟล์เสียง")

    # NOTE: AI correction ย้ายไปทำ "parallel" กับ Deletion mode ด้านล่าง (ประหยัด ~1.5 min)

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
[ตัวอย่างการตัดที่ดี]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ตัวอย่างที่ 1 — Filler ตอนต้น:
  Transcript: "อืม... เอ่อ... ใช่ ตอนนี้เรามาเรียน Python list กัน"
  Output: [{{"start": 0.0, "end": 3.5, "reason": "filler 'อืม เอ่อ ใช่' ก่อนเข้าเรื่องจริง", "confidence": "high"}}]

ตัวอย่างที่ 2 — เนื้อหาสำคัญ:
  Transcript: "ขั้นแรกเรา import library, ขั้นสองสร้าง list, ขั้นสาม append ค่า"
  Output: []   # ❌ ไม่ตัดเลย — เนื้อหา core content

ตัวอย่างที่ 3 — Off-topic tangent:
  Transcript: "...กลับมาที่หัวข้อ Python นะครับ จริง ๆ เมื่อวานผมไปกินข้าวกับเพื่อน เพื่อนผมเล่าเรื่อง... (5 นาที) ...เอาล่ะ กลับมาที่ list"
  Output: [{{"start": 120.0, "end": 420.0, "reason": "STORY_TANGENT เรื่องกินข้าวเพื่อน ไม่เกี่ยวกับ Python", "confidence": "high"}}]

ตัวอย่างที่ 4 — Repetition:
  Transcript: "...append คือเพิ่มข้อมูลท้าย list... เอ๊ะ ที่ผมพูดเมื่อกี้ ก็คือ append เพิ่มข้อมูลท้าย list นั่นแหละ"
  Output: [{{"start": 180.0, "end": 195.0, "reason": "REPETITION พูดซ้ำเรื่อง append", "confidence": "medium"}}]

ตัวอย่างที่ 5 — Technical issue:
  Transcript: "เสียงหายไหม ได้ยินไหม... รอแป๊บนึง... โอเค ได้แล้ว"
  Output: [{{"start": 45.0, "end": 60.0, "reason": "ปัญหาเทคนิคเสียง", "confidence": "high"}}]

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

    # ─────────────────────────────────────────────────────────────────────────
    # ⚡ Pipeline Parallelism: AI Correction + Deletion mode ทำพร้อมกัน
    #    - Correction: ทำงานบน transcript เต็ม (แก้ text)
    #    - Deletion:   ทำงานบน filtered_transcript (เลือกช่วงลบ — timestamp-based)
    #    ทั้งคู่ไม่พึ่งกัน → ทำ parallel ได้ ประหยัด ~1.5 min
    # ─────────────────────────────────────────────────────────────────────────
    import concurrent.futures as _futures

    def _run_correction():
        if not ai_correct:
            return transcript
        if not needs_ai_correction(transcript):
            print("⚡ [AI-Correct] Skipped — pure Thai transcript (no Latin chars)")
            return transcript
        # ถ้ามี ≥2 keys → parallel split ภายในด้วย (2 levels of parallelism!)
        return correct_transcript_with_ai_parallel(transcript, user_prompt)

    def _run_deletion():
        print("Sending filtered transcript to Gemini (Deletion mode)...")
        return call_gemini_with_retry(full_prompt)

    print(f"⚡ [Pipeline] Running AI Correction + Deletion in parallel...")
    with _futures.ThreadPoolExecutor(max_workers=2) as _ex:
        _correction_future = _ex.submit(_run_correction)
        _deletion_future = _ex.submit(_run_deletion)
        # Wait both
        try:
            corrected_transcript = _correction_future.result()
        except Exception as e:
            print(f"⚠️ Correction failed: {e} — use original")
            corrected_transcript = transcript
        response_text = _deletion_future.result()

    # Apply corrected text back to transcript (by index, timestamps unchanged)
    if corrected_transcript and len(corrected_transcript) == len(transcript):
        for i in range(len(transcript)):
            transcript[i] = {**transcript[i], "text": corrected_transcript[i].get("text", transcript[i].get("text", ""))}
        print(f"✅ Corrected text applied to {len(transcript)} segments")

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
        snapped_deletes.append({
            "start": start,
            "end": snapped_end,
            "reason": seg.get("reason", ""),
            "confidence": seg.get("confidence", "medium"),
        })

    # แปลง "ช่วงที่ลบ" → "ช่วงที่เก็บ"
    keep_segments = invert_segments(
        [{"start": s["start"], "end": s["end"]} for s in snapped_deletes],
        total_duration,
    )

    # รวม segment ที่เก็บที่อยู่ติดกัน
    final_segments = merge_close_segments(keep_segments, gap_threshold=2.0)

    # ── Enrich each keep_segment ด้วย text content จาก transcript (สำหรับ Preview) ──
    final_segments = _enrich_segments_with_text(final_segments, transcript)

    print(f"✅ Final keep segments ({len(final_segments)} total):")
    for s in final_segments:
        print(f"  {s['start']}s → {s['end']}s  ({s['end'] - s['start']:.1f}s)")

    return final_segments, transcript


def _enrich_segments_with_text(segments: list[dict], transcript: list[dict],
                                max_chars: int = 200) -> list[dict]:
    """แนบ text จาก transcript เข้าไปในแต่ละ segment (สำหรับ Preview UI)"""
    enriched = []
    for seg in segments:
        start = seg["start"]
        end = seg["end"]
        parts = []
        for t in transcript:
            t_mid = (t.get("start", 0) + t.get("end", 0)) / 2
            if start <= t_mid <= end:
                parts.append(t.get("text", "").strip())
        joined = " ".join(p for p in parts if p)
        if len(joined) > max_chars:
            joined = joined[:max_chars] + "..."
        enriched.append({**seg, "text": joined})
    return enriched


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
ตัวอย่าง Hook ที่ดี (Priority 1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ ห้ามใช้เป็น hook: "วันนี้ผมจะมาเล่าให้ฟัง...", "สวัสดีครับ...", "เอ่อ..."
✅ ใช้เป็น hook: "คุณรู้ไหมว่า X ทำได้ Y!", "นี่คือเหตุผลที่...", "ห้ามทำสิ่งนี้!", "ผมประหยัดได้ 50,000 ใน 1 เดือน"

ตัวอย่างผลลัพธ์ที่ดี:
[
  {{"start": 125.0, "end": 130.0, "priority": 1, "reason": "Hook 'ห้ามทำสิ่งนี้!' ดึงสนใจทันที"}},
  {{"start": 245.0, "end": 268.0, "priority": 2, "reason": "อธิบายเหตุผลหลัก พร้อมตัวอย่าง"}},
  {{"start": 410.0, "end": 425.0, "priority": 3, "reason": "Call-to-action ตอนจบ"}}
]
(รวม 43 วินาที — พอดี hook + main + outro)

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

    # เก็บ metadata (priority/reason) สำหรับ Preview UI
    final = [
        {
            "start": s["start"],
            "end": s["end"],
            "priority": s.get("priority"),
            "reason": s.get("reason", ""),
        }
        for s in kept
    ]
    final = _enrich_segments_with_text(final, transcript)

    print(f"✅ [TIKTOK] Final segments ({len(final)}, total {total:.1f}s):")
    for s in kept:
        print(f"  p{s['priority']} {s['start']}s → {s['end']}s  ({s['end']-s['start']:.1f}s) — {s['reason'][:60]}")

    return final, transcript