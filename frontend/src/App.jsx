import React, { useState, useEffect } from 'react';
import { Sparkles, Download, RotateCcw } from 'lucide-react';
import UploadScreen from './components/UploadScreen';
import Processing from './components/Processing';
import PreviewScreen from './components/PreviewScreen';
import SubtitleEditScreen from './components/SubtitleEditScreen';
import { API_URL } from './config';

const STORAGE_KEYS = {
  JOB: 'aive_job_id',
  VIDEO: 'aive_video_url',
  SUMMARY: 'aive_summary',
  PHASE: 'aive_phase',     // "processing" | "preview" | "editing" | "rendering" | "done"
  RENDER_TASK: 'aive_render_task',
  SELECTED_SEGS: 'aive_selected_segments',
};

function App() {
  const [jobId, setJobId] = useState(() => localStorage.getItem(STORAGE_KEYS.JOB));
  const [videoUrl, setVideoUrl] = useState(() => localStorage.getItem(STORAGE_KEYS.VIDEO));
  const [phase, setPhase] = useState(() => localStorage.getItem(STORAGE_KEYS.PHASE) || null);
  const [renderTaskId, setRenderTaskId] = useState(() => localStorage.getItem(STORAGE_KEYS.RENDER_TASK));
  const [selectedSegs, setSelectedSegs] = useState(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEYS.SELECTED_SEGS) || 'null'); }
    catch { return null; }
  });
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

  useEffect(() => {
    if (phase) localStorage.setItem(STORAGE_KEYS.PHASE, phase);
    else localStorage.removeItem(STORAGE_KEYS.PHASE);
  }, [phase]);

  useEffect(() => {
    if (renderTaskId) localStorage.setItem(STORAGE_KEYS.RENDER_TASK, renderTaskId);
    else localStorage.removeItem(STORAGE_KEYS.RENDER_TASK);
  }, [renderTaskId]);

  useEffect(() => {
    if (selectedSegs) localStorage.setItem(STORAGE_KEYS.SELECTED_SEGS, JSON.stringify(selectedSegs));
    else localStorage.removeItem(STORAGE_KEYS.SELECTED_SEGS);
  }, [selectedSegs]);

  // Callback จาก Processing — ทำงาน 2 กรณี
  const handleComplete = (urlOrPreview, summary) => {
    // ถ้า data.mode = "preview" → ไปหน้า preview
    if (urlOrPreview === '__PREVIEW__') {
      setPhase('preview');
      return;
    }
    setVideoUrl(urlOrPreview);
    setPhase('done');
    if (summary) setEditSummary(summary);
  };

  // จาก PreviewScreen — กดยืนยัน render
  const handleRendering = (newTaskId) => {
    setRenderTaskId(newTaskId);
    setPhase('rendering');
  };

  // จาก PreviewScreen — กดถัดไปแก้ subtitle (เมื่อ burn_subtitle=true)
  const handleEditSubtitle = (segments) => {
    setSelectedSegs(segments);
    setPhase('editing');
  };

  const handleBackToPreview = () => {
    setSelectedSegs(null);
    setPhase('preview');
  };

  const handleReset = () => {
    setJobId(null);
    setVideoUrl(null);
    setEditSummary(null);
    setPhase(null);
    setRenderTaskId(null);
    setSelectedSegs(null);
  };

  // Determine active jobId for Processing component
  const activeTaskId = phase === 'rendering' ? renderTaskId : jobId;

  const jobIdShort = videoUrl ? videoUrl.split('/')[0] : '';

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* ── Header ─────────────────────────────────────── */}
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur-md border-b border-slate-200/70">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-xl bg-indigo-600 flex items-center justify-center">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-slate-900 leading-tight">AI Video Smart Editor</h1>
              <p className="text-[10px] text-slate-400 leading-tight">Powered by Whisper</p>
            </div>
          </div>
          {(jobId || videoUrl) && (
            <button
              onClick={handleReset}
              className="text-xs font-medium text-slate-500 hover:text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            >
              ← เริ่มใหม่
            </button>
          )}
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────── */}
      <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-8 sm:py-12">
        {!jobId && !videoUrl && (
          <UploadScreen onUploadSuccess={(id, mode) => {
            setJobId(id);
            setPhase(mode === 'preview' ? 'processing' : 'processing');
          }} />
        )}

        {jobId && !videoUrl && (phase === 'processing' || phase === 'rendering') && (
          <Processing jobId={activeTaskId} onComplete={handleComplete} onCancel={handleReset} />
        )}

        {jobId && !videoUrl && phase === 'preview' && (
          <PreviewScreen
            jobId={jobId}
            onRendering={handleRendering}
            onCancel={handleReset}
            onEditSubtitle={handleEditSubtitle}
          />
        )}

        {jobId && !videoUrl && phase === 'editing' && selectedSegs && (
          <SubtitleEditScreen
            jobId={jobId}
            selectedSegments={selectedSegs}
            onRendering={handleRendering}
            onBack={handleBackToPreview}
          />
        )}

        {videoUrl && (
          <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Success badge */}
            <div className="text-center">
              <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-50 text-emerald-600 rounded-full text-xs font-medium">
                <Sparkles className="h-3.5 w-3.5" />
                ตัดต่อเสร็จสมบูรณ์
              </div>
              <h2 className="text-2xl sm:text-3xl font-semibold text-slate-900 mt-3">วิดีโอของคุณพร้อมแล้ว</h2>
            </div>

            {/* Video Player */}
            <div className="bg-slate-900 rounded-2xl overflow-hidden border border-slate-200">
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
                className="flex-1 flex items-center justify-center gap-2 bg-indigo-600 text-white px-6 py-3.5 rounded-xl font-semibold hover:bg-indigo-700 transition-colors active:scale-[0.98]"
              >
                <Download className="h-5 w-5" />
                ดาวน์โหลดวิดีโอ
              </a>
              <button
                onClick={handleReset}
                className="flex-1 flex items-center justify-center gap-2 bg-white text-slate-700 px-6 py-3.5 rounded-xl font-semibold border border-slate-200 hover:bg-slate-50 transition-colors active:scale-[0.98]"
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
