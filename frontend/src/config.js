// API URL configuration
// Priority:
//   1. VITE_API_URL build-time env (สำหรับ deploy production)
//   2. window.location.origin + :8000 (ทำงานทั้ง localhost และ remote)
//   3. fallback localhost
const ENV_URL = import.meta.env?.VITE_API_URL;
const ORIGIN = typeof window !== "undefined" ? window.location.origin : "";

let inferredBackend = "http://127.0.0.1:8000";
if (ENV_URL) {
  inferredBackend = ENV_URL.replace(/\/$/, "");
} else if (ORIGIN) {
  try {
    const u = new URL(ORIGIN);
    // ถ้า frontend ถูก serve ที่ port 80 หรือ origin → ลอง :8000 ของ host เดียวกัน
    inferredBackend = `${u.protocol}//${u.hostname}:8000`;
  } catch {
    /* keep fallback */
  }
}

export const API_URL = inferredBackend;
