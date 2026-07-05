import React, { useState, useEffect } from 'react';
import {
  Upload, MessageSquare, X, MicOff, BookOpen, Scissors, GraduationCap,
  Mic, Briefcase, Gamepad2, Smartphone, Edit, FileVideo, Zap, Sparkles,
  Star, Video, ShoppingBag, Utensils, Presentation, HelpCircle, Smile,
} from 'lucide-react';
import axios from 'axios';
import { API_URL } from '../config';

// preset เขียนแบบ "เก็บอะไร / ตัดอะไร" ให้ชัด → AI ตัดสินใจแม่นขึ้น
const PROMPT_PRESETS = [
  { id: 'silence',  icon: MicOff,          label: 'ตัดความเงียบ',  desc: 'ลบ filler + ช่วงเงียบ',
    text: 'ตัดเฉพาะช่วงเงียบ ช่วงหยุดคิดนาน คำ filler (อืม เอ่อ อ่อ เอิ่ม) และช่วงพูดซ้ำคำเดิมโดยไม่เพิ่มข้อมูล — เก็บเนื้อหาที่พูดจริงไว้ครบ ไม่ตัดใจความ' },
  { id: 'essence',  icon: BookOpen,        label: 'เก็บสาระสำคัญ', desc: 'concept + ตัวอย่าง',
    text: 'เก็บแนวคิด หลักการ คำอธิบาย และตัวอย่างที่ช่วยให้เข้าใจประเด็นหลัก — ตัดการเกริ่นยาว เรื่องเล่านอกประเด็น มุกที่ไม่เกี่ยวข้อง และช่วงพูดวกวน' },
  { id: 'shortest', icon: Scissors,        label: 'สรุปสั้นที่สุด', desc: 'key points เท่านั้น',
    text: 'ตัดให้สั้นกระชับที่สุด เก็บเฉพาะประเด็นสำคัญที่สุด (key points) และข้อสรุป — ตัด background การเกริ่น ตัวอย่างเสริม และรายละเอียดปลีกย่อยทั้งหมด' },
  { id: 'tutorial', icon: GraduationCap,   label: 'วิดีโอสอน',     desc: 'step-by-step',
    text: 'วิดีโอสอน — เก็บการอธิบายแนวคิด ขั้นตอนทำจริง (step-by-step) และตัวอย่างที่ลงมือทำ — ตัดการเกริ่นยาว พูดนอกเรื่อง ช่วงผู้สอนสับสน/แก้ที่ผิด และช่วงรอโหลด/เตรียมของ' },
  { id: 'seminar',  icon: Presentation,    label: 'สัมมนา/บรรยาย', desc: 'ประเด็นหลัก + Q&A',
    text: 'สัมมนา/บรรยาย — เก็บประเด็นหลัก สาระที่บรรยาย ข้อมูล/สถิติสำคัญ และช่วงถาม-ตอบที่มีสาระ — ตัดการแนะนำวิทยากรยาว การรอเริ่มงาน ปัญหาเทคนิค และช่วงพักเบรก' },
  { id: 'review',   icon: Star,            label: 'รีวิวสินค้า',   desc: 'ข้อดี-ข้อเสีย + สรุป',
    text: 'รีวิวสินค้า/บริการ — เก็บข้อดี ข้อเสีย ฟีเจอร์เด่น ราคา ประสบการณ์ใช้จริง และบทสรุป/คำแนะนำ — ตัดการเกริ่นนำยาว unboxing ที่ยืดเยื้อ และการพูดนอกเรื่อง' },
  { id: 'cooking',  icon: Utensils,        label: 'ทำอาหาร',       desc: 'สูตร + ขั้นตอน',
    text: 'ทำอาหาร — เก็บส่วนผสม ขั้นตอนการทำ เทคนิค/เคล็ดลับ และช่วงชิม/ผลลัพธ์ — ตัดการเกริ่นนำยาว ช่วงรอต้ม/รออบ/รอทอดที่ไม่มีคำอธิบาย และการพูดนอกเรื่อง' },
  { id: 'podcast',  icon: Mic,             label: 'Podcast/สัมภาษณ์', desc: 'แก่นการสนทนา',
    text: 'Podcast/สัมภาษณ์ — เก็บแก่นการสนทนา คำตอบสำคัญของแขก ประเด็นน่าสนใจและข้อคิด — ตัด small talk ทักทายช่วงต้น การทวนคำถามซ้ำ ช่วงคุยหลุดประเด็นยาว และการโปรโมท/ขอบคุณตอนท้ายที่ยืดเยื้อ' },
  { id: 'qa',       icon: HelpCircle,      label: 'ถาม-ตอบ/Q&A',   desc: 'คำถาม + คำตอบ',
    text: 'ไลฟ์ถาม-ตอบ — เก็บคำถามและคำตอบที่มีสาระ ประเด็นที่น่าสนใจ — ตัดช่วงรอคำถาม การทักทาย พูดคุยเล่น และช่วงที่ตอบวกวนไม่ตรงคำถาม' },
  { id: 'vlog',     icon: Video,           label: 'Vlog/เล่าเรื่อง', desc: 'ไฮไลต์ + เล่าเรื่อง',
    text: 'Vlog/เล่าเรื่อง — เก็บช่วงเล่าเรื่องสำคัญ ไฮไลต์ของวัน และช่วงที่มีอารมณ์ร่วม/น่าสนใจ — ตัดช่วงเดินทางเงียบ ๆ ช่วงเตรียมตัว และช่วงที่ไม่มีอะไรเกิดขึ้น' },
  { id: 'reaction', icon: Smile,           label: 'Reaction',      desc: 'ช่วงรีแอคเด่น',
    text: 'Reaction/ดูคลิป — เก็บช่วงที่มีปฏิกิริยาชัด (ตกใจ/ตลก/ประทับใจ) และช่วงคอมเมนต์/วิเคราะห์ที่น่าสนใจ — ตัดช่วงดูเงียบ ๆ ไม่พูด และการเกริ่นยาว' },
  { id: 'sales',    icon: ShoppingBag,     label: 'ไลฟ์ขายของ',    desc: 'สินค้า + ราคา + โปร',
    text: 'ไลฟ์ขายของ — เก็บช่วงพรีเซนต์สินค้า จุดขาย ราคา โปรโมชั่น และวิธีสั่งซื้อ — ตัดช่วงทักทาย รอลูกค้า พูดคุยเล่น และช่วงพูดซ้ำที่ไม่มีข้อมูลใหม่' },
  { id: 'meeting',  icon: Briefcase,       label: 'ประชุม', desc: 'ตัดสินใจ + action',
    text: 'สรุปประชุม — เก็บการตัดสินใจ ข้อสรุป action item ผู้รับผิดชอบ deadline และประเด็นสำคัญที่ถกกัน — ตัด chitchat ก่อนเริ่ม ช่วงรอคนเข้า การพูดนอกวาระ และปัญหาเทคนิค (เสียงหาย รอแชร์จอ)' },
  { id: 'gaming',   icon: Gamepad2,        label: 'Live/Gaming',  desc: 'ไฮไลต์เท่านั้น',
    text: 'Live/เกม — เก็บช่วงไฮไลต์ (kill, ชนะ, จังหวะพลิก, react ตลก, ช่วงลุ้น) และ milestone สำคัญ — ตัดช่วงเดินทาง รอ respawn/loading, AFK, เมนู และช่วงเงียบที่ไม่มีอะไรเกิดขึ้น' },
  { id: 'tiktok',   icon: Smartphone,      label: 'TikTok/Reels', desc: '9:16 · hook + พีค',
    text: 'คลิปสั้นแนวตั้ง — เลือกช่วงที่ปังที่สุด เริ่มด้วย hook ดึงความสนใจใน 3 วินาทีแรก ตามด้วยช่วงพีค (ตลก/น่าทึ่ง/น่าจดจำ) — ตัดทุกอย่างที่ยืดเยื้อ เน้นกระชับและ engaging' },
  { id: 'custom',   icon: Edit,            label: 'Custom', desc: 'พิมพ์เอง',
    text: '' },
];

