"""⑤ Feature และ Morphology (W4 edge, W5 color model, W6 morphology)"""
import cv2
import numpy as np

import config


# ---------------------------------------------------------- morphology
def clean_mask(binary, roi=None):
    """opening ลบจุดรบกวนเล็ก ๆ -> closing อุดรูในตัวรถ (W6)"""
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.OPEN_KSIZE, config.OPEN_KSIZE))
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.CLOSE_KSIZE, config.CLOSE_KSIZE))
    out = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open)
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, k_close)
    if roi is not None:
        out = cv2.bitwise_and(out, roi)
    return out


# ---------------------------------------------------------------- edge
def sobel_magnitude(gray):
    """ขนาด gradient จาก Sobel (W4)"""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def edge_map(gray, thresh=60):
    return (sobel_magnitude(gray) > thresh).astype(np.uint8) * 255


def edge_density(gray, roi, thresh=60):
    """สัดส่วนพิกเซลขอบใน ROI: ถนนเรียบมีขอบน้อย รถมีขอบมาก"""
    e = edge_map(gray, thresh)
    n = np.count_nonzero(roi)
    return float(np.count_nonzero(e & roi)) / n if n else 0.0


def extra_edges(frame_gray, bg_gray, thresh=60):
    """ขอบที่มีในภาพปัจจุบันแต่ไม่มีในพื้นหลัง (ตัดเส้นจราจร/ขอบเกาะกลางทิ้ง)"""
    ef = edge_map(frame_gray, thresh)
    eb = cv2.dilate(edge_map(bg_gray, thresh), np.ones((3, 3), np.uint8))
    return cv2.bitwise_and(ef, cv2.bitwise_not(eb))


# --------------------------------------------------------------- color
def saturation(img):
    """ช่อง S ของ HSV (W5): ถนนสีเทา S ต่ำ รถสีจัด S สูง"""
    return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]


def value_channel(img):
    """ช่อง V ของ HSV ใช้ดูเงา (เงา = V ลด แต่สี/S เปลี่ยนน้อย)"""
    return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 2]


# -------------------------------------------------------------- motion
def motion_ratio(gray_a, gray_b, vehicle_mask, diff_thresh=25):
    """สัดส่วนพิกเซลรถที่เปลี่ยนไประหว่าง 2 ภาพต่อเนื่อง (ห่างกัน ~60 วินาที)

    ต่ำ = รถจอดนิ่ง (ติดจริง)   สูง = รถเยอะแต่ยังเคลื่อนตัว
    คืน None ถ้าไม่มีพิกเซลรถให้วัด
    """
    d = cv2.absdiff(gray_a, gray_b)
    moving = (d > diff_thresh).astype(np.uint8) * 255
    moving = cv2.morphologyEx(moving, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # รถที่ขับออกไปแล้ว ตำแหน่งเดิมจะกลายเป็นถนน -> พิกเซลเปลี่ยน; รถที่จอดนิ่ง -> แทบไม่เปลี่ยน
    n = np.count_nonzero(vehicle_mask)
    if n < 50:
        return None
    return float(np.count_nonzero(moving & vehicle_mask)) / n
