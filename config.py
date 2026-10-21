"""ค่าตั้งต้นที่ทุกไฟล์ใช้ร่วมกัน (interface กลางของทีม)"""
import sys
import os
from pathlib import Path

# ให้ print ภาษาไทยได้แม้ console ของ Windows จะเป็น cp1252/cp874
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent

# ---------- แหล่งข้อมูล ----------
SNAPSHOT_URL = "https://ipcamlive.com/player/snapshot.php?alias=6695edb811079"
INTERVAL_SEC = 300          # ดึงภาพทุก 5 นาที
# กล้องอัปเดต snapshot นาทีละครั้ง ภาพที่สอง (วัดการเคลื่อนไหว) จึงต้องรอภาพใหม่ ~60 วินาที
PAIR_POLL_SEC = 5           # เช็กทุก 5 วินาทีว่าภาพเปลี่ยนหรือยัง
PAIR_TIMEOUT_SEC = 100      # รอนานสุดเท่านี้ ถ้าไม่เปลี่ยนก็ข้าม

# ---------- โฟลเดอร์ ----------
RAW_DIR = ROOT / "data" / "raw"               # data/raw/YYYY-MM-DD/HHMMSS.jpg (+ HHMMSS_b.jpg)
BG_DIR = ROOT / "data" / "background"         # ภาพถนนว่างที่จัดแนวแล้ว
REFERENCE_IMG = ROOT / "image_reference" / "when_clear_road.png"
MASK_DIR = ROOT / "masks"
RESULT_DIR = ROOT / "results"
CHART_DIR = RESULT_DIR / "charts"
OVERLAY_DIR = RESULT_DIR / "overlays"
OCCUPANCY_CSV = RESULT_DIR / "occupancy.csv"
LABELS_CSV = RESULT_DIR / "labels.csv"
DB_PATH = Path(os.environ.get("TRAFFIC_DB_PATH", str(RESULT_DIR / "traffic.db")))
THUMB_DIR = RESULT_DIR / "thumbnails"
WEB_STALE_SEC = 600

# ---------- รูปทรงภาพ ----------
FRAME_SIZE = (720, 576)     # (w, h) ของ snapshot จากกล้อง

# กล่องตัวหนังสือที่กล้องประทับลงภาพ (x1, y1, x2, y2) ต้องลบออกก่อนประมวลผล
OVERLAY_BOXES = [
    (30, 25, 200, 52),      # วันที่-เวลา มุมซ้ายบน
    (555, 25, 675, 50),     # คำว่า Rangsitcity มุมขวาบน
]

# ROI ของแต่ละฝั่งถนน (พิกัดในภาพ 720x576) วาดด้วย roi_tool.py เมื่อ 21/10/2569
# ครอบผิวถนนทั้งหมดจนถึงสะพานด้านบน ขอบล่างหลบยอดซุ้มประตูเมือง
# วาดใหม่ได้ด้วย roi_tool.py  ->  masks/<ชื่อ>.png และ masks/rois.json จะถูกใช้แทนค่าเหล่านี้
LANE_POLYGONS = {
    "left": [(224, 46), (257, 186), (247, 188), (243, 232), (252, 240), (302, 418), (324, 414),
             (332, 400), (340, 414), (383, 410), (393, 394), (404, 408), (413, 409), (346, 261),
             (320, 202), (293, 129), (273, 73), (265, 52), (258, 47)],
    "right": [(293, 44), (265, 44), (319, 173), (378, 291), (433, 398), (440, 408), (468, 407),
              (474, 390), (480, 396), (493, 378), (506, 391), (512, 382), (522, 403), (572, 404),
              (382, 150), (365, 131), (360, 135), (384, 185), (378, 186), (328, 92), (309, 60),
              (300, 46), (298, 44), (294, 44)],
}
LANE_NAMES_TH = {"left": "ฝั่งซ้าย (วิ่งออกจากกล้อง)", "right": "ฝั่งขวา (วิ่งเข้าหากล้อง)"}

# แบ่ง ROI เป็นช่วง ไกล/กลาง/ใกล้ ตามแกน y แล้วให้น้ำหนักเท่ากัน (ชดเชย perspective)
# far เริ่มที่ y=125 ใต้ป้ายคร่อมถนน (y≈110–120 ทำให้จับผิด) ส่วน ROI ที่สูงกว่านี้นับเฉพาะใน occupancy_raw
ZONES = {"far": (125, 200), "mid": (200, 300), "near": (300, 420)}

# จุดสังเกตที่ไม่ขยับ (ซุ้มประตูเมืองตรงกลาง) สำหรับ template matching ตรวจกล้องขยับ
LANDMARK_BOX = (455, 385, 545, 470)   # (x1, y1, x2, y2)
LANDMARK_SEARCH = 25                  # ค้นหารอบ ๆ ±25 px
CAMERA_SHIFT_TOL = 3                  # ขยับเกินกี่ px ถือว่ากล้องขยับ

# ---------- Preprocess ----------
MEDIAN_KSIZE = 5
NIGHT_BRIGHTNESS = 70       # ค่าเฉลี่ยความสว่างทั้งภาพต่ำกว่านี้ = กลางคืน

# ---------- Segmentation ----------
# ภาพถนนว่างกลางวันใน data/background/
#   "bg_day.png"        = จัดแนวจาก image_reference/when_clear_road.png (python segment.py --reference)
#   "bg_day_median.png" = temporal median จากภาพที่เก็บมา (python segment.py --median)
# กลางคืนใช้ "bg_night.png" อัตโนมัติถ้ามี
BG_DAY_FILE = "bg_day.png"
MIN_DIFF_THRESHOLD = 22     # ขั้นต่ำของ threshold กัน Otsu เลือกค่าต่ำเกินตอนถนนว่าง
MAX_DIFF_THRESHOLD = 60
SAT_WEIGHT = 0.8            # น้ำหนักของผลต่างช่อง S (HSV) ที่รวมกับผลต่าง grayscale
OPEN_KSIZE = 3
CLOSE_KSIZE = 5

# ---------- เกณฑ์ระดับ ----------
LEVELS = ["clear", "moderate", "congested"]
LEVEL_TH = {"clear": "โล่ง", "moderate": "ปานกลาง", "congested": "ติด"}
OCC_MODERATE = 0.20         # < 20% = โล่ง
OCC_CONGESTED = 0.45        # > 45% = ติด
MOTION_STOPPED = 0.35       # สัดส่วนพิกเซลรถที่เปลี่ยนระหว่างภาพคู่ < 35% = รถจอดนิ่ง (รถไหลปกติวัดได้ ~80%)
MOTION_FLOWING = 0.65       # > 65% = รถเยอะแต่ยังเคลื่อนตัวดี
LANDMARK_MIN_SCORE = 0.5    # template matching ต่ำกว่านี้ = กล้องหมุน/ซูม/ถูกบัง ผลไม่น่าเชื่อถือ
