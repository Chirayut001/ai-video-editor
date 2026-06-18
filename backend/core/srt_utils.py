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
SOFT_MAX_CHARS = 14
# Hard limit: ถึงไม่มี pause ก็ต้องตัด (กันยาวเกิน)
HARD_MAX_CHARS = 28
# Max duration ของ 1 phrase (วินาที) — กันค้างเกินเวลา
MAX_PHRASE_DURATION = 1.8
# pause threshold (วินาที) — gap เล็กน้อยก็ถือว่าตัดได้
PAUSE_THRESHOLD = 0.05
# punctuation ที่จบประโยค → cut ทันที
SENTENCE_END_CHARS = ".!?,。！？"
# Thai sentence-end particles (ครับ ค่ะ นะ ฯลฯ) — strong break point
THAI_END_PARTICLES = {"ครับ", "ค่ะ", "ครับผม", "นะครับ", "นะคะ", "จ้า", "จ๊ะ", "เลย", "แหละ"}
# Thai conjunctions — medium break point (ตัดก่อนคำเหล่านี้)
THAI_CONJUNCTIONS = {"และ", "แต่", "หรือ", "เพราะ", "แล้ว", "ก็", "จึง", "ดังนั้น", "อย่างไรก็ตาม", "ส่วน"}
# เว้นช่วงเล็ก ๆ ระหว่าง phrase (วินาที)
PHRASE_GAP = 0.02
# Min duration ของ subtitle entry — ต่ำกว่านี้ถูกตัดทิ้ง (กัน flash เกินไป)
MIN_PHRASE_DURATION = 0.05
# Min char ของ phrase — ถ้าน้อยกว่านี้ตอน scan break point ให้รวมต่อ
MIN_PHRASE_CHARS = 9
# Subtitle Lead Time (วินาที) — subtitle ปรากฏก่อนเสียงพูดเล็กน้อย ให้ผู้ดูทันอ่าน
# Whisper มัก return start ช้ากว่าเสียงจริง ~100-300ms → ชดเชยด้วยค่านี้
SUBTITLE_LEAD_TIME = 0.18


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


def _break_priority(word: str, next_word: str | None, bucket_after: str) -> int:
    """
    คืน priority ของ break point หลัง word นี้ (สูง = ควรตัดที่นี่):
      4 = หลัง sentence-end punctuation (. ! ? ,)
      3 = หลัง Thai end particle (ครับ ค่ะ นะ)
      2 = ก่อน Thai conjunction (และ แต่ ที่)
      1 = ขอบ Thai → Latin หรือ Latin → Thai (script transition)
      0 = ไม่ใช่ break point
    """
    if not word:
        return 0
    last_char = word[-1:]
    if last_char in SENTENCE_END_CHARS:
        return 4
    if word in THAI_END_PARTICLES:
        return 3
    if next_word and next_word in THAI_CONJUNCTIONS:
        return 2
    # Script transition: ไทย ↔ Latin
    if next_word and word and _is_thai_char(word[-1]) != _is_thai_char(next_word[:1]):
        # เฉพาะถ้าทั้งคู่ไม่ว่าง
        if _is_thai_char(word[-1]) or _is_thai_char(next_word[:1]):
            return 1
    return 0


