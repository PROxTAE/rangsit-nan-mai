"""⑥–⑧ ประมวลผลทุกภาพ -> results/occupancy.csv -> กราฟสรุป

ใช้งาน:
    python analyze.py                 # ประมวลผลภาพใหม่ที่ยังไม่อยู่ใน CSV + บันทึกภาพ overlay
    python analyze.py --charts        # สร้างกราฟ 24 ชม., heatmap, สรุปช่วงเวลารถติด/โล่ง
    python analyze.py --all           # ทั้งสองอย่าง
    python analyze.py --redo          # ประมวลผลใหม่ทั้งหมด (หลังแก้ ROI/threshold)
"""
import argparse
import csv
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config
from pipeline import Pipeline, draw_overlay
from preprocess import save_image
from result_schema import FIELDS

plt.rcParams["font.family"] = ["Tahoma", "Leelawadee UI", "Leelawadee", "DejaVu Sans"]

LEVEL_COLOR = {"clear": "#3fae49", "moderate": "#f5a623", "congested": "#d0312d"}


def list_frames():
    """ภาพหลักทั้งหมด ยกเว้นภาพล่าสุดที่ collector ยังรอภาพคู่อยู่ (เมื่อเปิด CAPTURE_PAIR จะประมวลผลรอบหน้า)"""
    now = time.time()
    return sorted(p for p in config.RAW_DIR.glob("*/*.jpg")
                  if not p.stem.endswith("_b")
                  and (not config.CAPTURE_PAIR or p.with_name(p.stem + "_b.jpg").exists()
                       or now - p.stat().st_mtime > config.PAIR_TIMEOUT_SEC + 30))


