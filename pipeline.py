"""รวมทุกขั้นเป็น pipeline เดียว: ภาพ 1 เฟรม -> ผลแต่ละเลน

interface:  occupancy(binary, mask) -> float
            process_frame(path) -> (rows, vis)
"""
import datetime

import cv2
import numpy as np

import config
import features
import geometry
import segment
from preprocess import brightness, contrast, load_frame


def occupancy(binary, mask):
    """สัดส่วนพื้นที่ถนนใน ROI ที่ถูกรถครอบครอง"""
    n = np.count_nonzero(mask)
    return float(np.count_nonzero(binary & mask)) / n if n else 0.0


def zone_occupancy(binary, lane_mask, zones):
    """occupancy แยกช่วง ไกล/กลาง/ใกล้ แล้วเฉลี่ยแบบน้ำหนักเท่ากัน

    รถไกลกล้องดูเล็กกว่า ถ้านับพิกเซลรวม ๆ ช่วงใกล้จะครองผล
    การให้แต่ละช่วงมีน้ำหนักเท่ากันจึงชดเชย perspective แบบง่าย
    """
    per_zone = {}
    for name, z in zones.items():
        m = lane_mask & z
        if np.count_nonzero(m):
            per_zone[name] = occupancy(binary, m)
    weighted = float(np.mean(list(per_zone.values()))) if per_zone else 0.0
    return weighted, per_zone


def classify(occ, motion=None):
    """แบ่งระดับจาก occupancy แล้วปรับด้วยการเคลื่อนไหว (ถ้ามีภาพคู่)"""
    if occ < config.OCC_MODERATE:
        return "clear"
    if occ > config.OCC_CONGESTED:
        if motion is not None and motion > config.MOTION_FLOWING:
            return "moderate"       # รถเยอะแต่ยังไหล
        return "congested"
    if motion is not None and motion < config.MOTION_STOPPED:
        return "congested"          # ไม่เต็มมากแต่จอดนิ่ง
    return "moderate"


def timestamp_from_path(path):
    """data/raw/2026-10-21/113852.jpg -> datetime"""
    try:
        return datetime.datetime.strptime(f"{path.parent.name} {path.stem[:6]}", "%Y-%m-%d %H%M%S")
    except ValueError:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def previous_frame(path):
    """ภาพหลักก่อนหน้า path (ข้ามภาพคู่ _b) ถ้าเป็นภาพแรกของวันให้ดูโฟลเดอร์เมื่อวาน"""
    def frames(folder):
        return sorted(p for p in folder.glob("*.jpg") if not p.stem.endswith("_b"))
    earlier = [p for p in frames(path.parent) if p.stem < path.stem]
    if not earlier:
        yesterday = timestamp_from_path(path) - datetime.timedelta(days=1)
        earlier = frames(path.parent.with_name(f"{yesterday:%Y-%m-%d}"))
    return earlier[-1] if earlier else None


def motion_reference(path):
    """ภาพที่ใช้วัดการเคลื่อนไหวคู่กับ path หรือ None

    ข้อมูลโหมด 5 นาทีเดิมมีภาพคู่ _b (ถ่ายหลังภาพหลัก ~60 วินาที) ใช้ภาพนั้น
    โหมด 60 วินาทีไม่มีภาพคู่ ใช้เฟรมก่อนหน้าซึ่งห่าง ~60 วินาทีเหมือนกัน
    absdiff ไม่สนลำดับเวลา: รถที่จอดนิ่งจะอยู่ที่เดิมทั้งก่อนและหลัง
    ถ้าเฟรมก่อนหน้าห่างเกิน MOTION_MAX_GAP_SEC (กล้องหลุด/ข้ามรอบ) รถจะเปลี่ยนไปหมดจนดูเหมือนไหลดี จึงไม่วัด
    """
    pair = path.with_name(path.stem + "_b" + path.suffix)
    if pair.exists():
        return pair
    prev = previous_frame(path)
    if prev is None:
        return None
    gap = (timestamp_from_path(path) - timestamp_from_path(prev)).total_seconds()
    return prev if 0 < gap <= config.MOTION_MAX_GAP_SEC else None


