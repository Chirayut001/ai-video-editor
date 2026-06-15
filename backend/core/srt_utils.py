"""
สร้าง SRT subtitle file จาก Whisper transcript โดย:
1. Merge sub-syllable tokens (Thai dependent marks) ให้กลายเป็น syllable เต็ม
2. แบ่ง subtitle เป็น phrase สั้น ๆ ตาม word-level timestamps
3. Remap timestamps ให้ตรงกับ video output หลังการ cut + concat
4. รับประกัน phrase ไม่ซ้อนกัน + มี gap เล็กน้อยให้แต่ละ phrase หายไปก่อนตัวถัดไป
"""

import unicodedata

try:
    from pythainlp.tokenize import word_tokenize as _thai_word_tokenize
    _HAS_PYTHAINLP = True
except ImportError:
    _HAS_PYTHAINLP = False

# Soft limit: พยายามไม่เกินค่านี้ ถ้าเจอ pause/punctuation
SOFT_MAX_CHARS = 12
# Hard limit: ถึงไม่มี pause ก็ต้องตัด (กันยาวเกิน)
HARD_MAX_CHARS = 24
# Max duration ของ 1 phrase (วินาที) — กันค้างเกินเวลา
MAX_PHRASE_DURATION = 1.8
# pause threshold (วินาที) — gap เล็กน้อยก็ถือว่าตัดได้
PAUSE_THRESHOLD = 0.05
# punctuation ที่จบประโยค → cut ทันที
SENTENCE_END_CHARS = ".!?,。！？"
# เว้นช่วงเล็ก ๆ ระหว่าง phrase (วินาที)
PHRASE_GAP = 0.02
# Min duration ของ subtitle entry — ต่ำกว่านี้ถูกตัดทิ้ง (กัน flash เกินไป)
MIN_PHRASE_DURATION = 0.05


def _format_srt_timestamp(seconds: float) -> str:
    """แปลงวินาที (float) → SRT format HH:MM:SS,mmm"""
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _segment_midpoint_in_keep(seg_start: float, seg_end: float,
                              keep_segments: list[dict]) -> dict | None:
    """หา keep_segment ที่ midpoint ของ transcript segment ตกอยู่ภายใน"""
    midpoint = (seg_start + seg_end) / 2
    for k in keep_segments:
        if k["start"] <= midpoint <= k["end"]:
            return k
    return None


def _is_thai_char(ch: str) -> bool:
    """เช็คว่าตัวอักษรอยู่ใน Unicode block ของไทย"""
    return "฀" <= ch <= "๿"


def _is_dependent_mark(ch: str) -> bool:
    """
    True ถ้าตัวอักษรเป็น "dependent mark" ที่ต้องมีพยัญชนะนำหน้า
    เช่น Thai vowels ิ ี ึ ื ุ ู, tone marks ่ ้ ๊ ๋, marks ์ ํ
    (Unicode category Mn=Mark Nonspacing, Mc=Mark Spacing Combining)
    """
    if not ch:
        return False
    return unicodedata.category(ch)[0] == "M"


# Thai vowels ที่ต้องตามหลังพยัญชนะ (independent ใน Unicode แต่ปฏิบัติเป็น dependent)
_THAI_TRAILING_VOWELS = set("ะาำๅ")
# Thai leading vowels (เขียนก่อนพยัญชนะ) — ปลอดภัยที่จะเริ่ม syllable ใหม่
_THAI_LEADING_VOWELS = set("เแโใไ")


def _is_safe_break_before(text: str, pos: int) -> bool:
    """
    True ถ้าตัด text หน้าตำแหน่ง pos ปลอดภัย (ไม่แยกกลาง syllable Thai)
    - ห้ามตัดก่อน dependent mark (สระบน/ล่าง/วรรณยุกต์)
    - ห้ามตัดก่อน trailing vowel (ะ า ำ ๅ)
    - ตัดก่อนพยัญชนะ/leading vowel/whitespace/อักขระอื่น = OK
    """
    if pos <= 0 or pos >= len(text):
        return True
    ch = text[pos]
    if _is_dependent_mark(ch):
        return False
    if ch in _THAI_TRAILING_VOWELS:
        return False
    return True


def _split_text_at_safe_position(text: str, target_pos: int) -> int:
    """
    หา safe break position ใกล้ ๆ target_pos
    Scan backward จาก target_pos จนเจอจุดตัดปลอดภัย
    """
    if target_pos >= len(text):
        return len(text)
    pos = target_pos
    while pos > 1 and not _is_safe_break_before(text, pos):
        pos -= 1
    return max(pos, 1)


