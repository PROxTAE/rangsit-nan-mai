# สรุปข้อมูลที่เก็บได้ — Rangsit Nan Mai (ปิดระบบบน VPS 22 พ.ย. 2569)

ระบบรันบน Hostinger VPS (`srv2124156`, Docker Compose ที่ `/opt/rangsitnanmai`) ดึงภาพจากกล้อง CCTV
ประตูเมืองรังสิต (ipcamlive alias `6695edb811079`) ทุก ~60 วินาที วิเคราะห์ความหนาแน่นรถแยกฝั่งซ้าย/ขวา
แล้วบันทึกลง SQLite แสดงผลที่ `https://rangsitnanmai.tntstudio.tech`

ปิด worker เมื่อ **2026-11-22 21:02 (เวลาไทย)** แล้วดึง DB สุดท้ายมาเก็บไว้ในโฟลเดอร์นี้ก่อนลบออกจาก VPS

**สถานะ: ลบออกจาก VPS แล้ว (22 พ.ย. 2569)** — container/image/โฟลเดอร์ `/opt/rangsitnanmai`, บล็อกใน Caddyfile
(สำรองไว้ที่ `/opt/caddy/Caddyfile.bak-before-rangsit-removal`) และ DNS `rangsitnanmai.tntstudio.tech`
ส่วน network `rangsitnanmai-ingress` ยังอยู่เพราะ Caddy / qplay / rodmairod ใช้ร่วม

## ระยะเวลาที่เก็บ

| รายการ | ค่า |
|---|---|
| เฟรมแรก | 2026-10-21 15:40:16 |
| เฟรมสุดท้าย | 2026-11-22 21:02:45 |
| ช่วงเวลา | **32.2 วัน** (มีข้อมูล 33 วันปฏิทิน) |
| จำนวนเฟรม (ภาพที่วิเคราะห์) | **43,586** |
| จำนวนแถวผลวัด (เฟรม × 2 ฝั่ง) | **87,172** |
| ความครอบคลุม | ~93.9% ของนาทีทั้งหมด (วันเต็มได้ ~1,400–1,420 จาก 1,440 เฟรม) |
| เฟรมกลางคืน | 21,373 (49%) |

ช่วงที่ข้อมูลขาดเยอะ: 22 ต.ค. (ขาด ~7.9 ชม. ช่วงย้ายขึ้น VPS), 24 ต.ค. และ 7 พ.ย. (กล้องหลุดหลายช่วง)
ดูจำนวนเฟรมรายวันได้ใน `daily_summary.csv`

## ไฟล์ในโฟลเดอร์นี้ (อยู่ใน GitHub)

| ไฟล์ | เนื้อหา |
|---|---|
| `traffic.db.gz` | SQLite ฉบับเต็มตอนปิดระบบ (gzip, คลายแล้ว 50.2 MB) sha256 ของ `.gz` = `55d20425…d609123` |
| `measurements.csv.gz` | ผลวัดทุกแถว 87,172 แถว (export จาก view `results`) เปิดด้วย pandas ได้ตรง ๆ |
| `daily_summary.csv` | สรุปรายวัน: จำนวนเฟรม, เฟรมกลางคืน, occupancy เฉลี่ยแต่ละฝั่ง, จำนวนครั้งที่ติดขัด |
| `hourly_profile.csv` | โปรไฟล์รายชั่วโมง (รวมทุกวัน) แยกฝั่ง: occupancy เฉลี่ย และ % clear/moderate/congested |

คลาย DB: `python -c "import gzip,shutil;shutil.copyfileobj(gzip.open('traffic.db.gz'),open('traffic.db','wb'))"`
แล้วชี้แดชบอร์ดไปที่ไฟล์ด้วย `TRAFFIC_DB_PATH=<path>/traffic.db`

## โครงสร้างข้อมูลใน DB