def process(redo=False, overlays=True):
    config.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    done = set()
    if config.OCCUPANCY_CSV.exists() and not redo:
        done = set(pd.read_csv(config.OCCUPANCY_CSV)["file"])
    frames = [p for p in list_frames()
              if str(p.relative_to(config.ROOT)).replace("\\", "/") not in done]
    if not frames:
        print("ไม่มีภาพใหม่ให้ประมวลผล")
        return
    pipe = Pipeline()
    mode = "w" if redo or not config.OCCUPANCY_CSV.exists() else "a"
    with open(config.OCCUPANCY_CSV, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if mode == "w":
            w.writeheader()
        for i, path in enumerate(frames, 1):
            try:
                rows, vis = pipe.process_frame(path)
            except Exception as e:  # ภาพเสีย/อ่านไม่ได้ ข้ามไป
                print(f"  ! {path.name}: {e}")
                continue
            w.writerows(rows)
            if overlays:
                out = config.OVERLAY_DIR / path.parent.name / f"{path.stem}.jpg"
                save_image(out, draw_overlay(vis["img"], vis["binary"], rows, vis["masks"]),
                           config.OVERLAY_JPEG_QUALITY)
            summary = "  ".join(f"{r['lane']}={r['occupancy']*100:4.1f}% {r['level']}" for r in rows)
            print(f"[{i}/{len(frames)}] {rows[0]['timestamp']}  {summary}")
    print(f"บันทึก -> {config.OCCUPANCY_CSV}")


# ---------------------------------------------------------------- charts
def load_results():
    df = pd.read_csv(config.OCCUPANCY_CSV, parse_dates=["timestamp"])
    df["hour"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date
    df["tod"] = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60
    return df


def chart_timeline(df):
    """กราฟ occupancy ตลอดวัน แยกเลน (แต่ละวันเป็นหนึ่งเส้น)"""
    lanes = list(config.LANE_POLYGONS)
    fig, axes = plt.subplots(len(lanes), 1, figsize=(12, 3.2 * len(lanes)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, lane in zip(axes, lanes):
        d = df[df.lane == lane]
        for date, g in d.groupby("date"):
            g = g.sort_values("tod")
            ax.plot(g["tod"], g["occupancy"] * 100, marker=".", lw=1.2, label=str(date))
            night = g[g.is_night == 1]
            ax.scatter(night["tod"], night["occupancy"] * 100, s=12, c="k", zorder=3)
        ax.axhspan(0, config.OCC_MODERATE * 100, color=LEVEL_COLOR["clear"], alpha=0.08)
        ax.axhspan(config.OCC_MODERATE * 100, config.OCC_CONGESTED * 100, color=LEVEL_COLOR["moderate"], alpha=0.08)
        ax.axhspan(config.OCC_CONGESTED * 100, 100, color=LEVEL_COLOR["congested"], alpha=0.08)
        ax.set_ylim(0, max(60, d["occupancy"].max() * 110 if len(d) else 60))
        ax.set_ylabel("occupancy (%)")
        ax.set_title(f"เลน{config.LANE_NAMES_TH[lane]}")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left", fontsize=8)
    axes[-1].set_xlim(0, 24)
    axes[-1].set_xticks(range(0, 25, 2))
    axes[-1].set_xlabel("เวลา (ชั่วโมง)  · จุดดำ = ภาพกลางคืน")
    fig.suptitle("ความหนาแน่นจราจรตลอด 24 ชั่วโมง", fontsize=14)
    fig.tight_layout()
    return fig


def chart_heatmap(df):
    """heatmap ค่าเฉลี่ย occupancy ราย ชั่วโมง x เลน (และ x วัน ถ้ามีหลายวัน)"""
    lanes = list(config.LANE_POLYGONS)
    fig, axes = plt.subplots(len(lanes), 1, figsize=(12, 1.2 + 0.5 * df["date"].nunique() * len(lanes) + 1.5),
                             squeeze=False)
    for ax, lane in zip(axes[:, 0], lanes):
        piv = (df[df.lane == lane].pivot_table(index="date", columns="hour", values="occupancy", aggfunc="mean")
               .reindex(columns=range(24)))
        im = ax.imshow(piv.values * 100, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=60)
        ax.set_yticks(range(len(piv.index)))
        ax.set_yticklabels([str(d) for d in piv.index])
        ax.set_xticks(range(24))
        ax.set_title(f"เลน{config.LANE_NAMES_TH[lane]}")
        for (r, c), v in np.ndenumerate(piv.values):
            if not np.isnan(v):
                ax.text(c, r, f"{v*100:.0f}", ha="center", va="center", fontsize=7)
        fig.colorbar(im, ax=ax, label="occupancy (%)")
    axes[-1, 0].set_xlabel("ชั่วโมง")
    fig.suptitle("Heatmap ความหนาแน่นเฉลี่ยรายชั่วโมง", fontsize=14)
    fig.tight_layout()
    return fig


def chart_levels(df):
    """สัดส่วนระดับ โล่ง/ปานกลาง/ติด ในแต่ละชั่วโมง"""
    lanes = list(config.LANE_POLYGONS)
    fig, axes = plt.subplots(1, len(lanes), figsize=(13, 4), sharey=True)
    for ax, lane in zip(np.atleast_1d(axes), lanes):
        ct = (pd.crosstab(df[df.lane == lane]["hour"], df[df.lane == lane]["level"], normalize="index")
              .reindex(index=range(24), columns=config.LEVELS).fillna(0))
        bottom = np.zeros(24)
        for lvl in config.LEVELS:
            ax.bar(ct.index, ct[lvl] * 100, bottom=bottom, color=LEVEL_COLOR[lvl], label=config.LEVEL_TH[lvl])
            bottom += ct[lvl].values * 100
        ax.set_title(f"เลน{config.LANE_NAMES_TH[lane]}")
        ax.set_xlabel("ชั่วโมง")
        ax.set_xticks(range(0, 24, 2))
    np.atleast_1d(axes)[0].set_ylabel("% ของภาพ")
    np.atleast_1d(axes)[0].legend()
    fig.suptitle("สัดส่วนระดับการจราจรรายชั่วโมง", fontsize=14)
    fig.tight_layout()
    return fig


def method_compare(df):
    """เทียบ occupancy วิธี threshold กับ K-means"""
    d = df.dropna(subset=["occupancy_kmeans"])
    fig, ax = plt.subplots(figsize=(5, 5))
    for lane, g in d.groupby("lane"):
        ax.scatter(g["occupancy"] * 100, g["occupancy_kmeans"] * 100, s=14, label=lane)
    lim = max(10, d[["occupancy", "occupancy_kmeans"]].max().max() * 105)
    ax.plot([0, lim], [0, lim], "k--", lw=0.8)
    ax.set_xlim(0, lim); ax.set_ylim(0, lim)
    ax.set_xlabel("absdiff + Otsu (%)"); ax.set_ylabel("K-means (%)")
    ax.set_title("เทียบสองวิธีแบ่งส่วน")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def summary_text(df):
    lines = [f"ข้อมูล {df['timestamp'].min():%Y-%m-%d %H:%M} ถึง {df['timestamp'].max():%Y-%m-%d %H:%M}"
             f"  ({df['file'].nunique()} ภาพ, {df['date'].nunique()} วัน)", ""]
    for lane in config.LANE_POLYGONS:
        d = df[df.lane == lane]
        hourly = d.groupby("hour")["occupancy"].mean().sort_values()
        lines.append(f"เลน{config.LANE_NAMES_TH[lane]}")
        lines.append(f"  occupancy เฉลี่ย {d['occupancy'].mean()*100:.1f}%  สูงสุด {d['occupancy'].max()*100:.1f}%"
                     f" ({d.loc[d['occupancy'].idxmax(), 'timestamp']:%Y-%m-%d %H:%M})")
        lines.append("  ชั่วโมงที่ติดที่สุด: " + ", ".join(f"{h:02d}:00 ({v*100:.0f}%)" for h, v in hourly[::-1][:3].items()))
        lines.append("  ชั่วโมงที่โล่งที่สุด: " + ", ".join(f"{h:02d}:00 ({v*100:.0f}%)" for h, v in hourly[:3].items()))
        counts = d["level"].value_counts(normalize=True)
        lines.append("  สัดส่วน: " + "  ".join(f"{config.LEVEL_TH[l]} {counts.get(l, 0)*100:.0f}%" for l in config.LEVELS))
        lines.append("")
    n_night = df[df.is_night == 1]["file"].nunique()
    if n_night:
        lines.append(f"หมายเหตุ: มี {n_night} ภาพกลางคืน ผลอาจคลาดเพราะไฟหน้ารถ/แสงน้อย")
    moved = df[(df.cam_dx.abs() > config.CAMERA_SHIFT_TOL) | (df.cam_dy.abs() > config.CAMERA_SHIFT_TOL)
               | (df.cam_score < config.LANDMARK_MIN_SCORE)]["file"].nunique()
    if moved:
        lines.append(f"หมายเหตุ: มี {moved} ภาพที่กล้องขยับหรือจุดสังเกตถูกบัง")
    return "\n".join(lines)


def charts():
    df = load_results()
    config.CHART_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in (("timeline_24h", chart_timeline), ("heatmap_hourly", chart_heatmap),
                     ("levels_by_hour", chart_levels), ("threshold_vs_kmeans", method_compare)):
        fig = fn(df)
        out = config.CHART_DIR / f"{name}.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print(f"กราฟ -> {out}")
    text = summary_text(df)
    (config.RESULT_DIR / "summary.txt").write_text(text, encoding="utf-8")
    print("\n" + text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charts", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--no-overlay", action="store_true")
    args = ap.parse_args()
    if not args.charts or args.all:
        process(redo=args.redo, overlays=not args.no_overlay)
    if args.charts or args.all:
        charts()


if __name__ == "__main__":
    main()
