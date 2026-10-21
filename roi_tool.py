"""③ ROI tool: วาด ROI ของแต่ละเลนเอง -> ใช้กับทุกภาพอัตโนมัติ

ใช้งาน:
    python roi_tool.py --draw                 # วาดทุกเลนต่อกัน (left แล้ว right)
    python roi_tool.py --draw left            # วาดเฉพาะเลน left
    python roi_tool.py --draw --image data/raw/2026-10-21/113952.jpg
    python roi_tool.py --preview              # บันทึกภาพ ROI ปัจจุบัน -> results/roi_preview.jpg
    python roi_tool.py --reset                # ลบ ROI ที่วาดไว้ กลับไปใช้ค่าใน config.py

ปุ่มในหน้าต่างวาด:
    คลิกซ้าย = เพิ่มจุด      คลิกขวา / Backspace / z = ลบจุดล่าสุด     r = เริ่มเลนนี้ใหม่
    Enter / Space = บันทึกเลนนี้แล้วไปเลนถัดไป      Esc = ยกเลิก (ไม่บันทึกเลนนี้)
    + / - = ซูมเข้า/ออก

ผลลัพธ์: masks/<lane>.png (ขาว = ถนน) และ masks/rois.json (จุดของรูปหลายเหลี่ยม)
ทุกสคริปต์ (analyze, demo_steps, label_tool) อ่านจากไฟล์เหล่านี้ หลังวาดใหม่ให้รัน
    python analyze.py --redo --all

ถ้าอยากระบายเองในโปรแกรมแต่งภาพ (Paint, Photoshop): สร้างภาพขาวดำขนาด 720x576
ระบายสีขาวทับพื้นที่ถนน พื้นหลังสีดำ แล้วบันทึกเป็น masks/left.png หรือ masks/right.png
(python roi_tool.py --export-template จะบันทึกภาพถนนว่างให้ใช้เป็นพื้นในการระบาย)
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import config
import geometry
from preprocess import load_frame, save_image

LANE_BGR = {"left": (80, 220, 80), "right": (255, 80, 255)}


def default_image():
    """ใช้ภาพถนนว่างเป็นพื้น (เห็นเส้นจราจรชัดกว่า) ถ้าไม่มีใช้ภาพล่าสุดจากกล้อง"""
    bg = config.BG_DIR / "bg_day.png"
    if bg.exists():
        return load_frame(bg)
    frames = sorted(p for p in config.RAW_DIR.glob("*/*.jpg") if not p.stem.endswith("_b"))
    return load_frame(frames[-1]) if frames else load_frame(config.REFERENCE_IMG)


def lane_color(lane):
    return LANE_BGR.get(lane, (0, 255, 255))


def preview(img, masks=None):
    masks = masks or geometry.lane_masks()
    tint = img.copy()
    for lane, m in masks.items():
        tint[m > 0] = lane_color(lane)
    out = cv2.addWeighted(tint, 0.35, img, 0.65, 0)
    for lane, m in masks.items():
        cv2.drawContours(out, geometry.mask_outline(m), -1, lane_color(lane), 1)
    for name, (y1, _) in config.ZONES.items():
        cv2.line(out, (0, y1), (config.FRAME_SIZE[0], y1), (255, 255, 255), 1)
        cv2.putText(out, name, (600, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    x1, y1, x2, y2 = config.LANDMARK_BOX
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 2)
    cv2.putText(out, "landmark", (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    for bx1, by1, bx2, by2 in config.OVERLAY_BOXES:
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 255), 1)
    return out


def save_lane(lane, pts):
    """บันทึก mask + จุดของเลนนี้ (ไฟล์ที่ทุกสคริปต์อ่าน)"""
    save_image(config.MASK_DIR / f"{lane}.png", geometry.polygon_mask(pts))
    saved = {}
    if geometry.ROI_JSON.exists():
        saved = json.loads(geometry.ROI_JSON.read_text(encoding="utf-8"))
    saved[lane] = [list(map(int, p)) for p in pts]
    geometry.ROI_JSON.write_text(json.dumps(saved, indent=2), encoding="utf-8")


def draw(img, lane, scale):
    """เปิดหน้าต่างให้คลิกจุดรอบเลน คืนรายการจุด (พิกัดภาพ 720x576) หรือ None ถ้ายกเลิก"""
    pts = []
    state = {"scale": scale, "mouse": None}
    win = "ROI tool"
    others = {k: m for k, m in geometry.lane_masks().items() if k != lane}

    def on_mouse(event, x, y, *_):
        s = state["scale"]
        p = (int(round(x / s)), int(round(y / s)))
        state["mouse"] = p
        if event == cv2.EVENT_LBUTTONDOWN:
            pts.append(p)
        elif event == cv2.EVENT_RBUTTONDOWN and pts:
            pts.pop()

    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        s = state["scale"]
        canvas = img.copy()
        for k, m in others.items():                       # เลนอื่นแสดงจาง ๆ ให้เห็นแนว
            cv2.drawContours(canvas, geometry.mask_outline(m), -1, lane_color(k), 1)
        canvas = cv2.resize(canvas, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR)
        color = lane_color(lane)
        if pts:
            p = (np.array(pts, np.float32) * s).astype(np.int32)
            if len(pts) >= 3:
                fill = canvas.copy()
                cv2.fillPoly(fill, [p], color)
                canvas = cv2.addWeighted(fill, 0.3, canvas, 0.7, 0)
            cv2.polylines(canvas, [p], len(pts) >= 3, color, 2)
            if state["mouse"]:                               # เส้นนำจากจุดล่าสุดไปที่เมาส์
                m = tuple(int(v * s) for v in state["mouse"])
                cv2.line(canvas, tuple(int(v) for v in p[-1]), m, color, 1)
            for q in p:
                cv2.circle(canvas, tuple(int(v) for v in q), 4, (0, 0, 255), -1)
        info = f"lane={lane}  points={len(pts)}  | L-click add  R-click/z undo  r reset  Enter save  Esc cancel  +/- zoom"
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(canvas, info, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        if state["mouse"]:
            cv2.putText(canvas, f"x={state['mouse'][0]} y={state['mouse'][1]}", (8, canvas.shape[0] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow(win, canvas)

        key = cv2.waitKey(30) & 0xFF
        if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:   # กดปิดหน้าต่าง
            return None
        if key in (13, 10, 32):
            if len(pts) >= 3:
                cv2.destroyWindow(win)
                return pts
        elif key in (8, ord("z")) and pts:
            pts.pop()
        elif key == ord("r"):
            pts.clear()
        elif key in (ord("+"), ord("=")):
            state["scale"] = min(3.0, s + 0.25)
        elif key in (ord("-"), ord("_")):
            state["scale"] = max(0.75, s - 0.25)
        elif key == 27:
            cv2.destroyWindow(win)
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draw", nargs="?", const="all", metavar="LANE", help="left / right (ว่าง = ทุกเลน)")
    ap.add_argument("--image", help="ภาพที่ใช้เป็นพื้นตอนวาด (ค่าเริ่มต้น = ภาพถนนว่าง)")
    ap.add_argument("--scale", type=float, default=1.5, help="ขยายหน้าต่าง (ค่าเริ่มต้น 1.5)")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--export-template", action="store_true")
    args = ap.parse_args()
    img = load_frame(Path(args.image).resolve()) if args.image else default_image()

    if args.reset:
        for lane in config.LANE_POLYGONS:
            (config.MASK_DIR / f"{lane}.png").unlink(missing_ok=True)
        geometry.ROI_JSON.unlink(missing_ok=True)
        print("ลบ ROI ที่วาดไว้แล้ว กลับไปใช้ LANE_POLYGONS ใน config.py")

    if args.export_template:
        out = config.MASK_DIR / "template_for_painting.png"
        save_image(out, img)
        print(f"ภาพพื้นสำหรับระบาย ROI -> {out}")

    if args.draw:
        lanes = list(config.LANE_POLYGONS) if args.draw == "all" else [args.draw]
        unknown = [l for l in lanes if l not in config.LANE_POLYGONS]
        if unknown:
            raise SystemExit(f"ไม่รู้จักเลน {unknown} ใช้ได้: {list(config.LANE_POLYGONS)}")
        for lane in lanes:
            print(f"วาดเลน {lane} ...")
            pts = draw(img, lane, args.scale)
            if pts is None:
                print(f"  ยกเลิก เลน {lane} ใช้ ROI เดิม")
                continue
            save_lane(lane, pts)
            print(f"  บันทึก masks/{lane}.png ({len(pts)} จุด): {pts}")
        cv2.destroyAllWindows()
        print("เสร็จแล้ว รัน  python analyze.py --redo --all  เพื่อประมวลผลทุกภาพด้วย ROI ใหม่")

    if args.preview or args.draw or args.reset:
        out = config.RESULT_DIR / "roi_preview.jpg"
        save_image(out, preview(img))
        print(f"ภาพตัวอย่าง ROI -> {out}")
    elif not args.export_template:
        ap.print_help()


if __name__ == "__main__":
    main()