def _tokenize_thai_aware(text: str) -> list[str]:
    """
    แบ่งข้อความเป็น "words" โดย:
    - ถ้ามี PyThaiNLP → ใช้ word_tokenize (newmm) สำหรับ Thai → ได้ word ที่ถูกต้อง
    - ถ้าไม่มี → fallback ใช้ safe-break split (character level)
    """
    if _HAS_PYTHAINLP:
        # newmm = Maximum Matching + TCC algorithm — Thai word tokenizer มาตรฐาน
        return [w for w in _thai_word_tokenize(text, engine="newmm", keep_whitespace=False) if w.strip()]
    # Fallback: split by whitespace แบบง่าย
    return [w for w in text.split() if w.strip()]


def _split_segment_text(text: str, seg_start: float, seg_end: float,
                         max_chars: int = HARD_MAX_CHARS,
                         max_dur: float = MAX_PHRASE_DURATION) -> list[dict]:
    """
    แบ่ง segment text เป็น chunks โดย:
    1. Tokenize เป็น words (PyThaiNLP สำหรับไทย, whitespace สำหรับ Latin)
    2. รวม words ใน chunk จนเกิน max_chars หรือ max_dur
    3. กระจายเวลาตามจำนวน chars
    """
    text = text.strip()
    if not text:
        return []
    duration = max(0.01, seg_end - seg_start)

    words = _tokenize_thai_aware(text)
    if not words:
        return [{"start": seg_start, "end": seg_end, "text": text}]

    # กลุ่ม words เป็น chunks ตาม max_chars
    chunks_text: list[str] = []
    bucket = ""
    for w in words:
        candidate = _join_token(bucket, w)
        if len(candidate) > max_chars and bucket:
            chunks_text.append(bucket)
            bucket = w
        else:
            bucket = candidate
    if bucket:
        chunks_text.append(bucket)

    # ถ้า duration ยาวเกินไป → แบ่งเพิ่ม (กัน subtitle ค้างยาวเกินไป)
    n_by_dur = max(1, int(duration / max_dur + 0.5))
    if len(chunks_text) < n_by_dur:
        # รวม chunks_text เป็นข้อความเดียวแล้วแบ่งใหม่
        combined = "".join(chunks_text) if all(_is_thai_char(c[-1]) and _is_thai_char(c[0]) for c in chunks_text if c) else " ".join(chunks_text)
        avg = len(combined) // n_by_dur
        chunks_text = []
        bucket = ""
        for w in words:
            candidate = _join_token(bucket, w)
            if len(candidate) > avg and bucket:
                chunks_text.append(bucket)
                bucket = w
            else:
                bucket = candidate
        if bucket:
            chunks_text.append(bucket)

    # กระจายเวลาตาม char fraction
    chunks = []
    total_chars = sum(len(c) for c in chunks_text)
    if total_chars == 0:
        return [{"start": seg_start, "end": seg_end, "text": text}]
    char_pos = 0
    for c in chunks_text:
        frac_start = char_pos / total_chars
        char_pos += len(c)
        frac_end = char_pos / total_chars
        chunks.append({
            "start": seg_start + frac_start * duration,
            "end":   seg_start + frac_end * duration,
            "text":  c.strip(),
        })
    return chunks


def _merge_dependent_marks(words: list[dict]) -> list[dict]:
    """
    รวม token ที่เริ่มด้วย dependent mark เข้ากับ token ก่อนหน้า
    เช่น Whisper อาจส่ง: [{text:"ค"}, {text:"ิด"}] → รวมเป็น [{text:"คิด"}]
    เพื่อกัน vowel/tone mark ลอยหลังการตัด phrase
    """
    if not words:
        return []
    merged: list[dict] = [dict(words[0])]
    for w in words[1:]:
        token = (w.get("text") or "")
        if token and _is_dependent_mark(token[:1]) and merged:
            prev = merged[-1]
            prev["text"] = prev["text"] + token
            prev["end"] = w["end"]
        else:
            merged.append(dict(w))
    return merged


def _join_token(existing: str, token: str) -> str:
    """รวม token เข้ากับ string เดิม โดย:
    - Thai → ไม่ใส่ space
    - Latin/อื่น ๆ → ใส่ space กั้น
    """
    if not existing:
        return token
    last = existing[-1]
    first = token[:1]
    if _is_thai_char(last) and _is_thai_char(first):
        return existing + token
    return existing + " " + token