class Pipeline:
    def __init__(self):
        self.masks = geometry.lane_masks()
        self.road = geometry.road_mask(self.masks)
        self.static = geometry.static_mask(self.masks)
        self.zones = geometry.zone_masks()
        self._bg = {}

    def background(self, night):
        if night not in self._bg:
            self._bg[night] = segment.load_background(night)
        return self._bg[night]

    def process_frame(self, path, with_kmeans=True):
        img = load_frame(path)
        bright = brightness(img)
        night = bright < config.NIGHT_BRIGHTNESS
        bg = self.background(night)

        # W8: ตรวจกล้องขยับ แล้วเลื่อนภาพกลับ (W7) ให้ตรงกับ ROI/พื้นหลัง
        dx, dy, score = geometry.camera_shift(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY),
                                              cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        if max(abs(dx), abs(dy)) > config.CAMERA_SHIFT_TOL and score >= config.LANDMARK_MIN_SCORE:
            img = geometry.shift_image(img, -dx, -dy)

        binary, dbg = segment.segment(img, bg, self.road, self.static, return_debug=True, lanes=self.masks)
        kbin = (segment.segment_kmeans(img, bg, self.road, static=self.static, lanes=self.masks)
                if with_kmeans else None)

        # ภาพที่ห่าง ~60 วินาที (ภาพคู่ หรือเฟรมก่อนหน้า) สำหรับวัดการเคลื่อนไหว
        other = motion_reference(path)
        gray_b = None
        if other is not None:
            img_b = load_frame(other)
            if max(abs(dx), abs(dy)) > config.CAMERA_SHIFT_TOL and score >= config.LANDMARK_MIN_SCORE:
                img_b = geometry.shift_image(img_b, -dx, -dy)
            gray_b = cv2.medianBlur(cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY), config.MEDIAN_KSIZE)

        ts = timestamp_from_path(path)
        rows = []
        for lane, m in self.masks.items():
            occ, per_zone = zone_occupancy(binary, m, self.zones)
            motion = None
            if gray_b is not None:
                motion = features.motion_ratio(dbg["frame_gray"], gray_b, binary & m)
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "file": str(path.relative_to(config.ROOT)).replace("\\", "/"),
                "lane": lane,
                "occupancy": round(occ, 4),
                "occupancy_raw": round(occupancy(binary, m), 4),
                "occ_far": round(per_zone.get("far", 0), 4),
                "occ_mid": round(per_zone.get("mid", 0), 4),
                "occ_near": round(per_zone.get("near", 0), 4),
                "occupancy_kmeans": round(zone_occupancy(kbin, m, self.zones)[0], 4) if kbin is not None else "",
                "edge_density": round(features.edge_density(dbg["frame_gray"], m), 4),
                "motion": "" if motion is None else round(motion, 4),
                "brightness": round(bright, 1),
                "contrast": round(contrast(img), 1),
                "is_night": int(night),
                "threshold": round(dbg["threshold"], 1),
                "cam_dx": dx, "cam_dy": dy, "cam_score": round(score, 3),
                "level": classify(occ, motion),
            })
        vis = {"img": img, "binary": binary, "kmeans": kbin, "diff": dbg["combined"], "masks": self.masks}
        return rows, vis


LEVEL_BGR = {"clear": (80, 200, 80), "moderate": (0, 190, 255), "congested": (40, 40, 230)}


def draw_overlay(img, binary, rows, masks):
    """ภาพสำหรับนำเสนอ: ระบายสีเลนตามระดับ + ขอบรถ + ตัวเลข occupancy"""
    out = img.copy()
    tint = img.copy()
    for r in rows:
        tint[masks[r["lane"]] > 0] = LEVEL_BGR[r["level"]]
    out = cv2.addWeighted(tint, 0.25, out, 0.75, 0)
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, (0, 0, 255), 1)
    for r in rows:
        cv2.drawContours(out, geometry.mask_outline(masks[r["lane"]]), -1, LEVEL_BGR[r["level"]], 2)
    # กล่องสรุปมุมล่างซ้าย
    cv2.rectangle(out, (8, 470), (300, 568), (0, 0, 0), -1)
    cv2.putText(out, rows[0]["timestamp"], (16, 492), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    for i, r in enumerate(rows):
        mot = "" if r["motion"] == "" else f" mov {r['motion']*100:.0f}%"
        txt = f"{r['lane']:5s} {r['occupancy']*100:5.1f}% {r['level']}{mot}"
        cv2.putText(out, txt, (16, 518 + 24 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, LEVEL_BGR[r["level"]], 2)
    return out