- `frames` — 1 แถวต่อภาพ: `timestamp`, `captured_at` (unix), path ของภาพดิบ/overlay/thumbnail,
  `brightness`, `contrast`, `is_night`, `threshold`, การขยับของกล้อง (`cam_dx`, `cam_dy`, `cam_score`),
  `processing_config` (JSON พารามิเตอร์ที่ใช้ตอนวิเคราะห์ — มี 3 ชุด เปลี่ยนเฉพาะวันแรก 21 ต.ค.)
- `lane_measurements` — 2 แถวต่อภาพ (`left`/`right`): `occupancy` (สัดส่วนพื้นถนนที่มีรถ 0–1),
  `occupancy_raw`, `occ_far`/`occ_mid`/`occ_near` (แบ่ง 3 ช่วงระยะ), `occupancy_kmeans`, `edge_density`,
  `motion`, `level` (`clear` < 0.20 ≤ `moderate` < 0.45 ≤ `congested`)
- `results` (view) — join สองตารางข้างบน
- `system_state` — สถานะ worker ล่าสุด (`phase = stopped`)

## ภาพรวมผล

| ระดับ | สัดส่วน (ทั้งสองฝั่ง) |
|---|---|
| clear | 44.5% |
| moderate | 36.3% |
| congested | 19.2% |

| ฝั่ง | occupancy เฉลี่ย | ติดขัด (วันธรรมดา) | ติดขัด (เสาร์-อาทิตย์) |
|---|---|---|---|
| ซ้าย | 0.210 | 9.2% | 12.3% |
| ขวา | 0.334 | 29.7% | 24.6% |

- ฝั่งซ้ายแน่นสุดช่วงเช้า **07:00** (occupancy เฉลี่ย 0.63)
- ฝั่งขวาแน่นสุดช่วงเย็น **16:00–18:59** (สูงสุด 18:00 = 0.60)
- รวมสองฝั่ง ชั่วโมง 07:00 ติดขัด 74.7% ของเวลา, 17:00 ติดขัด 44.3%; ช่วง 10:00–13:59 ติดขัดต่ำกว่า 5%

## ไฟล์ภาพ (ไม่ได้ขึ้น GitHub — ใหญ่เกิน)

ภาพเก็บไว้ในเครื่องที่ `backup/` (อยู่ใน `.gitignore`) แบ่งเป็น 2 ชุด รวมกันได้ครบทุกวัน:

| โฟลเดอร์ | ช่วงวันที่ | เนื้อหา |
|---|---|---|
| `backup/vps-2026-11-03/` | 21 ต.ค. – 3 พ.ย. (บางส่วน) | ภาพดิบ 1.6 GB, overlay+thumbnail 2.9 GB, DB ณ วันนั้น, code, Caddyfile เดิม |
| `backup/vps-2026-11-22/` | 3 พ.ย. – 22 พ.ย. | ภาพดิบ 27,320 ไฟล์ (2.5 GB), overlay 27,312 ไฟล์ (3.5 GB), thumbnail 27,312 ไฟล์ (1.1 GB), DB สุดท้าย, code, Caddyfile |

- `data/raw/YYYY-MM-DD/HHMMSS.jpg` — snapshot ดิบ 720×576 จากกล้อง
- `results/overlays/YYYY-MM-DD/*.jpg` — ภาพที่วาดผลวิเคราะห์ทับ (แสดงในแดชบอร์ด)
- `results/thumbnails/YYYY-MM-DD/*.jpg` — ภาพย่อสำหรับตารางประวัติ

บน VPS ภาพดิบถูกลบอัตโนมัติเมื่อเก่ากว่า 30 วัน (21–22 ต.ค. จึงเหลือแค่ในชุด 3 พ.ย.)
`backup/vps-2026-11-22/code.tgz` คือโค้ดที่ deploy อยู่จริง (revision `f5a80a7`) และ `Caddyfile.vps` คือ config proxy ก่อนลบ