def _split_segment_text(text: str, seg_start: float, seg_end: float,
                         soft_max: int = SOFT_MAX_CHARS,
                         hard_max: int = HARD_MAX_CHARS,
                         max_dur: float = MAX_PHRASE_DURATION) -> list[dict]:
    """
    แบ่ง segment text เป็น chunks โดย "ตัดที่ natural break point":
    1. Tokenize เป็น words (PyThaiNLP newmm)
    2. Scan word-by-word เก็บ "candidate break points" + priority
    3. เมื่อ bucket ถึง SOFT_MAX → ตัดที่ break point ที่ดีที่สุด (lookback)
    4. ถ้าเกิน HARD_MAX → ตัดทันที (ไม่รอ)
    5. กระจายเวลาตามจำนวน chars
    """
    text = text.strip()
    if not text:
        return []
    duration = max(0.01, seg_end - seg_start)

    words = _tokenize_thai_aware(text)
    if not words:
        return [{"start": seg_start, "end": seg_end, "text": text}]

    # Step 1: รวม words เป็น chunks ด้วย smart break logic
    chunks_text: list[str] = []
    bucket_words: list[str] = []     # words ใน bucket ปัจจุบัน
    bucket_chars: list[int] = []     # ความยาวของ bucket หลังเพิ่มแต่ละ word
    break_at: list[int] = []          # index ของ best break point (priority สูง = ดี)
    break_pri: list[int] = []         # priority ของ break ที่ index นั้น

    def cur_text() -> str:
        s = ""
        for w in bucket_words:
            s = _join_token(s, w)
        return s

    def flush_at(idx: int | None):
        """Flush bucket → chunks. ถ้า idx ระบุ → ตัดที่ index นั้น (เก็บ words[0:idx])"""
        nonlocal bucket_words, bucket_chars, break_at, break_pri
        if not bucket_words:
            return
        if idx is None or idx >= len(bucket_words):
            head = bucket_words
            tail: list[str] = []
        else:
            head = bucket_words[:idx]
            tail = bucket_words[idx:]
        if head:
            head_text = ""
            for w in head:
                head_text = _join_token(head_text, w)
            chunks_text.append(head_text.strip())
        # Reset bucket with tail
        bucket_words = tail
        bucket_chars = []
        s = ""
        for w in bucket_words:
            s = _join_token(s, w)
            bucket_chars.append(len(s))
        break_at = []
        break_pri = []

    for i, w in enumerate(words):
        next_w = words[i + 1] if i + 1 < len(words) else None
        bucket_words.append(w)
        s = cur_text()
        bucket_chars.append(len(s))

        # บันทึก break point หลัง word นี้
        pri = _break_priority(w, next_w, s)
        if pri > 0:
            break_at.append(len(bucket_words))   # split point = หลัง index นี้
            break_pri.append(pri)

        # ─── เช็คว่าควรตัดหรือยัง ───
        if len(s) > hard_max and len(bucket_words) > 1:
            # เกิน hard limit → หา best break (ภายใน lookback 8 chars จากท้าย)
            cutoff_char = len(s) - 8     # มองย้อนไป 8 chars
            best_idx = None
            best_pri = -1
            for bp_idx, bp_pri in zip(break_at, break_pri):
                bp_len = bucket_chars[bp_idx - 1] if bp_idx > 0 else 0
                if bp_pri > best_pri and bp_len >= MIN_PHRASE_CHARS:
                    if bp_len >= soft_max or bp_pri >= 3:
                        best_idx = bp_idx
                        best_pri = bp_pri
            if best_idx is not None:
                flush_at(best_idx)
            else:
                # ไม่มี break ดี → ตัดก่อน word ปัจจุบัน
                flush_at(len(bucket_words) - 1)
        elif len(s) >= soft_max:
            # ถึง soft limit → ตัดถ้าเจอ break priority สูง (3-4)
            if break_pri and max(break_pri) >= 3:
                # ตัดที่ break ตัวล่าสุดที่ pri >= 3
                for j in range(len(break_at) - 1, -1, -1):
                    if break_pri[j] >= 3:
                        flush_at(break_at[j])
                        break

    # Flush remaining
    flush_at(None)

    # Step 2: ถ้า phrase ใดยาวเกินเวลา (> max_dur) → แบ่งเพิ่ม
    n_by_dur = max(1, int(duration / max_dur + 0.5))
    if len(chunks_text) < n_by_dur:
        # แบ่งใหม่ด้วย hard limit ที่เล็กกว่า (กระจายเวลา)
        target_chars = max(MIN_PHRASE_CHARS, int(len("".join(chunks_text)) / n_by_dur))
        new_chunks = []
        bucket = ""
        for w in words:
            candidate = _join_token(bucket, w)
            if len(candidate) > target_chars and bucket:
                new_chunks.append(bucket)
                bucket = w
            else:
                bucket = candidate
        if bucket:
            new_chunks.append(bucket)
        if len(new_chunks) >= n_by_dur:
            chunks_text = new_chunks

    chunks_text = [c.strip() for c in chunks_text if c.strip()]

    # Step 2.5: Merge phrases สั้น (< MIN_PHRASE_CHARS) เข้ากับ neighbor
    # เพื่อกัน "นะครับ", "เบอร์แมน" ลอยเดี่ยว
    if len(chunks_text) > 1:
        merged: list[str] = []
        for c in chunks_text:
            if len(c) < MIN_PHRASE_CHARS and merged:
                # Try merge with previous (ถ้าไม่เกิน hard limit)
                combined = _join_token(merged[-1], c)
                if len(combined) <= hard_max + 4:  # ยอม overshoot นิดเดียว
                    merged[-1] = combined
                    continue
            merged.append(c)
        # Pass 2: ถ้า last phrase สั้น → ลอง merge with previous อีก
        if len(merged) >= 2 and len(merged[-1]) < MIN_PHRASE_CHARS:
            combined = _join_token(merged[-2], merged[-1])
            if len(combined) <= hard_max + 6:
                merged = merged[:-2] + [combined]
        chunks_text = merged

    # Step 3: กระจายเวลาตาม char fraction
    if not chunks_text:
        return [{"start": seg_start, "end": seg_end, "text": text}]
    total_chars = sum(len(c) for c in chunks_text)
    if total_chars == 0:
        return [{"start": seg_start, "end": seg_end, "text": text}]
    chunks = []
    char_pos = 0
    for c in chunks_text:
        frac_start = char_pos / total_chars
        char_pos += len(c)
        frac_end = char_pos / total_chars
        chunks.append({
            "start": seg_start + frac_start * duration,
            "end":   seg_start + frac_end * duration,
            "text":  c,
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


def generate_phrases_from_transcript(
    transcript: list[dict],
    keep_segments: list[dict],
) -> list[dict]:
    """
    สร้าง list ของ phrases (ยังไม่เขียนไฟล์) สำหรับให้ user แก้ก่อน burn เป็น SRT

    Args:
        transcript:    [{"start": 1.2, "end": 4.5, "text": "...", "words": [...]}]
        keep_segments: [{"start": 10.0, "end": 25.0}]

    Returns:
        [{"start": float, "end": float, "text": str}]  timestamps อยู่ใน output timeline
    """
    keep_sorted = sorted(keep_segments, key=lambda x: x["start"])
    offsets = {}
    cumulative = 0.0
    for k in keep_sorted:
        offsets[id(k)] = cumulative
        cumulative += (k["end"] - k["start"])

    raw_entries: list[tuple[float, float, str]] = []
    for seg in transcript:
        k = _segment_midpoint_in_keep(seg["start"], seg["end"], keep_sorted)
        if k is None:
            continue
        offset = offsets[id(k)]

        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue
        phrases = _split_segment_text(seg_text, seg["start"], seg["end"])

        for ph in phrases:
            text = (ph.get("text") or "").strip()
            if not text:
                continue
            ps = max(ph["start"], k["start"])
            pe = min(ph["end"],   k["end"])
            if pe <= ps:
                continue
            new_start = ps - k["start"] + offset
            new_end   = pe - k["start"] + offset
            raw_entries.append((new_start, new_end, text))

    raw_entries.sort(key=lambda x: x[0])
    final_phrases: list[dict] = []
    for start, end, text in raw_entries:
        if final_phrases and start < final_phrases[-1]["end"] + PHRASE_GAP:
            start = final_phrases[-1]["end"] + PHRASE_GAP
        if end <= start + MIN_PHRASE_DURATION:
            continue
        final_phrases.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        })

    return final_phrases


