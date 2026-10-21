"""④ Segmentation: แยกพิกเซล "รถ" ออกจาก "ถนน" (W2 threshold/Otsu, W3 temporal median, W9 K-means)

interface:  segment(img, bg, mask) -> binary (255 = รถ)

ใช้งาน (สร้างภาพพื้นหลัง):
    python segment.py --reference                 # จัดแนว image_reference -> data/background/bg_day.png
    python segment.py --median 2026-10-21         # temporal median จากภาพที่เก็บมา (แยกกลางวัน/กลางคืน)
"""
import argparse

import cv2
import numpy as np

import config
import features
import geometry
from preprocess import is_night, load_frame, match_photometry, preprocess, save_image


# ---------------------------------------------------------- background
def temporal_median(paths, max_images=60, percentile=50):
    """ภาพถนนว่างจาก median ของหลายภาพทีละพิกเซลตามแกนเวลา (median filter แบบเวลา W3)

    รถไม่ได้อยู่ที่ตำแหน่งเดิมนาน ค่ากลางของแต่ละพิกเซลจึงเป็นสีถนน
    percentile < 50 = เลือกค่าที่มืดกว่าค่ากลาง ใช้กับกลางคืน (ดู NIGHT_BG_PERCENTILE)
    """
    paths = list(paths)
    if len(paths) > max_images:
        idx = np.linspace(0, len(paths) - 1, max_images).astype(int)
        paths = [paths[i] for i in idx]
    stack = np.stack([load_frame(p) for p in paths], axis=0)
    return np.percentile(stack, percentile, axis=0).astype(np.uint8)


def load_background(night=False):
    """เลือกภาพพื้นหลังกลางวัน/กลางคืน ถ้าไม่มีภาพกลางคืนใช้ภาพกลางวันแทน"""
    day = [config.BG_DAY_FILE, "bg_day.png"]
    names = ["bg_night.png", *day] if night else [*day, "bg_night.png"]
    for name in names:
        p = config.BG_DIR / name
        if p.exists():
            return load_frame(p)
    raise FileNotFoundError("ยังไม่มีภาพพื้นหลัง ให้รัน: python segment.py --reference")


# -------------------------------------------------------- diff features
def road_relight(bg_ch, frame_ch, lanes, band=None, limit=None):
    """ปรับภาพพื้นหลังให้ความสว่างของผิวถนนตรงกับภาพปัจจุบัน แยกทีละเลน ทีละแถบแนวนอน

    แดดหรือเงาเมฆทำให้ถนนสว่าง/มืดลงทั้งแถบ ผลต่างเกิน threshold จนถนนทั้งแถบกลายเป็น "รถ"
    ในแต่ละแถบ รถกินพื้นที่ไม่ถึงครึ่ง median ของ (ภาพ - พื้นหลัง) จึงเป็นค่าที่แสงทำให้ผิวถนนเปลี่ยนไป
    ค่าชดเชยถูกบีบไว้ที่ ±limit (รถติดเต็มแถบ median จะเป็นสีรถ ไม่ใช่ถนน)
    แล้ว interpolate ทีละแถว ภาพพื้นหลังจะได้ไม่มีรอยต่อระหว่างแถบที่กลายเป็นขอบปลอม
    """
    band = band or config.RELIGHT_BAND
    limit = config.RELIGHT_MAX if limit is None else limit
    signed = frame_ch.astype(np.float32) - bg_ch.astype(np.float32)
    corr = np.zeros(bg_ch.shape, np.float32)
    for m in lanes.values():
        rows = np.nonzero(m.any(axis=1))[0]
        if not len(rows):
            continue
        centers, offsets = [], []
        for y0 in range(rows.min(), rows.max() + 1, band):
            win = slice(max(y0 - band, 0), y0 + 2 * band)     # หน้าต่าง 3 แถบ ให้ค่าเรียบขึ้น
            road_px = signed[win][m[win] > 0]
            if road_px.size >= 50:
                centers.append(y0 + band / 2)
                offsets.append(np.clip(np.median(road_px), -limit, limit))
        if not centers:
            continue
        per_row = np.interp(np.arange(bg_ch.shape[0]), centers, offsets)
        lane_px = m > 0
        corr[lane_px] = np.broadcast_to(per_row[:, None], corr.shape)[lane_px]
    return np.clip(bg_ch.astype(np.float32) + corr, 0, 255).astype(np.uint8)


