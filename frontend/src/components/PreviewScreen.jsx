import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import {
  Sparkles, Check, X, Eye, Clock, Sigma, Play, Loader2, AlertTriangle,
} from 'lucide-react';
import { API_URL } from '../config';

const formatTime = (sec) => {
  const s = Math.floor(sec || 0);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
};

const PreviewScreen = ({ jobId, onRendering, onCancel, onEditSubtitle }) => {
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState({});  // {idx: true/false}
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Load preview data
  useEffect(() => {
    let cancelled = false;
    axios.get(`${API_URL}/preview/${jobId}`)
      .then((res) => {
        if (cancelled) return;
        setPreview(res.data);
        // Default: ทุก segment ถูกเลือก
        const init = {};
        res.data.segments.forEach((_, i) => { init[i] = true; });
        setSelected(init);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.response?.data?.detail || 'โหลด preview ไม่สำเร็จ');
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [jobId]);

  const segments = preview?.segments || [];
  const isTiktok = preview?.output_mode === 'tiktok';

  const toggle = (idx) => setSelected((s) => ({ ...s, [idx]: !s[idx] }));
  const selectAll = () => {
    const all = {};
    segments.forEach((_, i) => { all[i] = true; });
    setSelected(all);
  };
  const deselectAll = () => {
    const none = {};
    segments.forEach((_, i) => { none[i] = false; });
    setSelected(none);
  };

  const stats = useMemo(() => {
    let count = 0, dur = 0;
    segments.forEach((s, i) => {
      if (selected[i]) { count++; dur += (s.end - s.start); }
    });
    return { count, dur };
  }, [segments, selected]);

  const handleConfirm = async () => {
    if (stats.count === 0) {
      alert('กรุณาเลือกอย่างน้อย 1 ช่วง');
      return;
    }
    const chosen = segments.filter((_, i) => selected[i]);

    // ถ้า burn_subtitle = true → ไปหน้าแก้ subtitle ก่อน render
    if (preview?.burn_subtitle && onEditSubtitle) {
      onEditSubtitle(chosen);
      return;
    }

    // ไม่มี subtitle → render ทันที
    setSubmitting(true);
    try {
      const res = await axios.post(`${API_URL}/render/${jobId}`, { segments: chosen });
      onRendering(res.data.task_id);
    } catch (err) {
      alert(err.response?.data?.detail || 'Render fail');
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-12 text-center">
        <Loader2 className="h-12 w-12 text-indigo-500 animate-spin mx-auto mb-3" />
        <p className="text-sm text-slate-500">กำลังโหลด preview...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-red-200 p-8 text-center">
        <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-3" />
        <p className="text-sm text-red-700 mb-4">{error}</p>
        <button
          onClick={onCancel}
          className="px-5 py-2.5 bg-slate-100 text-slate-700 rounded-xl font-semibold hover:bg-slate-200"
        >
          กลับ
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Hero */}
      <div className="text-center">
        <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-indigo-50 text-indigo-600 rounded-full text-xs font-medium mb-2">
          <Eye className="h-3.5 w-3.5" />
          Preview
        </div>
        <h2 className="text-2xl font-semibold text-slate-900">
          ตรวจสอบช่วงที่ AI <span className="text-indigo-600">เลือกไว้</span>
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          เลือก / ยกเลิก ช่วงที่ต้องการ — แล้วกด "ตัดต่อ" เพื่อ render
        </p>
      </div>

      {/* Stats card */}
      <div className="bg-white rounded-2xl border border-slate-200 p-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-indigo-50 flex items-center justify-center">
            <Sigma className="h-4 w-4 text-indigo-600" />
          </div>
          <div>
            <p className="text-[10px] text-slate-500 uppercase font-medium">ที่เลือก</p>
            <p className="text-lg font-bold text-slate-800">{stats.count} / {segments.length}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-indigo-50 flex items-center justify-center">
            <Clock className="h-4 w-4 text-indigo-600" />
          </div>
          <div>
            <p className="text-[10px] text-slate-500 uppercase font-medium">ความยาวรวม</p>
            <p className="text-lg font-bold text-slate-800">{formatTime(stats.dur)}</p>
          </div>
        </div>
        <div className="col-span-2 sm:col-span-1 flex items-center gap-2 justify-end">
          <button
            onClick={selectAll}
            className="text-xs px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-md hover:bg-indigo-100 font-medium"
          >
            เลือกทั้งหมด
          </button>
          <button
            onClick={deselectAll}
            className="text-xs px-3 py-1.5 bg-slate-100 text-slate-700 rounded-md hover:bg-slate-200 font-medium"
          >
            ล้าง
          </button>
        </div>
      </div>

      {/* Segments list */}
      <div className="space-y-2 max-h-[55vh] overflow-y-auto pr-1">
        {segments.map((seg, idx) => {
          const isOn = !!selected[idx];
          const duration = seg.end - seg.start;
          return (
            <button
              key={idx}
              type="button"
              onClick={() => toggle(idx)}
              className={`w-full text-left p-3.5 rounded-xl border-2 transition-all ${
                isOn
                  ? 'border-indigo-400 bg-indigo-50/40 shadow-sm'
                  : 'border-slate-200 bg-slate-50/50 opacity-60 hover:opacity-80'
              }`}
            >
              <div className="flex items-start gap-3">
                <div className={`h-6 w-6 rounded-md flex items-center justify-center flex-shrink-0 transition-colors ${
                  isOn ? 'bg-indigo-500 text-white' : 'bg-slate-300 text-slate-500'
                }`}>
                  {isOn ? <Check className="h-4 w-4" /> : <X className="h-3.5 w-3.5" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs text-slate-500 mb-1.5">
                    <span className="font-mono">
                      {formatTime(seg.start)} – {formatTime(seg.end)}
                    </span>
                    <span className="font-semibold text-slate-700">({duration.toFixed(1)}s)</span>
                    {isTiktok && seg.priority && (
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        seg.priority === 1 ? 'bg-indigo-100 text-indigo-700' :
                        seg.priority === 2 ? 'bg-indigo-50 text-indigo-600' :
                        'bg-slate-100 text-slate-600'
                      }`}>
                        {seg.priority === 1 ? '🔥 Hook' : `P${seg.priority}`}
                      </span>
                    )}
                  </div>
                  {seg.text && (
                    <p className={`text-sm leading-relaxed ${isOn ? 'text-slate-800' : 'text-slate-500'}`}>
                      "{seg.text}"
                    </p>
                  )}
                  {seg.reason && (
                    <p className="text-xs text-slate-500 mt-1 italic">💡 {seg.reason}</p>
                  )}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Submit */}
      <div className="space-y-2 pt-2">
        <button
          onClick={handleConfirm}
          disabled={submitting || stats.count === 0}
          className={`w-full flex items-center justify-center gap-2 py-4 rounded-2xl font-semibold text-white shadow-lg transition-all ${
            submitting || stats.count === 0
              ? 'bg-slate-400 cursor-not-allowed'
              : 'bg-indigo-600 hover:bg-indigo-700 active:scale-[0.98]'
          }`}
        >
          {submitting ? (
            <>
              <Loader2 className="h-5 w-5 animate-spin" /> กำลังส่ง render...
            </>
          ) : preview?.burn_subtitle ? (
            <>
              <Play className="h-5 w-5" />
              ถัดไป — แก้ subtitle ({stats.count} ช่วง · {formatTime(stats.dur)})
            </>
          ) : (
            <>
              <Play className="h-5 w-5" />
              ตัดต่อตามที่เลือก ({stats.count} ช่วง · {formatTime(stats.dur)})
            </>
          )}
        </button>
        <button
          onClick={onCancel}
          disabled={submitting}
          className="w-full py-3 rounded-xl font-semibold text-slate-700 bg-white border border-slate-200 hover:bg-slate-50"
        >
          ← ยกเลิก
        </button>
      </div>
    </div>
  );
};

export default PreviewScreen;
