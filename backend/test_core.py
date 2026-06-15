import os
import time
from dotenv import load_dotenv
from core.ffmpeg_utils import extract_clean_audio, edit_and_merge_video
from core.vad_logic import get_voice_activity
from core.ai_logic import analyze_video_content

# 1. โหลดการตั้งค่า (โดยเฉพาะ API Key) จากไฟล์ .env
load_dotenv()

def run_real_test(video_file_name, user_instruction):
    # สร้างโฟลเดอร์สำหรับเก็บผลลัพธ์การทดสอบ
    job_id = "test_run_" + str(int(time.time()))
    job_dir = os.path.join("storage", job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    video_path = video_file_name
    audio_path = os.path.join(job_dir, "extracted_audio.wav")
    output_video_path = os.path.join(job_dir, "final_summary.mp4")

    print(f"🚀 เริ่มการประมวลผลสำหรับ Job: {job_id}")
    print(f"📽️ ไฟล์วิดีโอต้นฉบับ: {video_path}")

    try:
        # ขั้นตอนที่ 1: แยกและคลีนเสียง
        print("\n[Step 1/4] กำลังแยกเสียงและลดเสียงรบกวน...")
        extract_clean_audio(video_path, audio_path)
        print("✅ แยกเสียงสำเร็จ!")

        # ขั้นตอนที่ 2: ตรวจจับช่วงเสียงพูดจริง (VAD)
        print("\n[Step 2/4] กำลังสแกนหาช่วงเงียบด้วย VAD...")
        voice_segments = get_voice_activity(audio_path)
        print(f"✅ พบช่วงที่มีการพูดจริงทั้งหมด {len(voice_segments)} ช่วง")

        # ขั้นตอนที่ 3: ส่งให้ Gemini 3.1 Flash วิเคราะห์เนื้อหา
        print("\n[Step 3/4] กำลังส่งให้ Gemini วิเคราะห์ 'เนื้อ' และ 'น้ำ'...")
        # เราส่ง User Instruction ที่คุณพิมพ์สั่ง (Prompt) ไปให้ AI
        ai_decision_json = analyze_video_content(video_path, user_instruction)
        print("✅ AI วิเคราะห์เสร็จสิ้นและคืนค่า JSON มาแล้ว!")
        print(f"📊 ผลลัพธ์ AI: {ai_decision_json}")

        # ขั้นตอนที่ 4: ตัดต่อและรวมไฟล์จริง
        print("\n[Step 4/4] กำลัง Render วิดีโอใหม่ด้วยเทคนิค Stream Copy...")
        edit_and_merge_video(video_path, ai_decision_json, output_video_path, job_dir)
        
        print(f"\n✨ เสร็จสมบูรณ์! ✨")
        print(f"🎬 วิดีโอผลลัพธ์อยู่ที่: {output_video_path}")

    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {str(e)}")

if __name__ == "__main__":
    # --- ส่วนที่คุณต้องแก้ไขก่อนรัน ---
    # 1. ใส่ชื่อไฟล์วิดีโอที่มีอยู่ในเครื่อง (เช่น "my_lecture.mp4")
    target_video = "your_test_video.mp4" 
    
    # 2. ใส่คำสั่งที่อยากให้ AI ทำ (Prompt)
    my_prompt = "ตัดวิดีโอให้เหลือเฉพาะเนื้อหาสาระสำคัญ ตัดช่วงพูดเล่นหรือนอกเรื่องออก"
    
    if os.path.exists(target_video):
        run_real_test(target_video, my_prompt)
    else:
        print(f"⚠️ ไม่พบไฟล์วideโอ '{target_video}' กรุณาเตรียมไฟล์ไว้ในโฟลเดอร์ backend ก่อนครับ")