def difference_maps(img, bg, road, static, lanes=None):
    """ผลต่างระหว่างภาพปัจจุบันกับภาพถนนว่าง หลังปรับแสงของพื้นหลังให้ตรงกันแล้ว

    ปรับแสง 2 ชั้น: match_photometry จากอาคาร (แสงทั้งภาพ/การตั้งค่ากล้อง)
    แล้ว road_relight จากผิวถนนเอง (แสงเฉพาะบนถนน)
    คืน dict: gray (absdiff ความสว่าง), sat (absdiff ช่อง S), edge (ขอบที่เพิ่มขึ้น), combined
    """
    lanes = lanes if lanes is not None else geometry.lane_masks()
    f_gray, b_gray = preprocess(img), preprocess(bg)
    b_gray = road_relight(match_photometry(b_gray, f_gray, static), f_gray, lanes)
    d_gray = cv2.absdiff(f_gray, b_gray)

    f_sat = cv2.medianBlur(features.saturation(img), config.MEDIAN_KSIZE)
    b_sat = cv2.medianBlur(features.saturation(bg), config.MEDIAN_KSIZE)
    b_sat = road_relight(match_photometry(b_sat, f_sat, static), f_sat, lanes)
    d_sat = cv2.absdiff(f_sat, b_sat)

    combined = np.maximum(d_gray, (d_sat * config.SAT_WEIGHT).astype(np.uint8))
    combined = cv2.bitwise_and(combined, road)
    edge = cv2.bitwise_and(features.extra_edges(f_gray, b_gray), road)
    return {"gray": d_gray, "sat": d_sat, "edge": edge, "combined": combined,
            "frame_gray": f_gray, "bg_gray": b_gray}


