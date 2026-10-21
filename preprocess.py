"""② Preprocess (W1 gray/brightness, W2 histogram, W3 median filter)

interface:  preprocess(img) -> gray ที่ผ่าน median filter แล้ว
"""
import cv2
import numpy as np

import config


def load_frame(path):
    """อ่านภาพ (รองรับ path ภาษาไทย/Unicode บน Windows) และปรับเป็นขนาดมาตรฐาน"""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f"อ่านภาพไม่ได้: {path}")
    if (img.shape[1], img.shape[0]) != config.FRAME_SIZE:
        img = cv2.resize(img, config.FRAME_SIZE, interpolation=cv2.INTER_AREA)
    return img


def save_image(path, img, quality=None):
    """quality = คุณภาพ JPEG 0–100 (None = ค่าเริ่มต้นของ OpenCV คือ 95)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)] if quality is not None else []
    ok, buf = cv2.imencode(path.suffix, img, params)
    if not ok:
        raise IOError(f"บันทึกภาพไม่ได้: {path}")
    buf.tofile(str(path))


def remove_overlay_text(img):
    """ลบตัวหนังสือวันที่-เวลาที่กล้องประทับลงภาพ ด้วย inpainting จากพิกเซลรอบข้าง"""
    mask = np.zeros(img.shape[:2], np.uint8)
    for x1, y1, x2, y2 in config.OVERLAY_BOXES:
        mask[y1:y2, x1:x2] = 255
    return cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)


def to_gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def denoise(gray):
    """median filter ลด noise จากการบีบอัด JPEG (W3)"""
    return cv2.medianBlur(gray, config.MEDIAN_KSIZE)


def equalize(gray, clahe=True):
    """ปรับ histogram ให้ภาพทุกช่วงเวลามี contrast ใกล้กัน (W2)"""
    if clahe:
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.equalizeHist(gray)


def brightness(img):
    """ค่าความสว่างเฉลี่ย (W1) ใช้ตัดสินกลางวัน/กลางคืน"""
    gray = img if img.ndim == 2 else to_gray(img)
    return float(gray.mean())


def contrast(img):
    """ส่วนเบี่ยงเบนมาตรฐานของความสว่าง (RMS contrast) ต่ำผิดปกติ = หมอก/ฝน/เลนส์มัว"""
    gray = img if img.ndim == 2 else to_gray(img)
    return float(gray.std())


def dark_fraction(img):
    """สัดส่วนพิกเซลที่มืดกว่า NIGHT_DARK_LEVEL (W1 histogram)

    กลางคืนไฟถนน/ป้ายไฟทำให้ค่าเฉลี่ยยังสูง (~80) แต่ท้องฟ้าและอาคารมืดสนิท
    กลางวันมีพิกเซลมืดแค่ ~5% (เงาใต้หลังคา) กลางคืนกระโดดเป็น ~35%
    """
    gray = img if img.ndim == 2 else to_gray(img)
    return float(np.count_nonzero(gray < config.NIGHT_DARK_LEVEL)) / gray.size


def is_night(img):
    return brightness(img) < config.NIGHT_BRIGHTNESS or dark_fraction(img) > config.NIGHT_DARK_FRAC


def preprocess(img):
    """ตัดตัวหนังสือ -> grayscale -> median filter"""
    return denoise(to_gray(remove_overlay_text(img)))


def match_photometry(bg_gray, frame_gray, static_mask):
    """ปรับความสว่าง/contrast ของภาพพื้นหลังให้ใกล้ภาพปัจจุบัน

    หา gain, bias ของสมการ frame = gain * bg + bias จากพิกเซลที่ไม่ใช่ถนน (อาคาร ป้าย)
    เพราะส่วนนั้นไม่มีรถ จึงสะท้อนเฉพาะผลของแสง/การตั้งค่ากล้อง (contrast stretching แบบเชิงเส้น W2)
    """
    b = bg_gray[static_mask > 0].astype(np.float32)
    f = frame_gray[static_mask > 0].astype(np.float32)
    # ใช้ percentile แทน mean/std เพื่อทนต่อ outlier (ป้ายไฟ คนเดิน ฯลฯ)
    b_lo, b_hi = np.percentile(b, [5, 95])
    f_lo, f_hi = np.percentile(f, [5, 95])
    gain = (f_hi - f_lo) / max(b_hi - b_lo, 1.0)
    bias = f_lo - gain * b_lo
    out = bg_gray.astype(np.float32) * gain + bias
    return np.clip(out, 0, 255).astype(np.uint8)
