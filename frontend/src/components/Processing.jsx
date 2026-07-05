import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { CheckCircle, Loader2, Clock, AlertTriangle, FileAudio, Brain, Film } from 'lucide-react';
import { API_URL } from '../config';

const STATUS_TRANSLATIONS = {
  PENDING: '🕐 รอคิวประมวลผล',
  RECEIVED: '📥 ได้รับงาน',
  STARTED: '🚀 กำลังเริ่ม',
  RETRY: '🔄 กำลังลองใหม่',
  REVOKED: '🛑 ยกเลิก',
};

const STEPS = [
  { id: 1, label: 'แยกเสียง',     icon: FileAudio,  progressMin: 0,  progressMax: 24 },
  { id: 2, label: 'ตรวจเสียงพูด',  icon: Loader2,    progressMin: 25, progressMax: 44 },
  { id: 3, label: 'AI วิเคราะห์',  icon: Brain,      progressMin: 45, progressMax: 79 },
  { id: 4, label: 'ตัด & รวม',     icon: Film,       progressMin: 80, progressMax: 100 },
];

// เพดานเวลา polling — งานที่ค้างนานเกินนี้ (เช่น worker ตาย) จะเลิก poll แล้วโชว์ error
const MAX_POLL_MS = 60 * 60 * 1000;   // 60 นาที
// ยอมแพ้ถ้าเชื่อมต่อ backend ไม่ได้ติดต่อกันเกินจำนวนนี้ (≈ 20 × 3s = 60s)
const MAX_CONSECUTIVE_ERRORS = 20;