# ---------------------------------------------------------- threshold
def otsu_threshold(diff, roi):
    """Otsu บนเฉพาะพิกเซลใน ROI แล้วบีบให้อยู่ในช่วง [MIN, MAX]

    ถนนว่างเกือบหมด Otsu จะเลือกค่าต่ำมาก (แยก noise) จึงต้องมีค่าขั้นต่ำกันไว้
    """
    vals = diff[roi > 0].reshape(-1, 1)
    t, _ = cv2.threshold(vals, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(np.clip(t, config.MIN_DIFF_THRESHOLD, config.MAX_DIFF_THRESHOLD))


def glare_mask(frame_gray, bg_gray, threshold, roi):
    """พื้นที่ที่ไฟหน้ารถส่องถนนจนขาวจ้า (กลางคืน) = กล้องมองไม่เห็นว่าตรงนั้นเป็นรถหรือถนน

    1) แกนแสง: สว่างมาก (>= GLARE_LEVEL) + สว่างกว่าพื้นหลังเกิน threshold + เรียบ (local std ต่ำ)
       ตัวรถมีขอบ/ลวดลาย แต่แสงที่ฉายลงพื้นเป็นก้อนขาวเรียบ จุดเล็ก ๆ ถูก opening ลบออก แล้วขยาย GLARE_GROW px
    2) ขอบแสงที่จางลง: ขยายจากแกนไปยังพิกเซลที่ติดกันซึ่งสว่างกว่าพื้นหลังเกินครึ่ง threshold และยังเรียบ
       (hysteresis แบบ Canny: เก็บเฉพาะก้อนอ่อนที่ต่อกับก้อนแข็ง ด้วย connected components)
    """
    diff = frame_gray.astype(np.int16) - bg_gray.astype(np.int16)
    texture = features.local_std(frame_gray)
    core = ((frame_gray >= config.GLARE_LEVEL) & (diff > threshold) & (texture < config.GLARE_SMOOTH))
    core = cv2.morphologyEx(core.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    core = cv2.dilate(core, np.ones((config.GLARE_GROW, config.GLARE_GROW), np.uint8))
    weak = ((diff > threshold / 2) & (texture < config.GLARE_HALO_SMOOTH)).astype(np.uint8) * 255
    _, labels = cv2.connectedComponents(cv2.bitwise_or(weak, core), connectivity=8)
    touched = np.unique(labels[core > 0])
    glare = np.isin(labels, touched[touched > 0]).astype(np.uint8) * 255
    glare = cv2.dilate(glare, np.ones((3, 3), np.uint8))
    return cv2.bitwise_and(glare, roi)


def segment(img, bg, mask, static=None, return_debug=False, lanes=None):
    """แยกรถด้วย absdiff + Otsu + ขอบเสริม + morphology"""
    static = static if static is not None else geometry.static_mask()
    maps = difference_maps(img, bg, mask, static, lanes)
    t = otsu_threshold(maps["combined"], mask)
    binary = (maps["combined"] > t).astype(np.uint8) * 255
    # รถสีเทาใกล้ถนน: ความสว่างต่างไม่ถึง T แต่มีขอบตัวรถ -> เติมเฉพาะขอบใหม่ที่ผลต่างเกินครึ่งหนึ่งของ T
    weak = (maps["combined"] > t / 2).astype(np.uint8) * 255
    edge_blobs = cv2.bitwise_and(maps["edge"], weak)
    edge_blobs = cv2.morphologyEx(edge_blobs, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    binary = cv2.bitwise_or(binary, edge_blobs)
    binary = features.clean_mask(binary, mask)
    if return_debug:
        return binary, {**maps, "threshold": t}
    return binary


def segment_kmeans(img, bg, mask, k=2, static=None, lanes=None):
    """ทางเลือก (W9): K-means บนเวกเตอร์ [diff gray, diff S, edge] ของพิกเซลใน ROI

    cluster ที่จุดศูนย์กลางมีผลต่างเกิน MIN_DIFF_THRESHOLD ถือเป็นรถ
    (ถ้าถนนว่างทั้งเลน ทุก cluster จะต่างจากพื้นหลังน้อย -> ไม่มีรถ)
    """
    static = static if static is not None else geometry.static_mask()
    maps = difference_maps(img, bg, mask, static, lanes)
    ys, xs = np.nonzero(mask)
    X = np.stack([maps["gray"][ys, xs], maps["sat"][ys, xs] * config.SAT_WEIGHT,
                  maps["edge"][ys, xs] / 255.0 * 40], axis=1).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(X, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    strength = centers[:, :2].max(axis=1)
    vehicle_clusters = np.where(strength > config.MIN_DIFF_THRESHOLD)[0]
    binary = np.zeros(mask.shape, np.uint8)
    is_vehicle = np.isin(labels.ravel(), vehicle_clusters)
    binary[ys[is_vehicle], xs[is_vehicle]] = 255
    return features.clean_mask(binary, mask)


# ---------------------------------------------------------------- CLI
def _all_frames(day=None):
    pattern = f"{day}/*.jpg" if day else "*/*.jpg"
    return sorted(p for p in config.RAW_DIR.glob(pattern) if not p.stem.endswith("_b"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", action="store_true", help="จัดแนวภาพ image_reference เป็น bg_day.png")
    ap.add_argument("--median", nargs="?", const="", metavar="YYYY-MM-DD",
                    help="สร้าง bg จาก temporal median ของภาพที่เก็บ (ว่าง = ทุกวัน)")
    ap.add_argument("--sample", help="เฟรมตัวอย่างที่ใช้จัดแนว (ค่าเริ่มต้น = ภาพล่าสุด)")
    args = ap.parse_args()

    if args.reference:
        frames = _all_frames()
        sample = args.sample or (frames[-1] if frames else None)
        if sample is None:
            raise SystemExit("ยังไม่มีภาพจากกล้อง ให้รัน collector.py ก่อนอย่างน้อย 1 ภาพ")
        out, Hm, n = geometry.build_aligned_reference(sample)
        print(f"จัดแนวด้วย {n} จุดที่ตรงกัน -> {out}")
        print("homography =\n", np.round(Hm, 4))

    if args.median is not None:
        frames = _all_frames(args.median or None)
        day = [p for p in frames if not is_night(load_frame(p))]
        night = [p for p in frames if p not in day]
        for name, group, q in (("bg_day_median.png", day, 50),
                               ("bg_night.png", night, config.NIGHT_BG_PERCENTILE)):
            if len(group) >= 10:
                out = config.BG_DIR / name
                save_image(out, temporal_median(group, percentile=q))
                print(f"{name}: percentile {q} จาก {len(group)} ภาพ -> {out}")
            else:
                print(f"{name}: มีภาพแค่ {len(group)} ภาพ (ต้องการ >= 10) ข้าม")


if __name__ == "__main__":
    main()
