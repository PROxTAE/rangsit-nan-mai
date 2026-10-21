"""① Collector: ดึง snapshot จากกล้อง CCTV ตามรอบเวลา (polling)

ทุกรอบเก็บ 2 ภาพ: ภาพหลัก และภาพถัดไปที่กล้องอัปเดต (กล้องนี้อัปเดต snapshot นาทีละครั้ง)
    data/raw/2026-10-21/113830.jpg     ภาพหลัก
    data/raw/2026-10-21/113830_b.jpg   ภาพถัดไป ~60 วินาทีต่อมา (ใช้วัดว่ารถขยับหรือจอดนิ่ง)

ใช้งาน:
    python collector.py                    # วนทุก 5 นาทีไปเรื่อย ๆ (Ctrl+C เพื่อหยุด)
    python collector.py --interval 60 --count 10
    python collector.py --no-pair          # ไม่ถ่ายภาพที่สอง
"""
import argparse
import datetime
import hashlib
import time
import os

import requests

import config

HEADERS = {"User-Agent": "Mozilla/5.0 (ImageProcessing MiniProject collector)"}


def fetch_snapshot(session, retries=3):
    """คืน bytes ของภาพ JPEG หรือ None ถ้าดึงไม่สำเร็จ"""
    for attempt in range(retries):
        try:
            r = session.get(config.SNAPSHOT_URL, timeout=20, headers=HEADERS)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
                return r.content
            print(f"  ! status={r.status_code} type={r.headers.get('Content-Type')}")
        except requests.RequestException as e:
            print(f"  ! error: {e}")
        time.sleep(3 * (attempt + 1))
    return None


def wait_next_snapshot(session, current_hash):
    """รอจนกล้องอัปเดตภาพใหม่ (hash เปลี่ยน) แล้วคืนภาพนั้น หรือ None ถ้าเกินเวลา"""
    deadline = time.time() + config.PAIR_TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(config.PAIR_POLL_SEC)
        content = fetch_snapshot(session, retries=1)
        if content is not None and hashlib.md5(content).hexdigest() != current_hash:
            return content
    return None


def save(content, now, suffix=""):
    folder = config.RAW_DIR / f"{now:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now:%H%M%S}{suffix}.jpg"
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(content)
    os.replace(temporary, path)
    return path


def collect_once(session, last_hash=None, no_pair=False, on_phase=None):
    """Collect one ready-to-analyze frame. Shared by CLI and the dashboard worker."""
    notify = on_phase or (lambda phase: None)
    notify('fetching')
    content = fetch_snapshot(session)
    if content is None:
        return {'status': 'fetch_error', 'path': None, 'last_hash': last_hash}
    current_hash = hashlib.md5(content).hexdigest()
    if current_hash == last_hash:
        return {'status': 'duplicate', 'path': None, 'last_hash': last_hash}
    now = datetime.datetime.now()
    path = save(content, now)
    if not no_pair:
        notify('waiting_pair')
        content_b = wait_next_snapshot(session, current_hash)
        if content_b is not None:
            save(content_b, now, '_b')
            current_hash = hashlib.md5(content_b).hexdigest()
    return {'status': 'ready', 'path': path, 'last_hash': current_hash}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=config.INTERVAL_SEC, help="วินาทีต่อรอบ")
    ap.add_argument("--count", type=int, default=0, help="จำนวนรอบ (0 = ไม่จำกัด)")
    ap.add_argument("--no-pair", action="store_true", help="ไม่ถ่ายภาพที่สองสำหรับวัดการเคลื่อนไหว")
    args = ap.parse_args()

    session = requests.Session()
    last_hash = None
    n = 0
    print(f"เริ่มเก็บภาพทุก {args.interval} วินาที -> {config.RAW_DIR}")
    while True:
        start = time.time()
        result = collect_once(session, last_hash, args.no_pair)
        last_hash = result['last_hash']
        print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {result['status']} {result['path'] or ''}")
        n += 1
        if args.count and n >= args.count:
            break
        time.sleep(max(0, args.interval - (time.time() - start)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nหยุดเก็บภาพแล้ว")
