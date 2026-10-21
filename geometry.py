"""เรขาคณิตของภาพ (W7 geometric transform, W8 template matching)

- lane_masks()            ROI ขาวดำของแต่ละฝั่งถนน
- align_reference()       จัดแนวภาพถนนว่างที่ผู้ใช้เตรียมไว้ให้ตรงกับเฟรมกล้อง (ORB + homography)
- camera_shift()          ตรวจว่ากล้องขยับไหมด้วย template matching จุดสังเกตที่ไม่เคลื่อนที่
- shift_mask()            เลื่อน ROI ตามกล้อง (translation)
- birdseye()              ดึงเลนให้เป็นมุมมองจากด้านบน (perspective warp)
"""
import json

import cv2
import numpy as np

import config
from preprocess import load_frame, save_image, to_gray

W, H = config.FRAME_SIZE


# ---------------------------------------------------------------- ROI
def polygon_mask(points):
    mask = np.zeros((H, W), np.uint8)
    cv2.fillPoly(mask, [np.array(points, np.int32)], 255)
    return mask


ROI_JSON = config.MASK_DIR / "rois.json"


def lane_polygons():
    """จุดของ ROI แต่ละเลน: ที่วาดด้วย roi_tool.py (masks/rois.json) ถ้ามี ไม่งั้นใช้ค่าใน config.py"""
    polys = dict(config.LANE_POLYGONS)
    if ROI_JSON.exists():
        saved = json.loads(ROI_JSON.read_text(encoding="utf-8"))
        polys.update({lane: [tuple(p) for p in pts] for lane, pts in saved.items() if lane in polys})
    return polys


def lane_masks():
    """ROI ขาวดำของแต่ละเลน ลำดับความสำคัญ:
    1) masks/<lane>.png  (วาดด้วย roi_tool.py หรือระบายเองในโปรแกรมแต่งภาพ: ขาว = ถนน, ดำ = ไม่ใช้)
    2) จุดใน masks/rois.json
    3) LANE_POLYGONS ใน config.py
    """
    masks = {}
    for lane, pts in lane_polygons().items():
        path = config.MASK_DIR / f"{lane}.png"
        if path.exists():
            m = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
            if (m.shape[1], m.shape[0]) != (W, H):
                m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
            masks[lane] = (m > 127).astype(np.uint8) * 255
        else:
            masks[lane] = polygon_mask(pts)
    return masks


