"""วัดผล: เทียบระดับที่ระบบทำนายกับป้ายที่คนติด (results/labels.csv)

- confusion matrix + accuracy ของวิธี absdiff+Otsu และ K-means
- ค้นหาเกณฑ์ occupancy (โล่ง/ปานกลาง/ติด) ที่ให้ accuracy สูงสุด เพื่อนำไปแก้ใน config.py

ใช้งาน:  python evaluate.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config

plt.rcParams["font.family"] = ["Tahoma", "Leelawadee UI", "Leelawadee", "DejaVu Sans"]


def to_level(occ, t1, t2):
    return np.where(occ < t1, "clear", np.where(occ > t2, "congested", "moderate"))


def confusion(y_true, y_pred):
    idx = {l: i for i, l in enumerate(config.LEVELS)}
    cm = np.zeros((3, 3), int)
    for t, p in zip(y_true, y_pred):
        cm[idx[t], idx[p]] += 1
    return cm


def best_thresholds(occ, y):
    best = (0, config.OCC_MODERATE, config.OCC_CONGESTED)
    for t1 in np.arange(0.02, 0.50, 0.01):
        for t2 in np.arange(t1 + 0.02, 0.80, 0.01):
            acc = float(np.mean(to_level(occ, t1, t2) == y))
            if acc > best[0]:
                best = (acc, round(t1, 2), round(t2, 2))
    return best


def plot_cm(ax, cm, title):
    ax.imshow(cm, cmap="Blues")
    names = [config.LEVEL_TH[l] for l in config.LEVELS]
    ax.set_xticks(range(3)); ax.set_xticklabels(names)
    ax.set_yticks(range(3)); ax.set_yticklabels(names)
    ax.set_xlabel("ระบบทำนาย"); ax.set_ylabel("คนติดป้าย")
    for (r, c), v in np.ndenumerate(cm):
        ax.text(c, r, v, ha="center", va="center", color="white" if v > cm.max() / 2 else "black")
    ax.set_title(title)


def main():
    if not config.LABELS_CSV.exists():
        raise SystemExit("ยังไม่มี results/labels.csv ให้รัน python label_tool.py ก่อน")
    occ = pd.read_csv(config.OCCUPANCY_CSV)
    lab = pd.read_csv(config.LABELS_CSV).drop_duplicates(["file", "lane"], keep="last")
    df = lab.merge(occ, on=["file", "lane"], how="inner")
    if df.empty:
        raise SystemExit("ไม่มีภาพที่ทั้งติดป้ายแล้วและประมวลผลแล้ว ให้รัน python analyze.py ก่อน")
    print(f"ภาพที่ใช้ประเมิน: {len(df)} (เลน x ภาพ)\n")

    y = df["label"].values
    methods = {
        "absdiff + Otsu (+motion)": df["level"].values,
        "absdiff + Otsu (occupancy only)": to_level(df["occupancy"].values, config.OCC_MODERATE, config.OCC_CONGESTED),
        "K-means": to_level(df["occupancy_kmeans"].astype(float).values, config.OCC_MODERATE, config.OCC_CONGESTED),
    }
    fig, axes = plt.subplots(1, len(methods), figsize=(5 * len(methods), 4.5))
    lines = []
    for ax, (name, pred) in zip(axes, methods.items()):
        cm = confusion(y, pred)
        acc = np.trace(cm) / cm.sum()
        plot_cm(ax, cm, f"{name}\naccuracy {acc*100:.1f}%")
        lines.append(f"{name:34s} accuracy = {acc*100:5.1f}%")
    fig.tight_layout()
    config.CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CHART_DIR / "confusion_matrix.png"
    fig.savefig(out, dpi=130)
    print("\n".join(lines))
    print(f"\nconfusion matrix -> {out}")

    for col in ("occupancy", "occupancy_kmeans"):
        acc, t1, t2 = best_thresholds(df[col].astype(float).values, y)
        print(f"เกณฑ์ที่ดีที่สุดสำหรับ {col}: โล่ง < {t1:.2f}, ติด > {t2:.2f}  (accuracy {acc*100:.1f}%)")
    print("หมายเหตุ: ถ้าเปลี่ยนเกณฑ์ใน config.py ให้รัน python analyze.py --redo --all อีกครั้ง")

    # การกระจายของ occupancy แยกตามป้าย ใช้ดูว่าเกณฑ์ควรอยู่ตรงไหน
    fig, ax = plt.subplots(figsize=(7, 4))
    for lvl in config.LEVELS:
        v = df[df.label == lvl]["occupancy"] * 100
        if len(v):
            ax.hist(v, bins=20, alpha=0.6, label=config.LEVEL_TH[lvl])
    for t in (config.OCC_MODERATE, config.OCC_CONGESTED):
        ax.axvline(t * 100, color="k", ls="--")
    ax.set_xlabel("occupancy (%)"); ax.set_ylabel("จำนวน")
    ax.set_title("การกระจาย occupancy แยกตามป้ายที่คนติด")
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.CHART_DIR / "occupancy_by_label.png", dpi=130)


if __name__ == "__main__":
    main()
