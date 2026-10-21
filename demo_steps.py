"""ภาพอธิบายทีละขั้นสำหรับรายงาน/สไลด์ -> results/charts/pipeline_steps.png

ใช้งาน:
    python demo_steps.py                                   # ใช้ภาพล่าสุด
    python demo_steps.py data/raw/2026-10-21/113952.jpg
"""
import sys
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import config
import features
import geometry
import segment
from pipeline import Pipeline, draw_overlay
from preprocess import load_frame, preprocess

plt.rcParams["font.family"] = ["Tahoma", "Leelawadee UI", "Leelawadee", "DejaVu Sans"]


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).resolve()
    else:
        path = sorted(p for p in config.RAW_DIR.glob("*/*.jpg") if not p.stem.endswith("_b"))[-1]
    pipe = Pipeline()
    rows, vis = pipe.process_frame(path)
    img = vis["img"]
    bg = pipe.background(bool(rows[0]["is_night"]))
    raw_bin = ((vis["diff"] > rows[0]["threshold"]).astype(np.uint8) * 255)
    gray = preprocess(img)
    edges = features.edge_map(gray)
    sat = features.saturation(img)

    lanes = list(config.LANE_POLYGONS)
    bev = np.hstack([cv2.cvtColor(geometry.birdseye(img, pipe.masks[l]), cv2.COLOR_BGR2RGB) for l in lanes])
    bev_bin = np.hstack([geometry.birdseye(vis["binary"], pipe.masks[l]) for l in lanes])

    rgb = lambda a: cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
    panels = [
        (rgb(img), "1. ภาพจากกล้อง", None),
        (rgb(bg), "2. ภาพถนนว่าง (จัดแนวแล้ว)", None),
        (gray, "3. grayscale + median filter\n(ลบตัวหนังสือเวลา)", "gray"),
        (sat, "4. ช่อง S (HSV)", "gray"),
        (edges, "5. ขอบ Sobel", "gray"),
        (vis["diff"], f"6. |ภาพ − พื้นหลัง| ใน ROI", "inferno"),
        (raw_bin, f"7. Otsu threshold (T={rows[0]['threshold']:.0f})", "gray"),
        (vis["binary"], "8. + ขอบเสริม, opening/closing", "gray"),
        (vis["kmeans"], "9. เทียบ: K-means (K=2)", "gray"),
        (rgb(draw_overlay(img, vis["binary"], rows, vis["masks"], vis["glare"])), "10. ผลลัพธ์", None),
        (bev, "11. bird's-eye (ซ้าย | ขวา)", None),
        (bev_bin, "12. mask รถใน bird's-eye", "gray"),
    ]
    fig, axes = plt.subplots(3, 4, figsize=(18, 13))
    for ax, (im, title, cmap) in zip(axes.ravel(), panels):
        ax.imshow(im, cmap=cmap)
        ax.set_title(title, fontsize=11)
        ax.axis("off")
    summary = "   ".join(f"{r['lane']}: occupancy {r['occupancy']*100:.1f}% → {config.LEVEL_TH[r['level']]}"
                         for r in rows)
    fig.suptitle(f"{rows[0]['timestamp']}    {summary}", fontsize=14)
    fig.tight_layout()
    config.CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CHART_DIR / "pipeline_steps.png"
    fig.savefig(out, dpi=110)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