def _apply_subtitle_lead(phrases: list[dict], lead_time: float) -> list[dict]:
    """
    Shift subtitle start เริ่มก่อนเสียง (ชดเชย Whisper bias + ให้ผู้อ่านทันอ่าน)
    - ลด start ลง LEAD_TIME (แต่ไม่ต่ำกว่า 0)
    - คง end เดิม → subtitle ค้างนานขึ้นบนจอ
    - ถ้า shift แล้ว overlap กับ phrase ก่อนหน้า → ปรับเป็นหลัง prev.end + PHRASE_GAP
    """
    if lead_time <= 0:
        return phrases
    result = []
    for ph in phrases:
        start = float(ph.get("start", 0))
        end = float(ph.get("end", 0))
        new_start = max(0.0, start - lead_time)
        if result:
            prev_end = float(result[-1].get("end", 0))
            new_start = max(new_start, prev_end + PHRASE_GAP)
        # ถ้า shift แล้ว new_start >= end → ใช้ start เดิม (ไม่ shift)
        if new_start >= end - MIN_PHRASE_DURATION:
            new_start = start
        result.append({**ph, "start": round(new_start, 3), "end": round(end, 3)})
    return result


def write_srt_from_phrases(phrases: list[dict], srt_path: str,
                            lead_time: float = SUBTITLE_LEAD_TIME) -> int:
    """
    เขียน SRT file จาก phrases (อาจเป็น phrases ที่ user แก้แล้ว) → คืนจำนวน entries
    lead_time: shift subtitle start เริ่มก่อนเสียง (default 150ms)
    """
    phrases = _apply_subtitle_lead(phrases, lead_time)

    lines: list[str] = []
    written = 0
    for ph in phrases:
        text = (ph.get("text") or "").strip()
        if not text:
            continue
        start = float(ph.get("start", 0))
        end = float(ph.get("end", 0))
        if end <= start + MIN_PHRASE_DURATION:
            continue
        written += 1
        lines.append(str(written))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[SRT] Wrote {written} subtitle entries → {srt_path}")
    return written


def generate_srt_from_transcript(
    transcript: list[dict],
    keep_segments: list[dict],
    srt_path: str,
) -> int:
    """Backward-compat: generate phrases + write SRT ในขั้นตอนเดียว → คืนจำนวน entries"""
    phrases = generate_phrases_from_transcript(transcript, keep_segments)
    return write_srt_from_phrases(phrases, srt_path)
