"""Observability — Sentry error tracking (เปิด/ปิดผ่าน env SENTRY_DSN)."""
import os


def init_sentry(component: str) -> None:
    """
    เปิด Sentry เฉพาะเมื่อ SENTRY_DSN ถูกตั้งค่า (ไม่งั้น no-op สำหรับ dev)
    เรียกครั้งเดียวต่อ process — FastAPI (backend) และ Celery (worker) แยกกัน
    integration ของ FastAPI/Celery เปิดอัตโนมัติเมื่อ sentry-sdk เจอ framework ติดตั้งอยู่
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("SENTRY_ENV", "production"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            # PDPA: ไม่ส่ง PII (IP / headers / request body) ไป Sentry
            send_default_pii=False,
        )
        sentry_sdk.set_tag("component", component)
        print(f"🛰️ Sentry enabled ({component})")
    except Exception as e:
        print(f"⚠️ Sentry init failed: {e}")
