import React, { useState, useEffect } from 'react';
import {
  Upload, MessageSquare, X, MicOff, BookOpen, Scissors, GraduationCap,
  Mic, Briefcase, Gamepad2, Smartphone, Edit, FileVideo, Zap, Sparkles,
} from 'lucide-react';
import axios from 'axios';
import { API_URL } from '../config';

const PROMPT_PRESETS = [
  { id: 'silence',  icon: MicOff,          label: 'ตัดความเงียบ',  desc: 'ลบ filler + pause',
    text: 'ตัดเฉพาะช่วงเงียบ ช่วง filler (อืม เอ่อ อ่อ) และช่วงพูดซ้ำที่ไม่เพิ่มข้อมูลใหม่ออก เก็บเนื้อหาที่ผู้พูดอธิบายไว้ทั้งหมด' },
  { id: 'essence',  icon: BookOpen,        label: 'เก็บสาระสำคัญ', desc: 'concept + ตัวอย่าง',
    text: 'เก็บเฉพาะเนื้อหาสาระสำคัญ — แนวคิด/หลักการ คำอธิบาย ตัวอย่าง ตัดเรื่องนอกประเด็น เรื่องเล่าส่วนตัวที่ไม่เกี่ยวข้องออก' },
  { id: 'shortest', icon: Scissors,        label: 'สรุปสั้นที่สุด', desc: 'key points only',
    text: 'ตัดให้สั้นที่สุด เก็บเฉพาะ key points สำคัญที่สุด ตัด background, context, ตัวอย่างซ้ำซ้อนและส่วนปลีกย่อยออก' },
  { id: 'tutorial', icon: GraduationCap,   label: 'วิดีโอสอน',     desc: 'step-by-step',
    text: 'วิดีโอสอน — เก็บการอธิบาย concept และตัวอย่าง/practical steps ตัด rambling นอกเรื่อง และช่วงที่ผู้สอนสับสน/แก้ความผิดพลาดออก' },
  { id: 'podcast',  icon: Mic,             label: 'Podcast/สัมภาษณ์', desc: 'แก่นการสนทนา',
    text: 'Podcast/Interview — ตัด small talk ช่วงต้น การทบทวนตอนท้าย และช่วงที่หัวข้อหลุดออกจากประเด็นหลัก เก็บแก่นการสนทนา' },
  { id: 'meeting',  icon: Briefcase,       label: 'ประชุม', desc: 'decisions + actions',
    text: 'สรุปการประชุม — เก็บการตัดสินใจ, action items, deadline และประเด็นที่ผู้เข้าร่วมเสนอ ตัด chitchat, การรอระหว่างประชุม และช่วงปัญหาเทคนิค (เสียงหาย รอ load หน้าจอ)' },
  { id: 'gaming',   icon: Gamepad2,        label: 'Live/Gaming',  desc: 'highlight only',
    text: 'Live stream/Gaming — เก็บช่วง action สำคัญ (kill, win, react ตลก, milestone) ตัดช่วงเดินทาง, รอ respawn, AFK, loading screen และ chat ที่ไม่เกี่ยวกับ gameplay' },
  { id: 'tiktok',   icon: Smartphone,      label: 'TikTok/Reels', desc: '9:16 vertical',
    text: 'เลือก peak moment ที่ดีที่สุดมาเป็นคลิปสั้น มี hook ใน 3 วินาทีแรก ตามด้วยเนื้อหาน่าตื่นเต้น/ตลก/น่าจดจำ เน้นความ engaging ของ short-form video' },
  { id: 'custom',   icon: Edit,            label: 'Custom', desc: 'พิมพ์เอง',
    text: '' },
];

const UploadScreen = ({ onUploadSuccess }) => {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [activePresetId, setActivePresetId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [targetLength, setTargetLength] = useState(60);
  const [burnSubtitle, setBurnSubtitle] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);

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
      alert('กรุณาเลือกไฟล์วิดีโอเท่านั้น');
      return;
    }
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
    if (!file) return alert('กรุณาเลือกไฟล์วิดีโอก่อน');
    if (!prompt.trim()) return alert('กรุณาเลือก preset หรือพิมพ์คำสั่งให้ AI');

    setLoading(true);
    const formData = new FormData();
    formData.append('video', file);
    formData.append('prompt', prompt);
    formData.append('output_mode', isTiktokMode ? 'tiktok' : 'standard');
    formData.append('target_length', String(targetLength));
    formData.append('burn_subtitle', String(burnSubtitle));

    const uploadUrl = `${API_URL}/upload`;
    try {
      const response = await axios.post(uploadUrl, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 30 * 60 * 1000,
        onUploadProgress: (e) => {
          const total = e.total || file.size;
          const percent = Math.round((e.loaded * 100) / total);
          console.log(`⏳ Upload: ${percent}%`);
        },
      });
      onUploadSuccess(response.data.job_id);
    } catch (error) {
      let msg = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้';
      if (error.response) {
        try {
          msg = error.response.data?.detail || `Server Error ${error.response.status}`;
        } catch { msg = `Server Error ${error.response.status}`; }
      } else if (error.code === 'ECONNABORTED') {
        msg = 'ใช้เวลานานเกินไป (Timeout)';
      }
      alert(msg);
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
      </section>

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
            กำลังส่งวิดีโอ...
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
