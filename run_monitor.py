"""Collect and analyze continuously; publish completed measurements to SQLite."""
import argparse
import hashlib
import logging
import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager

import cv2
import requests

import config
from collector import collect_once, next_wait
from pipeline import Pipeline, draw_overlay
from preprocess import is_night, load_frame
from web import db
from web.media import atomic_image, relative

log = logging.getLogger('traffic.worker')


@contextmanager
def worker_lock():
    path = config.DB_PATH.with_suffix('.worker.lock')
    with open(path, 'a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another traffic worker is already running') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def heartbeat(stop):
    while not stop.wait(10):
        try:
            db.worker_state()
        except Exception:
            log.exception('Heartbeat failed')


def process_path(pipe, path):
    db.worker_state('processing')
    rows, vis = pipe.process_frame(path)
    version = uuid.uuid4().hex[:12]
    overlay = config.OVERLAY_DIR / path.parent.name / f'{path.stem}-web-{version}.jpg'
    thumb = config.THUMB_DIR / path.parent.name / f'{path.stem}-web-{version}.jpg'
    atomic_image(overlay, draw_overlay(vis['img'], vis['binary'], rows, vis['masks']), config.OVERLAY_JPEG_QUALITY)
    image = vis['img']
    atomic_image(thumb, cv2.resize(image, (240, round(image.shape[0]*240/image.shape[1]))))
    pair = path.with_name(path.stem+'_b.jpg')
    settings = {key: getattr(config, key) for key in ['OCC_MODERATE', 'OCC_CONGESTED', 'MOTION_STOPPED',
                'MOTION_FLOWING', 'BG_DAY_FILE', 'CAMERA_SHIFT_TOL', 'LANDMARK_MIN_SCORE',
                'RELIGHT_BAND', 'RELIGHT_MAX', 'INTERVAL_SEC', 'CAPTURE_PAIR', 'MOTION_MAX_GAP_SEC',
                'NIGHT_BRIGHTNESS', 'NIGHT_DARK_FRAC', 'NIGHT_BG_PERCENTILE']}
    roi_file = config.MASK_DIR / 'rois.json'
    settings['roi_sha256'] = hashlib.sha256(roi_file.read_bytes()).hexdigest() if roi_file.exists() else None
    result = db.upsert_frame(rows, dict(raw_path=relative(path), pair_path=relative(pair) if pair.exists() else None,
                             overlay_path=relative(overlay), thumbnail_path=relative(thumb)),
                             processed_at=time.time(), processing_config=settings)
    db.worker_state('idle', success=True)
    log.info('Published frame=%s revision=%s %s', result[0], result[2],
             ' / '.join(f"{r['lane']} {r['occupancy']:.1%} {r['level']}" for r in rows))
    return result


def pending_frames():
    with db.connect() as conn:
        done = {r[0] for r in conn.execute('SELECT source_file FROM frames f WHERE '
                '(SELECT COUNT(*) FROM lane_measurements m WHERE m.frame_id=f.id)=2')}
    return [p for p in sorted(config.RAW_DIR.glob('*/*.jpg')) if not p.stem.endswith('_b')
            and relative(p) not in done and (not config.CAPTURE_PAIR or p.with_name(p.stem+'_b.jpg').exists()
                or time.time()-p.stat().st_mtime > config.PAIR_TIMEOUT_SEC+30)]


def redo_night(pipe):
    """ประมวลผลภาพกลางคืนที่บันทึกไปแล้วใหม่ (หลังสร้าง/แก้ bg_night.png หรือเกณฑ์กลางคืน)"""
    frames = [p for p in sorted(config.RAW_DIR.glob('*/*.jpg'))
              if not p.stem.endswith('_b') and is_night(load_frame(p))]
    log.info('Reprocessing %d night frames', len(frames))
    for path in frames:
        process_path(pipe, path)


def recover_pending(pipe, retry_after=None):
    retry_after = retry_after if retry_after is not None else {}
    errors = []
    for path in pending_frames():
        if retry_after.get(path, 0) > time.monotonic():
            errors.append(f'Waiting to retry unreadable frame: {path.name}')
            continue
        try:
            process_path(pipe, path)
            retry_after.pop(path, None)
        except Exception as exc:
            log.exception('Recovery failed: %s', path)
            retry_after[path] = time.monotonic()+60
            errors.append(str(exc))
    if errors:
        db.worker_state('error', error=errors[-1])
    return not errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=config.INTERVAL_SEC)
    parser.add_argument('--count', type=int, default=0, help='0 means run continuously')
    parser.add_argument('--pair', action=argparse.BooleanOptionalAction, default=config.CAPTURE_PAIR)
    parser.add_argument('--recover-only', action='store_true', help='Analyze existing pending images, then exit')
    parser.add_argument('--redo-night', action='store_true', help='Re-analyze every stored night frame, then exit')
    parser.add_argument('--watch-only', action='store_true', help='Analyze files from an already-running collector; do not fetch images')
    args = parser.parse_args()
    if args.interval < 1 or args.count < 0:
        parser.error('interval must be positive; count must be nonnegative')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    db.init_db()
    with worker_lock():
        stop = threading.Event()
        thread = threading.Thread(target=heartbeat, args=(stop,), daemon=True)
        db.worker_state('starting')
        thread.start()
        try:
            pipe = Pipeline()
            pipe.background(False)  # fail clearly before collecting if setup is incomplete
            recover_pending(pipe)
            if args.redo_night:
                redo_night(pipe)
            if args.recover_only or args.redo_night:
                return
            if args.watch_only:
                retry_after = {}
                while True:
                    if recover_pending(pipe, retry_after):
                        db.worker_state('watching')
                    time.sleep(2)
            with requests.Session() as session:
                last_hash = None
                # Preserve duplicate detection after restarting the worker.
                recent = sorted(config.RAW_DIR.glob('*/*.jpg'))
                if recent:
                    last_hash = hashlib.md5(recent[-1].read_bytes()).hexdigest()
                cycles = 0
                while True:
                    started = time.monotonic()
                    try:
                        # A frame interrupted during pair capture may only become ready
                        # after startup; retry it on subsequent cycles as well.
                        recover_pending(pipe)
                        result = collect_once(session, last_hash, not args.pair, db.worker_state)
                        last_hash = result['last_hash']
                        if result['path']:
                            process_path(pipe, result['path'])
                        else:
                            db.worker_state(result['status'], error='Cannot fetch camera snapshot' if result['status'] == 'fetch_error' else None)
                            if result['status'] != 'duplicate':  # ภาพซ้ำเกิดแทบทุกรอบ ไม่ต้อง log
                                log.info('Collection: %s', result['status'])
                    except Exception as exc:
                        log.exception('Collection/analysis failed')
                        db.worker_state('error', error=str(exc))
                        result = {'status': 'error'}
                    cycles += 1
                    if args.count and cycles >= args.count:
                        break
                    time.sleep(next_wait(result['status'], args.interval, time.monotonic()-started))
        finally:
            stop.set()
            thread.join(timeout=2)
            db.worker_state('stopped')


if __name__ == '__main__':
    def request_stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, request_stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