// จำกัดขนาดไฟล์ฝั่ง client — ตรงกับ MAX_FILE_SIZE_MB ของ backend (กันเริ่มอัปโหลดแล้วโดน 413)
const MAX_FILE_MB = 2048;

const UploadScreen = ({ onUploadSuccess }) => {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [activePresetId, setActivePresetId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [targetLength, setTargetLength] = useState(60);
  const [burnSubtitle, setBurnSubtitle] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);   // % อัปโหลดจริง
  const [error, setError] = useState('');                    // error inline (แทน alert)

  const isTiktokMode = activePresetId === 'tiktok';

  const handlePresetClick = (preset) => {
    setPrompt(preset.text);
    setActivePresetId(preset.id);
  };

  const handlePromptChange = (e) => {
    const value = e.target.value;
    setPrompt(value);
    if (activePresetId === 'tiktok') return;
    const matched = PROMPT_PRESETS.find(p => p.text === value && p.id !== 'custom');
    setActivePresetId(matched ? matched.id : (value === '' ? null : 'custom'));
  };

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0];
    pickFile(selectedFile);
  };

  const pickFile = (selectedFile) => {
    if (!selectedFile) return;
    if (!selectedFile.type.startsWith('video/')) {
      setError('กรุณาเลือกไฟล์วิดีโอเท่านั้น');
      return;
    }
    if (selectedFile.size > MAX_FILE_MB * 1024 * 1024) {
      const sizeMB = (selectedFile.size / 1024 / 1024).toFixed(0);
      setError(`ไฟล์ใหญ่เกิน ${MAX_FILE_MB} MB (ไฟล์นี้ ${sizeMB} MB) — กรุณาเลือกไฟล์เล็กลง`);
      return;
    }
    setError('');
    setFile(selectedFile);
    const url = URL.createObjectURL(selectedFile);
    setPreviewUrl(url);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    pickFile(e.dataTransfer.files[0]);
  };

  const clearFile = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(null);
    setPreviewUrl(null);
  };

  useEffect(() => {
    return () => { if (previewUrl) URL.revokeObjectURL(previewUrl); };
  }, [previewUrl]);

  const handleUpload = async () => {
    if (!file) return setError('กรุณาเลือกไฟล์วิดีโอก่อน');
    if (!prompt.trim()) return setError('กรุณาเลือก preset หรือพิมพ์คำสั่งให้ AI');

    setError('');
    setUploadProgress(0);
    setLoading(true);
    const formData = new FormData();
    formData.append('video', file);
    formData.append('prompt', prompt);
    formData.append('output_mode', isTiktokMode ? 'tiktok' : 'standard');
    formData.append('target_length', String(targetLength));
    formData.append('burn_subtitle', String(burnSubtitle));
    formData.append('preview_mode', String(previewMode));
    formData.append('preset_id', activePresetId || '');

    const uploadUrl = `${API_URL}/upload`;
    try {
      const response = await axios.post(uploadUrl, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 30 * 60 * 1000,
        onUploadProgress: (e) => {
          const total = e.total || file.size;
          setUploadProgress(Math.round((e.loaded * 100) / total));
        },
      });
      onUploadSuccess(response.data.job_id, previewMode ? 'preview' : 'final');
    } catch (err) {
      let msg = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้';
      if (err.response) {
        msg = err.response.data?.detail || `Server Error ${err.response.status}`;
      } else if (err.code === 'ECONNABORTED') {
        msg = 'ใช้เวลานานเกินไป (Timeout)';
      }
      setError(msg);
      setLoading(false);
    }
  };

  const sizeMB = file ? (file.size / 1024 / 1024).toFixed(1) : 0;

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Hero */}
      <div className="text-center mb-2">
        <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-semibold mb-3">
          <Zap className="h-3 w-3" />
          AI-Powered Editing
        </div>
        <h2 className="text-2xl sm:text-3xl font-bold text-slate-800">
          ตัดวิดีโออัตโนมัติ <span className="bg-gradient-to-r from-blue-600 to-violet-600 bg-clip-text text-transparent">ด้วย AI</span>
        </h2>
        <p className="text-sm text-slate-500 mt-1.5">อัปโหลด → เลือก preset → ได้วิดีโอที่ตัดเสร็จ</p>
      </div>

      {/* Upload area */}
      <section className="bg-white rounded-2xl shadow-sm shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={onDrop}
          className={`relative p-6 sm:p-8 transition-all ${
            isDragOver ? 'bg-blue-50' : ''
          }`}
        >
          {!previewUrl ? (
            <label
              htmlFor="video-input"
              className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl py-10 cursor-pointer transition-all ${
                isDragOver
                  ? 'border-blue-400 bg-blue-50/50'
                  : 'border-slate-300 hover:border-blue-400 hover:bg-slate-50'
              }`}
            >
              <input
                id="video-input"
                type="file"
                accept="video/*"
                onChange={handleFileChange}
                className="hidden"
              />
              <div className={`h-14 w-14 rounded-2xl flex items-center justify-center transition-colors ${
                isDragOver ? 'bg-blue-100' : 'bg-slate-100'
              }`}>
                <Upload className={`h-6 w-6 ${isDragOver ? 'text-blue-600' : 'text-slate-400'}`} />
              </div>
              <p className="text-sm font-semibold text-slate-700 mt-3">
                {isDragOver ? 'ปล่อยไฟล์ที่นี่' : 'คลิกหรือลากไฟล์มาวาง'}
              </p>
              <p className="text-xs text-slate-400 mt-1">
                MP4, MOV, MKV, AVI, WebM · สูงสุด 2GB
              </p>
            </label>
          ) : (
            <div className="space-y-3">
              <div className="relative rounded-xl overflow-hidden bg-slate-900 aspect-video">
                <button
                  onClick={clearFile}
                  className="absolute top-2 right-2 z-10 bg-black/50 backdrop-blur-sm text-white p-1.5 rounded-full hover:bg-black/70 transition-colors"
                  title="ลบไฟล์"
                >
                  <X className="h-4 w-4" />
                </button>
                <video src={previewUrl} controls className="w-full h-full object-contain" />
              </div>
              <div className="flex items-center gap-2.5 text-xs text-slate-600">
                <FileVideo className="h-4 w-4 text-blue-500 flex-shrink-0" />
                <span className="font-medium truncate flex-1">{file.name}</span>
                <span className="text-slate-400 flex-shrink-0">{sizeMB} MB</span>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Preset chips */}
      <section className="bg-white rounded-2xl shadow-sm shadow-slate-200/50 border border-slate-200 p-5 sm:p-6">
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare className="h-4 w-4 text-blue-500" />
          <h3 className="text-sm font-semibold text-slate-800">เลือกสไตล์การตัดต่อ</h3>
          <span className="text-xs text-slate-400">เลือก 1 แบบ หรือพิมพ์เอง</span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
          {PROMPT_PRESETS.map((preset) => {
            const Icon = preset.icon;
            const isActive = activePresetId === preset.id;
            const isTikTok = preset.id === 'tiktok';
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => handlePresetClick(preset)}
                className={`group flex items-start gap-2 p-2.5 rounded-xl border-2 text-left transition-all ${
                  isActive
                    ? isTikTok
                      ? 'border-pink-500 bg-pink-50'
                      : 'border-blue-500 bg-blue-50'
                    : 'border-slate-200 hover:border-slate-300 bg-white hover:bg-slate-50'
                }`}
              >
                <div className={`h-7 w-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors ${
                  isActive
                    ? isTikTok ? 'bg-pink-500 text-white' : 'bg-blue-500 text-white'
                    : 'bg-slate-100 text-slate-600 group-hover:bg-slate-200'
                }`}>
                  <Icon className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className={`text-xs font-semibold leading-tight truncate ${
                    isActive ? (isTikTok ? 'text-pink-700' : 'text-blue-700') : 'text-slate-800'
                  }`}>
                    {preset.label}
                  </p>
                  <p className="text-[10px] text-slate-500 leading-tight truncate mt-0.5">{preset.desc}</p>
                </div>
              </button>
            );
          })}
        </div>

        <textarea
          className="w-full p-3 text-sm border border-slate-200 rounded-xl focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 outline-none transition-all resize-none"
          rows="3"
          placeholder="คำสั่งจะปรากฏที่นี่เมื่อคุณเลือก preset · หรือพิมพ์เอง"
          value={prompt}
          onChange={handlePromptChange}
        />

        {/* TikTok options */}
        {isTiktokMode && (
          <div className="mt-3 p-4 bg-gradient-to-br from-pink-50 to-fuchsia-50 border border-pink-200 rounded-xl space-y-3 animate-in fade-in slide-in-from-top-2 duration-300">
            <div className="flex items-center gap-2">
              <Smartphone className="h-4 w-4 text-pink-600" />
              <p className="text-xs font-semibold text-pink-700">TikTok/Reels Options</p>
            </div>
            <div>
              <label className="text-xs text-slate-600 block mb-1.5">ความยาวสูงสุด</label>
              <div className="grid grid-cols-3 gap-2">
                {[30, 60, 90].map((sec) => (
                  <button
                    key={sec}
                    type="button"
                    onClick={() => setTargetLength(sec)}
                    className={`px-3 py-2 rounded-lg text-sm border transition-all ${
                      targetLength === sec
                        ? 'border-pink-500 bg-pink-100 text-pink-700 font-semibold shadow-sm'
                        : 'border-slate-200 bg-white text-slate-600 hover:border-pink-300'
                    }`}
                  >
                    {sec}s
                  </button>
                ))}
              </div>
            </div>
            <p className="text-[11px] text-slate-500 flex items-center gap-1">
            </p>
          </div>
        )}

        {/* Subtitle */}
        <label className={`flex items-start gap-3 mt-3 p-3.5 rounded-xl border cursor-pointer transition-all ${
          burnSubtitle
            ? 'border-blue-400 bg-blue-50/50'
            : 'border-slate-200 hover:bg-slate-50'
        }`}>
          <input
            type="checkbox"
            checked={burnSubtitle}
            onChange={(e) => setBurnSubtitle(e.target.checked)}
            className="mt-0.5 rounded accent-blue-600"
          />
          <div className="flex-1 text-sm">
            <p className="font-semibold text-slate-800">📝Subtitle อัตโนมัติ</p>
            <p className="text-xs text-slate-500 mt-0.5">
              สร้างคำบรรยายจากเสียงพูด ลงในวิดีโอ
            </p>
          </div>
        </label>

        {/* Preview Mode */}
        <label className={`flex items-start gap-3 mt-3 p-3.5 rounded-xl border cursor-pointer transition-all ${
          previewMode ? 'border-violet-400 bg-violet-50/50' : 'border-slate-200 hover:bg-slate-50'
        }`}>
          <input
            type="checkbox"
            checked={previewMode}
            onChange={(e) => setPreviewMode(e.target.checked)}
            className="mt-0.5 rounded accent-violet-600"
          />
          <div className="flex-1 text-sm">
            <p className="font-semibold text-slate-800">
              👁️ Preview ก่อนตัดต่อจริง
              <span className="ml-1 px-1.5 py-0.5 bg-violet-100 text-violet-700 rounded text-[10px] font-medium">NEW</span>
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              ดูช่วงที่ AI เลือกก่อน แล้วยืนยัน/ยกเลิกได้รายช่วง (แม่นยำขึ้น)
            </p>
          </div>
        </label>
      </section>

      {/* Inline error (แทน alert) */}
      {error && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
          <span>⚠️</span>
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')} className="text-red-400 hover:text-red-600" aria-label="ปิด">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Upload progress bar (โชว์ % จริงตอนอัปโหลดไฟล์ใหญ่) */}
      {loading && (
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>{uploadProgress < 100 ? 'กำลังอัปโหลดวิดีโอ...' : 'อัปโหลดเสร็จ — กำลังเริ่มประมวลผล'}</span>
            <span className="font-semibold text-slate-700">{uploadProgress}%</span>
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
            <div
              className="h-2 rounded-full bg-gradient-to-r from-blue-500 to-violet-600 transition-all duration-200"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Submit */}
      <button
        onClick={handleUpload}
        disabled={loading}
        className={`w-full flex items-center justify-center gap-2 py-4 rounded-2xl font-semibold text-white shadow-lg transition-all ${
          loading
            ? 'bg-slate-400 cursor-not-allowed'
            : 'bg-gradient-to-r from-blue-600 to-violet-600 hover:shadow-xl hover:shadow-blue-500/30 active:scale-[0.98]'
        }`}
      >
        {loading ? (
          <>
            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.3" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
            </svg>
            {uploadProgress < 100 ? `กำลังส่งวิดีโอ... ${uploadProgress}%` : 'กำลังเริ่มประมวลผล...'}
          </>
        ) : (
          <>
            <Sparkles className="h-5 w-5" />
            เริ่มประมวลผลด้วย AI
          </>
        )}
      </button>
    </div>
  );
};

export default UploadScreen;
