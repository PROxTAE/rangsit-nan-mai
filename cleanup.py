"""⑨ Cleanup: ลบไฟล์ภาพเก่าเพื่อคุมพื้นที่ดิสก์ (ตัวเลขผลวิเคราะห์ใน DB เก็บไว้ตลอด)

ลบ 3 อย่าง:
    1) โฟลเดอร์ภาพดิบ data/raw/YYYY-MM-DD ที่เก่ากว่า RAW_KEEP_DAYS        -> ล้าง raw_path, pair_path ใน DB
    2) โฟลเดอร์ overlay/thumbnail รายวันที่เก่ากว่า MEDIA_KEEP_DAYS        -> ล้าง overlay_path, thumbnail_path
    3) overlay/thumbnail กำพร้า: ไฟล์ *-web-* ที่ DB ไม่อ้างถึงแล้ว (เกิดจากการประมวลผลซ้ำ)
       และไฟล์ .tmp ที่ค้างจากการเขียนไม่สำเร็จ

ล้าง path ใน DB ก่อนแล้วค่อยลบไฟล์ แดชบอร์ดจะไม่ชี้ไปยังไฟล์ที่หายไปแล้ว

ใช้งาน:
    python cleanup.py --dry-run     # ดูว่าจะลบอะไร โดยไม่ลบจริง
    python cleanup.py               # ลบจริง (ตั้ง cron ให้รันวันละครั้ง)
"""
import argparse
import datetime as dt
import shutil
import time

import config
from web import db


def day_folders(base, cutoff):
    """โฟลเดอร์รายวัน (ชื่อ YYYY-MM-DD) ที่วันที่ก่อน cutoff"""
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.iterdir()):
        try:
            day = dt.date.fromisoformat(p.name)
        except ValueError:
            continue
        if p.is_dir() and day < cutoff:
            out.append(p)
    return out


def size_of(paths):
    total = 0
    for p in paths:
        files = [p] if p.is_file() else p.rglob("*")
        total += sum(f.stat().st_size for f in files if f.is_file())
    return total


def clear_paths(conn, columns, folders, dry_run):
    """ล้าง path ใน DB ที่ชี้เข้าไปในโฟลเดอร์ที่จะลบ คืน id ของเฟรมที่เปลี่ยน"""
    ids = set()
    for folder in folders:
        prefix = folder.relative_to(config.ROOT).as_posix() + "/"
        for col in columns:
            # substr แทน LIKE เพราะ _ และ % ใน path จะกลายเป็น wildcard
            where = f"substr({col}, 1, ?) = ?"
            ids.update(r[0] for r in conn.execute(f"SELECT id FROM frames WHERE {where}", (len(prefix), prefix)))
            if not dry_run:
                conn.execute(f"UPDATE frames SET {col} = NULL WHERE {where}", (len(prefix), prefix))
    return ids


def orphan_files(conn, now):
    """overlay/thumbnail ที่ worker สร้าง (*-web-*) หรือ .tmp ค้าง ที่ไม่มีเฟรมไหนอ้างถึง

    ต้องเก่ากว่า ORPHAN_GRACE_SEC: worker เขียนไฟล์ก่อนแล้วค่อยบันทึก path ลง DB
    ไฟล์ HHMMSS.jpg จาก analyze.py ไม่ถูกนับ เพราะไม่ได้ผูกกับ DB
    """
    referenced = set()
    for overlay, thumb in conn.execute("SELECT overlay_path, thumbnail_path FROM frames"):
        referenced.update(p for p in (overlay, thumb) if p)
    out = []
    for base in (config.OVERLAY_DIR, config.THUMB_DIR):
        if not base.is_dir():
            continue
        for f in base.glob("*/*.jpg"):
            generated = "-web-" in f.name or ".tmp" in f.name
            if (generated and f.relative_to(config.ROOT).as_posix() not in referenced
                    and now - f.stat().st_mtime > config.ORPHAN_GRACE_SEC):
                out.append(f)
    return out


def cleanup(today=None, now=None, dry_run=False):
    today = today or dt.date.today()
    now = now if now is not None else time.time()
    raw_old = day_folders(config.RAW_DIR, today - dt.timedelta(days=config.RAW_KEEP_DAYS))
    media_cutoff = today - dt.timedelta(days=config.MEDIA_KEEP_DAYS)
    media_old = day_folders(config.OVERLAY_DIR, media_cutoff) + day_folders(config.THUMB_DIR, media_cutoff)

    db.init_db()
    with db.connect() as conn, conn:
        conn.execute("BEGIN IMMEDIATE")      # กัน worker บันทึกเฟรมใหม่ระหว่างที่อ่านรายการ path
        ids = (clear_paths(conn, ["raw_path", "pair_path"], raw_old, dry_run)
               | clear_paths(conn, ["overlay_path", "thumbnail_path"], media_old, dry_run))
        doomed = set(media_old)
        orphans = [f for f in orphan_files(conn, now) if f.parent not in doomed]
        if ids and not dry_run:
            # version ใหม่ = URL ภาพใหม่ แดชบอร์ดที่เปิดอยู่จะโหลดข้อมูลใหม่ผ่าน data_revision
            conn.executemany("UPDATE frames SET version = version + 1 WHERE id = ?", [(i,) for i in ids])
            conn.execute("UPDATE system_state SET data_revision = data_revision + 1 WHERE id = 1")

    summary = {
        "raw_folders": [p.name for p in raw_old],
        "media_folders": sorted({p.name for p in media_old}),
        "orphans": len(orphans),
        "frames_updated": len(ids),
        "bytes": size_of(raw_old + media_old + orphans),
        "dry_run": dry_run,
    }
    if not dry_run:
        for folder in raw_old + media_old:
            shutil.rmtree(folder, ignore_errors=True)    # ลบไม่หมด (ไฟล์ถูกล็อก) จะลองใหม่รอบหน้า
        for f in orphans:
            f.unlink(missing_ok=True)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="แสดงสิ่งที่จะลบ โดยไม่ลบจริง")
    args = ap.parse_args()
    s = cleanup(dry_run=args.dry_run)
    verb = "จะลบ" if s["dry_run"] else "ลบแล้ว"
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {verb}"
          f"  ภาพดิบ {len(s['raw_folders'])} วัน {s['raw_folders'] or ''}"
          f"  overlay/thumbnail {len(s['media_folders'])} วัน {s['media_folders'] or ''}"
          f"  กำพร้า {s['orphans']} ไฟล์"
          f"  ({s['bytes'] / 1e6:.1f} MB, ล้าง path {s['frames_updated']} เฟรม)")
    disk = shutil.disk_usage(config.ROOT)
    print(f"ดิสก์ว่าง {disk.free / 1e9:.1f} GB จาก {disk.total / 1e9:.1f} GB")


if __name__ == "__main__":
    main()
