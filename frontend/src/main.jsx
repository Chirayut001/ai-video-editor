import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx' // ตรวจสอบว่าบรรทัดนี้มีอยู่ และไฟล์ App.jsx อยู่ที่โฟลเดอร์เดียวกัน
import './index.css'
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)