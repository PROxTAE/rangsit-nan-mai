"""SQLite storage. A frame and its lane results become visible atomically."""
import datetime as dt
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import config
from result_schema import FIELDS, FRAME_FIELDS, METRIC_FIELDS

BANGKOK = dt.timezone(dt.timedelta(hours=7))


def parse_time(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BANGKOK)
    return int(parsed.timestamp())


def iso_time(value):
    return dt.datetime.fromtimestamp(value, BANGKOK).isoformat() if value is not None else None


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY, source_file TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, captured_at INTEGER NOT NULL,
                processed_at REAL, raw_path TEXT, pair_path TEXT,
                overlay_path TEXT, thumbnail_path TEXT, processing_config TEXT,
                brightness REAL, contrast REAL, is_night INTEGER,
                threshold REAL, cam_dx REAL, cam_dy REAL, cam_score REAL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS frames_time ON frames(captured_at, id);
            CREATE TABLE IF NOT EXISTS lane_measurements (
                frame_id INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
                lane TEXT NOT NULL CHECK(lane IN ('left','right')),
                occupancy REAL, occupancy_raw REAL, occ_far REAL, occ_mid REAL,
                occ_near REAL, occupancy_kmeans REAL, edge_density REAL,
                motion REAL, level TEXT NOT NULL CHECK(level IN ('clear','moderate','congested')),
                PRIMARY KEY(frame_id, lane)
            );
            CREATE INDEX IF NOT EXISTS lane_frames ON lane_measurements(lane, frame_id);
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY CHECK(id=1), data_revision INTEGER NOT NULL DEFAULT 0,
                heartbeat REAL, phase TEXT NOT NULL DEFAULT 'stopped',
                phase_at REAL, last_success REAL, last_error TEXT
            );
            INSERT OR IGNORE INTO system_state(id) VALUES(1);
            CREATE VIEW IF NOT EXISTS results AS
                SELECT f.id AS frame_id, f.source_file AS file, f.timestamp,
                    f.captured_at, f.processed_at, f.version,
                    f.raw_path, f.overlay_path, f.thumbnail_path,
                    f.brightness, f.contrast, f.is_night, f.threshold,
                    f.cam_dx, f.cam_dy, f.cam_score,
                    m.lane, m.occupancy, m.occupancy_raw, m.occ_far,
                    m.occ_mid, m.occ_near, m.occupancy_kmeans,
                    m.edge_density, m.motion, m.level
                FROM frames f JOIN lane_measurements m ON f.id=m.frame_id;
        """)


def normalize_row(row):
    missing = set(FIELDS) - set(row)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    result = {key: row[key] for key in FIELDS}
    result['file'] = str(result['file']).replace('\\', '/')
    if result['lane'] not in ('left', 'right') or result['level'] not in config.LEVELS:
        raise ValueError('Invalid lane or level')
    parse_time(str(result['timestamp']))
    for key in FRAME_FIELDS + METRIC_FIELDS:
        if key == 'level':
            continue
        value = result[key]
        value = None if value is None or value == '' else float(value)
        if value is not None and not math.isfinite(value):
            raise ValueError(f'{key} must be finite')
        if key in METRIC_FIELDS and value is not None and not 0 <= value <= 1:
            raise ValueError(f'{key} must be in [0,1]')
        if key == 'is_night' and value not in (0, 1):
            raise ValueError('is_night must be 0 or 1')
        result[key] = value
    if result['occupancy'] is None:
        raise ValueError('occupancy is required')
    return result


def upsert_frame(rows, assets=None, processed_at=None, processing_config=None):
    rows = [normalize_row(row) for row in rows]
    if not rows or len({r['lane'] for r in rows}) != len(rows):
        raise ValueError('A frame needs distinct lane rows')
    first = rows[0]
    shared = ['file', 'timestamp', *FRAME_FIELDS]
    if any(any(r[key] != first[key] for key in shared) for r in rows):
        raise ValueError('Shared frame values do not match between lanes')
    with connect() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT * FROM frames WHERE source_file=?', (first['file'],)).fetchone()
        frame = dict(previous) if previous else {}
        values = dict(timestamp=first['timestamp'], captured_at=parse_time(first['timestamp']),
                      **{key: first[key] for key in FRAME_FIELDS})
        values.update(assets or {})
        if processed_at is not None:
            values['processed_at'] = processed_at
        if processing_config is not None:
            values['processing_config'] = json.dumps(processing_config, sort_keys=True)
        changed = previous is None or any(frame.get(k) != v for k, v in values.items())
        if previous is None:
            values['source_file'] = first['file']
            keys = list(values)
            cursor = conn.execute(f"INSERT INTO frames ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                                  list(values.values()))
            frame_id = cursor.lastrowid
        else:
            frame_id = frame['id']
            if changed:
                conn.execute(f"UPDATE frames SET {','.join(k+'=?' for k in values)},version=version+1 WHERE id=?",
                             [*values.values(), frame_id])
        for row in rows:
            old = conn.execute('SELECT * FROM lane_measurements WHERE frame_id=? AND lane=?',
                               (frame_id, row['lane'])).fetchone()
            metric = [row[key] for key in METRIC_FIELDS]
            if old is None or any(old[key] != row[key] for key in METRIC_FIELDS):
                changed = True
                conn.execute(f"INSERT INTO lane_measurements(frame_id,lane,{','.join(METRIC_FIELDS)}) "
                             f"VALUES({','.join('?' for _ in range(2+len(metric)))}) "
                             f"ON CONFLICT(frame_id,lane) DO UPDATE SET "
                             f"{','.join(k+'=excluded.'+k for k in METRIC_FIELDS)}",
                             [frame_id, row['lane'], *metric])
        if changed:
            if previous is not None and all(frame.get(k) == v for k, v in values.items()):
                conn.execute('UPDATE frames SET version=version+1 WHERE id=?', (frame_id,))
            conn.execute('UPDATE system_state SET data_revision=data_revision+1 WHERE id=1')
        revision = conn.execute('SELECT data_revision FROM system_state WHERE id=1').fetchone()[0]
        return frame_id, changed, revision


def worker_state(phase=None, error=None, success=False):
    values = {'heartbeat': time.time()}
    if phase is not None:
        values.update(phase=phase, phase_at=time.time(), last_error=error)
    if success:
        values['last_success'] = time.time()
    with connect() as conn, conn:
        conn.execute(f"UPDATE system_state SET {','.join(k+'=?' for k in values)} WHERE id=1", list(values.values()))


def state():
    with connect() as conn:
        result = dict(conn.execute('SELECT * FROM system_state WHERE id=1').fetchone())
        result['latest_at'] = conn.execute('SELECT MAX(captured_at) FROM frames f WHERE '
            '(SELECT COUNT(*) FROM lane_measurements m WHERE m.frame_id=f.id)=2').fetchone()[0]
    now = time.time()
    result['worker_online'] = bool(result['heartbeat'] and now-result['heartbeat'] < 30 and result['phase'] != 'stopped')
    result['data_age_sec'] = max(0, round(now-result['latest_at'])) if result['latest_at'] else None
    result['stale'] = result['data_age_sec'] is None or result['data_age_sec'] > config.WEB_STALE_SEC
    return result


def safe_media_path(value):
    if not value:
        return None
    path = (config.ROOT / value).resolve()
    allowed = (config.RAW_DIR, config.OVERLAY_DIR, config.THUMB_DIR)
    if not any(path.is_relative_to(root.resolve()) for root in allowed):
        return None
    return path if path.is_file() else None


def media_urls(row):
    frame_id, version = row['frame_id'], row['version']
    return {variant: f'/api/frames/{frame_id}/image?variant={variant}&v={version}'
            if safe_media_path(row.get(key)) else None
            for variant, key in [('raw', 'raw_path'), ('overlay', 'overlay_path'), ('thumbnail', 'thumbnail_path')]}


def serialize(row):
    result = {key: row[key] for key in FIELDS}
    result.update(frame_id=row['frame_id'], version=row['version'],
                  captured_at=iso_time(row['captured_at']), processed_at=iso_time(row['processed_at']),
                  images=media_urls(row))
    return result