def _split_words_into_phrases(words: list[dict]) -> list[dict]:
    """
    Group consecutive words เป็น phrases สั้น ๆ โดย "ตัดที่ pause/punctuation"
    เพื่อรักษาคำให้ครบ (ไม่ตัดกลางคำ) — ไม่ใช่ตัดที่ char limit แบบดื้อ ๆ

    หลักการ:
    - hard cut เมื่อ candidate > HARD_MAX_CHARS  (กันยาวเกิน)
    - soft cut เมื่อ text_buf >= SOFT_MAX_CHARS และ มี pause > PAUSE_THRESHOLD
    - soft cut เมื่อ ตัวท้าย text_buf เป็น punctuation (จบประโยค)

    คืนค่า: [{"start": ..., "end": ..., "text": ...}]
    """
    if not words:
        return []

    # Preprocess: รวม dependent marks เข้ากับพยัญชนะนำหน้า กัน split กลาง syllable
    words = _merge_dependent_marks(words)

    phrases: list[dict] = []
    bucket: list[dict] = []
    text_buf = ""

    def flush():
        if bucket:
            phrases.append({
                "start": bucket[0]["start"],
                "end":   bucket[-1]["end"],
                "text":  text_buf.strip(),
            })

    for w in words:
        token = (w.get("text") or "").strip()
        if not token:
            continue

        candidate = _join_token(text_buf, token)
        should_cut = False

        if bucket:
            prev_end = bucket[-1]["end"]
            pause = w["start"] - prev_end
            phrase_dur = bucket[-1]["end"] - bucket[0]["start"]

            if len(candidate) > HARD_MAX_CHARS:
                # ยาวเกิน — ต้องตัดแม้ไม่เจอ pause
                should_cut = True
            elif phrase_dur >= MAX_PHRASE_DURATION:
                # phrase ยาวเกินเวลา → ตัดเพื่อไม่ให้ subtitle ค้าง
                should_cut = True
            elif len(text_buf) >= SOFT_MAX_CHARS and pause >= PAUSE_THRESHOLD:
                # ถึง soft limit + มี pause = จุดตัดที่ดี
                should_cut = True
            elif text_buf and text_buf[-1] in SENTENCE_END_CHARS:
                # จบประโยคด้วย punctuation
                should_cut = True

        if should_cut:
            flush()
            bucket = [w]
            text_buf = token
        else:
            bucket.append(w)
            text_buf = candidate

    flush()
    return phrases


def generate_srt_from_transcript(
    transcript: list[dict],
    keep_segments: list[dict],
    srt_path: str,
) -> int:
    """
    สร้าง SRT จาก transcript (มี word-level timestamps) → คืนจำนวน entries

    Args:
        transcript:    [{"start": 1.2, "end": 4.5, "text": "...", "words": [...]}]
        keep_segments: [{"start": 10.0, "end": 25.0}]
        srt_path:      path สำหรับเขียน .srt
    """
    keep_sorted = sorted(keep_segments, key=lambda x: x["start"])
    # cumulative offset ของแต่ละ keep_segment ใน output timeline
    offsets = {}
    cumulative = 0.0
    for k in keep_sorted:
        offsets[id(k)] = cumulative
        cumulative += (k["end"] - k["start"])

    # ── Step 1: collect ทุก phrase พร้อม remap timestamp ──────────────────────
    raw_entries: list[tuple[float, float, str]] = []
    for seg in transcript:
        k = _segment_midpoint_in_keep(seg["start"], seg["end"], keep_sorted)
        if k is None:
            continue
        offset = offsets[id(k)]

        # ใช้ segment text (proper Thai) + แบ่งที่ safe position
        # หลีกเลี่ยง Whisper word_timestamps ที่ broken Thai เป็น sub-syllables
        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue
        phrases = _split_segment_text(seg_text, seg["start"], seg["end"])

        for ph in phrases:
            text = (ph.get("text") or "").strip()
            if not text:
                continue
            # ตัด phrase ให้อยู่ในช่วง keep_segment (กัน overflow)
            ps = max(ph["start"], k["start"])
            pe = min(ph["end"],   k["end"])
            if pe <= ps:
                continue
            new_start = ps - k["start"] + offset
            new_end   = pe - k["start"] + offset
            raw_entries.append((new_start, new_end, text))

    # ── Step 2: sort + บังคับไม่ overlap (PHRASE_GAP ก่อนตัวถัดไป) ─────────────
    raw_entries.sort(key=lambda x: x[0])
    final: list[tuple[float, float, str]] = []
    for start, end, text in raw_entries:
        if final and start < final[-1][1] + PHRASE_GAP:
            # ดัน start ไปหลัง phrase ก่อนหน้า + gap
            start = final[-1][1] + PHRASE_GAP
        if end <= start + MIN_PHRASE_DURATION:  # phrase สั้นเกิน → skip
            continue
        final.append((start, end, text))

    # ── Step 3: write SRT ─────────────────────────────────────────────────────
    lines: list[str] = []
    for i, (start, end, text) in enumerate(final, start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[SRT] Generated {len(final)} subtitle entries "
          f"(soft={SOFT_MAX_CHARS} hard={HARD_MAX_CHARS} pause={PAUSE_THRESHOLD}s) → {srt_path}")
    return len(final)
