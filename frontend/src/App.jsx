import React, { useState, useEffect } from 'react';
import { Sparkles, Download, RotateCcw } from 'lucide-react';
import UploadScreen from './components/UploadScreen';
import Processing from './components/Processing';
import { API_URL } from './config';

const STORAGE_KEYS = { JOB: 'aive_job_id', VIDEO: 'aive_video_url', SUMMARY: 'aive_summary' };

function App() {
  const [jobId, setJobId] = useState(() => localStorage.getItem(STORAGE_KEYS.JOB));
  const [videoUrl, setVideoUrl] = useState(() => localStorage.getItem(STORAGE_KEYS.VIDEO));
  const [editSummary, setEditSummary] = useState(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEYS.SUMMARY) || 'null'); }
    catch { return null; }
  });

  useEffect(() => {
    if (jobId) localStorage.setItem(STORAGE_KEYS.JOB, jobId);
    else localStorage.removeItem(STORAGE_KEYS.JOB);
  }, [jobId]);

  useEffect(() => {
    if (videoUrl) localStorage.setItem(STORAGE_KEYS.VIDEO, videoUrl);
    else localStorage.removeItem(STORAGE_KEYS.VIDEO);
  }, [videoUrl]);

  useEffect(() => {
    if (editSummary) localStorage.setItem(STORAGE_KEYS.SUMMARY, JSON.stringify(editSummary));
    else localStorage.removeItem(STORAGE_KEYS.SUMMARY);
  }, [editSummary]);

  const handleComplete = (url, summary) => {
    setVideoUrl(url);
    if (summary) setEditSummary(summary);
  };

  const handleReset = () => {
    setJobId(null);
    setVideoUrl(null);
    setEditSummary(null);
  };

  const jobIdShort = videoUrl ? videoUrl.split('/')[0] : '';

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/30 to-violet-50/30 flex flex-col">
      {/* ── Header ─────────────────────────────────────── */}
      <header className="sticky top-0 z-10 bg-white/70 backdrop-blur-md border-b border-slate-200/60">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-blue-500 to-violet-500 flex items-center justify-center shadow-md shadow-blue-500/30">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-base font-bold text-slate-800 leading-tight">AI Video Smart Editor</h1>
              <p className="text-[10px] text-slate-500 leading-tight">Powered by Whisper</p>
            </div>
          </div>
          {(jobId || videoUrl) && (
            <button
              onClick={handleReset}
              className="text-xs font-medium text-slate-600 hover:text-blue-600 px-3 py-1.5 rounded-lg hover:bg-blue-50 transition-colors"
            >
              ← เริ่มใหม่
            </button>
          )}
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────── */}
      <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-8 sm:py-12">
        {!jobId && !videoUrl && (
          <UploadScreen onUploadSuccess={(id) => setJobId(id)} />
        )}

        {jobId && !videoUrl && (
          <Processing jobId={jobId} onComplete={handleComplete} onCancel={handleReset} />
        )}

        {videoUrl && (
          <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Success badge */}
            <div className="text-center">
              <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-100 text-emerald-700 rounded-full text-xs font-semibold">
                <Sparkles className="h-3.5 w-3.5" />
                ตัดต่อเสร็จสมบูรณ์
              </div>
              <h2 className="text-2xl sm:text-3xl font-bold text-slate-800 mt-3">วิดีโอของคุณพร้อมแล้ว ✨</h2>
            </div>

            {/* Video Player */}
            <div className="bg-slate-900 rounded-2xl overflow-hidden shadow-2xl shadow-slate-200 ring-1 ring-slate-200">
              <div className="flex justify-center max-h-[70vh]">
                <video
                  src={`${API_URL}/storage/${videoUrl}`}
                  controls
                  autoPlay
                  className="max-h-[70vh] max-w-full object-contain"
                />
              </div>
            </div>

            {/* Actions */}
            <div className="flex flex-col sm:flex-row gap-3 pt-2">
              <a
                href={`${API_URL}/download/${jobIdShort}`}
                download="ai_edited_video.mp4"
                className="flex-1 flex items-center justify-center gap-2 bg-gradient-to-r from-blue-600 to-violet-600 text-white px-6 py-3.5 rounded-xl font-semibold hover:shadow-lg hover:shadow-blue-500/30 transition-all active:scale-[0.98]"
              >
                <Download className="h-5 w-5" />
                ดาวน์โหลดวิดีโอ
              </a>
              <button
                onClick={handleReset}
                className="flex-1 flex items-center justify-center gap-2 bg-white text-slate-700 px-6 py-3.5 rounded-xl font-semibold border border-slate-200 hover:bg-slate-50 hover:border-slate-300 transition-all active:scale-[0.98]"
              >
                <RotateCcw className="h-5 w-5" />
                ตัดต่อใหม่
              </button>
            </div>
          </div>
        )}
      </main>

      {/* ── Footer ─────────────────────────────────────── */}
      <footer className="border-t border-slate-200/60 mt-8">
        <div className="max-w-5xl mx-auto px-4 py-4 text-center text-xs text-slate-500">
          © 2026 AI Video Smart Editor · ระบบสรุปเนื้อหาวิดีโออัจฉริยะด้วยปัญญาประดิษฐ์
        </div>
      </footer>
    </div>
  );
}

export default App;