const Processing = ({ jobId, onComplete, onCancel }) => {
  const [status, setStatus] = useState('PENDING');
  const [message, setMessage] = useState('🕐 รอคิวประมวลผล');
  const [progress, setProgress] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);
  const intervalRef = useRef(null);
  const startTimeRef = useRef(Date.now());
  const onCompleteRef = useRef(onComplete);

  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);

  const stopPolling = () => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = null;
  };

  // Elapsed timer
  useEffect(() => {
    const t = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let errorCount = 0;
    const checkStatus = async () => {
      if (cancelled) return;

      // เพดานเวลา — งานค้างนานผิดปกติ (worker ตาย/task ค้าง PENDING) → เลิก poll
      if (Date.now() - startTimeRef.current > MAX_POLL_MS) {
        stopPolling();
        setStatus('FAILURE');
        setMessage('ใช้เวลานานผิดปกติ — งานอาจค้าง กรุณาลองใหม่');
        return;
      }

      try {
        const { data } = await axios.get(`${API_URL}/status/${jobId}`);
        if (cancelled) return;
        errorCount = 0;   // ติดต่อสำเร็จ → reset ตัวนับ error

        if (data.status === 'SUCCESS') {
          stopPolling();
          setStatus('SUCCESS');
          setProgress(100);
          const mode = data.result?.mode;
          if (mode === 'preview') {
            // Preview mode: ไม่ render — แจ้ง parent ไปหน้า preview
            setMessage('✨ วิเคราะห์เสร็จ — เปิด preview');
            onCompleteRef.current('__PREVIEW__');
          } else {
            // Final render เสร็จ
            setMessage('✨ ตัดต่อเสร็จเรียบร้อยแล้ว!');
            const finalUrl = data.result?.output_url;
            if (finalUrl) {
              onCompleteRef.current(finalUrl, data.result?.edit_summary);
            }
          }
        } else if (data.status === 'FAILURE') {
          stopPolling();
          setStatus('FAILURE');
          setMessage('เกิดข้อผิดพลาด: ' + (data.result || 'AI ไม่สามารถประมวลผลได้'));
        } else {
          setStatus('PROGRESS');
          const raw = data.status || 'กำลังประมวลผล...';
          setMessage(STATUS_TRANSLATIONS[raw] || raw);
          setProgress(data.progress || 0);
        }
      } catch (err) {
        if (cancelled) return;
        console.error('Polling error', err);
        errorCount += 1;
        // เชื่อมต่อ backend ไม่ได้ติดต่อกันหลายครั้ง → เลิก poll แล้วแจ้ง error
        if (errorCount >= MAX_CONSECUTIVE_ERRORS) {
          stopPolling();
          setStatus('FAILURE');
          setMessage('เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ — กรุณาตรวจสอบการเชื่อมต่อแล้วลองใหม่');
        }
      }
    };
    checkStatus();
    intervalRef.current = setInterval(checkStatus, 3000);
    return () => { cancelled = true; stopPolling(); };
  }, [jobId]);

  const formatTime = (sec) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  const currentStepIdx = STEPS.findIndex(s => progress >= s.progressMin && progress <= s.progressMax);
  const safeStepIdx = currentStepIdx === -1 ? 0 : currentStepIdx;
  const isFailure = status === 'FAILURE';
  const isSuccess = status === 'SUCCESS';
  const isPending = message.startsWith('🕐') || status === 'PENDING';

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Status hero */}
      <div className="bg-white rounded-2xl shadow-sm shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <div className="p-6 sm:p-8 text-center">
          {/* Icon */}
          <div className="relative inline-flex mb-4">
            <div className={`absolute inset-0 rounded-full blur-2xl opacity-30 ${
              isSuccess ? 'bg-emerald-400' :
              isFailure ? 'bg-red-400' :
              isPending ? 'bg-amber-400' : 'bg-blue-500'
            }`} />
            <div className={`relative h-16 w-16 rounded-2xl flex items-center justify-center shadow-lg ${
              isSuccess ? 'bg-gradient-to-br from-emerald-400 to-green-500 shadow-emerald-500/40' :
              isFailure ? 'bg-gradient-to-br from-red-400 to-rose-500 shadow-red-500/40' :
              isPending ? 'bg-gradient-to-br from-amber-400 to-orange-500 shadow-amber-500/40' :
              'bg-gradient-to-br from-blue-500 to-violet-600 shadow-blue-500/40'
            }`}>
              {isSuccess ? <CheckCircle className="h-8 w-8 text-white" /> :
                isFailure ? <AlertTriangle className="h-8 w-8 text-white" /> :
                isPending ? <Clock className="h-8 w-8 text-white animate-pulse" /> :
                <Loader2 className="h-8 w-8 text-white animate-spin" />}
            </div>
          </div>

          {/* Title */}
          <h2 className="text-xl sm:text-2xl font-bold text-slate-800">
            {isSuccess ? 'สำเร็จ!' :
              isFailure ? 'เกิดข้อผิดพลาด' :
              isPending ? 'รอคิวประมวลผล' : 'กำลังประมวลผล'}
          </h2>
          <p className="text-sm text-slate-500 mt-1.5 max-w-md mx-auto">{message}</p>

          {/* Progress bar */}
          <div className="mt-5">
            <div className="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
              <div
                className={`h-2.5 rounded-full transition-all duration-700 ease-out ${
                  isSuccess ? 'bg-gradient-to-r from-emerald-400 to-green-500' :
                  isFailure ? 'bg-gradient-to-r from-red-400 to-rose-500' :
                  isPending ? 'bg-gradient-to-r from-amber-300 to-amber-400' :
                  'bg-gradient-to-r from-blue-500 to-violet-600'
                }`}
                style={{ width: `${Math.max(progress, isPending ? 5 : 0)}%` }}
              />
            </div>
            <div className="flex items-center justify-between mt-2 text-xs">
              <span className="text-slate-400 font-mono">ID: {jobId.substring(0, 8)}</span>
              <span className="font-bold text-slate-700">{progress}% · ⏱️ {formatTime(elapsedSec)}</span>
            </div>
          </div>
        </div>

        {/* Step indicator */}
        {!isFailure && !isSuccess && (
          <div className="border-t border-slate-100 px-6 py-4 bg-slate-50/50">
            <div className="grid grid-cols-4 gap-2">
              {STEPS.map((s, idx) => {
                const Icon = s.icon;
                const isDone = idx < safeStepIdx;
                const isCurrent = idx === safeStepIdx;
                return (
                  <div key={s.id} className="flex flex-col items-center text-center">
                    <div className={`h-8 w-8 rounded-full flex items-center justify-center text-[10px] font-bold transition-all ${
                      isDone ? 'bg-emerald-500 text-white' :
                      isCurrent ? 'bg-blue-500 text-white ring-4 ring-blue-100' :
                      'bg-slate-200 text-slate-400'
                    }`}>
                      {isDone ? '✓' : <Icon className={`h-3.5 w-3.5 ${isCurrent && Icon === Loader2 ? 'animate-spin' : ''}`} />}
                    </div>
                    <p className={`text-[10px] mt-1 leading-tight ${
                      isCurrent ? 'font-semibold text-blue-700' :
                      isDone ? 'text-emerald-600' : 'text-slate-400'
                    }`}>
                      {s.label}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Warning */}
      {!isFailure && !isSuccess && (
        <div className="bg-amber-50/50 border border-amber-200 rounded-xl p-3 text-xs text-amber-800">
          ⚠️ <strong>ห้าม refresh</strong> หน้านี้ — วิดีโอ 20 นาที ใช้เวลา 5-10 นาที ระบบจะแสดงผลให้อัตโนมัติ
        </div>
      )}

      {/* Cancel/Reset button */}
      {(status === 'PROGRESS' || status === 'PENDING' || isFailure) && onCancel && (
        <button
          onClick={onCancel}
          className="w-full py-3 rounded-xl font-semibold text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 hover:border-slate-300 transition-all"
        >
          {isFailure ? '← กลับไปหน้าแรก' : '✕ ยกเลิก / กลับไปอัปโหลดใหม่'}
        </button>
      )}
    </div>
  );
};

export default Processing;
