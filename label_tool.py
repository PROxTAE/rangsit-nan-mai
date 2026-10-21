"""ติดป้ายระดับรถติดด้วยตาคน (ground truth) -> results/labels.csv

ใช้งาน:
    python label_tool.py              # ติดป้ายภาพที่ยังไม่มีป้าย (สุ่มลำดับ)
    python label_tool.py --limit 100

หน้าต่างจะแสดงทีละเลน (กรอบสีเขียว)
    1 = clear (โล่ง)   2 = moderate (ปานกลาง)   3 = congested (ติด)
    s = ข้ามภาพนี้      q = เลิก (บันทึกที่ทำไปแล้ว)
"""
import argparse
import csv
import random

import cv2

import config
import geometry
from preprocess import load_frame

KEYS = {ord("1"): "clear", ord("2"): "moderate", ord("3"): "congested"}


def load_labels():
    if not config.LABELS_CSV.exists():
        return {}
    with open(config.LABELS_CSV, encoding="utf-8") as f:
        return {(r["file"], r["lane"]): r["label"] for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    labels = load_labels()
    masks = geometry.lane_masks()
    frames = sorted(p for p in config.RAW_DIR.glob("*/*.jpg") if not p.stem.endswith("_b"))
    rel = lambda p: str(p.relative_to(config.ROOT)).replace("\\", "/")
    todo = [p for p in frames if any((rel(p), lane) not in labels for lane in config.LANE_POLYGONS)]
    random.Random(args.seed).shuffle(todo)
    todo = todo[:args.limit]
    print(f"มี {len(todo)} ภาพที่ต้องติดป้าย")

    config.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not config.LABELS_CSV.exists()
    with open(config.LABELS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "lane", "label"])
        if new_file:
            w.writeheader()
        for i, path in enumerate(todo, 1):
            img = load_frame(path)
            for lane, m in masks.items():
                if (rel(path), lane) in labels:
                    continue
                show = img.copy()
                cv2.drawContours(show, geometry.mask_outline(m), -1, (0, 255, 0), 2)
                cv2.putText(show, f"[{i}/{len(todo)}] lane={lane}   1=clear 2=moderate 3=congested  s=skip q=quit",
                            (10, 568), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
                cv2.imshow("label", cv2.resize(show, None, fx=1.5, fy=1.5))
                while True:
                    k = cv2.waitKey(0) & 0xFF
                    if k in KEYS or k in (ord("s"), ord("q")):
                        break
                if k == ord("q"):
                    cv2.destroyAllWindows()
                    print("บันทึกแล้ว")
                    return
                if k == ord("s"):
                    break
                w.writerow({"file": rel(path), "lane": lane, "label": KEYS[k]})
                f.flush()
    cv2.destroyAllWindows()
    print(f"เสร็จ -> {config.LABELS_CSV}")


if __name__ == "__main__":
    main()