def mask_outline(mask):
    """เส้นขอบของ mask สำหรับวาดลงภาพ"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return cnts


def zone_masks():
    """แถบแนวนอน ไกล/กลาง/ใกล้ สำหรับถ่วงน้ำหนัก perspective"""
    out = {}
    for name, (y1, y2) in config.ZONES.items():
        m = np.zeros((H, W), np.uint8)
        m[y1:y2, :] = 255
        out[name] = m
    return out


def road_mask(masks=None):
    masks = masks or lane_masks()
    out = np.zeros((H, W), np.uint8)
    for m in masks.values():
        out |= m
    return out


def static_mask(masks=None):
    """พื้นที่นอกถนน (อาคาร/ป้าย) ใช้เทียบแสง ไม่รวมตัวหนังสือที่ประทับและขอบภาพ"""
    road = cv2.dilate(road_mask(masks), np.ones((25, 25), np.uint8))
    m = np.full((H, W), 255, np.uint8)
    m[road > 0] = 0
    for x1, y1, x2, y2 in config.OVERLAY_BOXES:
        m[y1:y2, x1:x2] = 0
    m[:, :10] = m[:, -10:] = 0
    m[:10, :] = m[-10:, :] = 0
    return m


# ---------------------------------------------------------- alignment
def align_reference(ref_img, frame_img, max_features=5000):
    """จัดแนวภาพอ้างอิง (ขนาด/มุมไม่ตรงกับกล้อง) ให้ทับเฟรมกล้องพอดี

    1) ย่อ/ขยายให้ได้ขนาดเท่าเฟรม (scaling)
    2) หาจุดเด่น ORB ทั้งสองภาพ จับคู่ แล้วประมาณ homography ด้วย RANSAC
    3) warpPerspective ภาพอ้างอิงไปยังพิกัดเฟรม
    คืน (ภาพที่จัดแนวแล้ว, H, จำนวน inlier)
    """
    ref = cv2.resize(ref_img, (W, H), interpolation=cv2.INTER_AREA)
    g1, g2 = to_gray(ref), to_gray(frame_img)
    # ไม่ใช้บริเวณตัวหนังสือประทับ เพราะเวลาไม่ตรงกัน
    feat_mask = np.full((H, W), 255, np.uint8)
    for x1, y1, x2, y2 in config.OVERLAY_BOXES:
        feat_mask[y1:y2, x1:x2] = 0
    orb = cv2.ORB_create(max_features)
    k1, d1 = orb.detectAndCompute(g1, feat_mask)
    k2, d2 = orb.detectAndCompute(g2, feat_mask)
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in matches if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 20:
        return ref, np.eye(3), 0
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    Hm, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if Hm is None:
        return ref, np.eye(3), 0
    aligned = cv2.warpPerspective(ref, Hm, (W, H), borderMode=cv2.BORDER_REPLICATE)
    return aligned, Hm, int(inliers.sum())


# ------------------------------------------------------- camera shift
def camera_shift(bg_gray, frame_gray):
    """template matching ซุ้มประตูเมือง (ไม่เคลื่อนที่) หาว่ากล้องเลื่อนไปกี่พิกเซล

    คืน (dx, dy, score)  score = ค่า TM_CCOEFF_NORMED (ใกล้ 1 = ตรงกันมาก)
    """
    x1, y1, x2, y2 = config.LANDMARK_BOX
    s = config.LANDMARK_SEARCH
    tpl = bg_gray[y1:y2, x1:x2]
    sx1, sy1 = max(0, x1 - s), max(0, y1 - s)
    sx2, sy2 = min(W, x2 + s), min(H, y2 + s)
    search = frame_gray[sy1:sy2, sx1:sx2]
    res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    dx = loc[0] + sx1 - x1
    dy = loc[1] + sy1 - y1
    return int(dx), int(dy), float(score)


def shift_image(img, dx, dy):
    """เลื่อนภาพ/mask ด้วย affine translation (W7)"""
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_NEAREST)


# ---------------------------------------------------------- bird's-eye
def lane_quad(mask):
    """มุม 4 จุดของเลนจาก mask: บนซ้าย บนขวา ล่างขวา ล่างซ้าย

    ใช้ช่วง y เดียวกับ ZONES (ส่วนที่นับ occupancy) ส่วนแถวล่างสุดไล่จากล่างขึ้นบน
    จนเจอแถวที่ถนนเต็มแถบ (ขอบล่างที่หลบยอดซุ้มประตูเป็นหยัก ๆ จะทำให้ภาพบิด)
    """
    z_top = min(y1 for y1, _ in config.ZONES.values())
    z_bot = max(y2 for _, y2 in config.ZONES.values())

    def extent(y):
        xs = np.nonzero(mask[y])[0]
        if len(xs) == 0:
            return None
        return xs.min(), xs.max(), len(xs) / (xs.max() - xs.min() + 1)

    rows = [y for y in range(max(z_top, 0), min(z_bot, H)) if extent(y)]
    top, bot = rows[0], rows[-1]
    widest = max(extent(y)[1] - extent(y)[0] for y in rows)
    for y in reversed(rows):
        lo, hi, fill = extent(y)
        if fill >= 0.95 and hi - lo >= 0.8 * widest:
            bot = y
            break
    tl, tr, _ = extent(top)
    bl, br, _ = extent(bot)
    return np.float32([[tl, top], [tr, top], [br, bot], [bl, bot]])


def birdseye(img, lane_mask, out_size=(120, 400)):
    """ดึงเลนให้เป็นสี่เหลี่ยมผืนผ้ามองจากด้านบน รถไกล/ใกล้จะมีขนาดใกล้กันขึ้น"""
    w, h = out_size
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(lane_quad(lane_mask), dst)
    interp = cv2.INTER_NEAREST if img.ndim == 2 else cv2.INTER_LINEAR
    return cv2.warpPerspective(img, M, (w, h), flags=interp)


# ------------------------------------------------------------- CLI
def build_aligned_reference(sample_frame_path):
    """สร้าง data/background/bg_day.png จาก image_reference + เฟรมตัวอย่าง"""
    ref = load_frame(config.REFERENCE_IMG)
    frame = load_frame(sample_frame_path)
    aligned, Hm, n = align_reference(ref, frame)
    out = config.BG_DIR / "bg_day.png"
    save_image(out, aligned)
    return out, Hm, n
