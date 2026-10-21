"""Bounded, indexed read queries and database-side aggregates."""
import json

import config
from web import db


def where(start, end, lane='all', level=None):
    clause, args = 'captured_at>=? AND captured_at<?', [start, end]
    if lane != 'all':
        clause += ' AND lane=?'
        args.append(lane)
    if level:
        clause += ' AND level=?'
        args.append(level)
    return clause, args


def bucket_for(start, end):
    """ช่วงสั้นแสดงทุกเฟรม ช่วงยาวเฉลี่ยรายชั่วโมง/รายวัน กันจำนวนจุดบนกราฟเยอะเกิน"""
    return 0 if end-start <= 2*86400 else (3600 if end-start <= 35*86400 else 86400)


def series(conn, clause, args, bucket):
    if bucket:
        sql = f'''SELECT lane,((captured_at+25200)/{bucket})*{bucket}-25200 t,
            AVG(occupancy) occupancy,COUNT(*) sample_count FROM results WHERE {clause}
            GROUP BY lane,t ORDER BY t,lane'''
    else:
        sql = f'''SELECT lane,captured_at t,occupancy,1 sample_count
            FROM results WHERE {clause} ORDER BY captured_at,lane'''
    rows = [dict(r) for r in conn.execute(sql, args)]
    for item in rows:
        item['timestamp'] = db.iso_time(item.pop('t'))
    return rows


def trend(start, end, lane):
    """เฉพาะเส้นแนวโน้มของช่วงเวลาที่ผู้ใช้ซูม/เลื่อนดู ช่วงสั้นจึงได้ทุกเฟรมแม้ภาพรวมจะเลือกหลายวัน"""
    clause, args = where(start, end, lane)
    bucket = bucket_for(start, end)
    with db.connect() as conn:
        conn.execute('BEGIN')
        revision = conn.execute('SELECT data_revision FROM system_state WHERE id=1').fetchone()[0]
        rows = series(conn, clause, args, bucket)
    return dict(revision=revision, series=rows, bucket_seconds=bucket, lane=lane,
                range={'from': db.iso_time(start), 'to': db.iso_time(end)})


def dashboard(start, end, lane):
    clause, args = where(start, end, lane)
    bucket = bucket_for(start, end)
    with db.connect() as conn:
        conn.execute('BEGIN')
        revision = conn.execute('SELECT data_revision FROM system_state WHERE id=1').fetchone()[0]
        latest_id = conn.execute('SELECT f.id FROM frames f WHERE '
                                 '(SELECT COUNT(*) FROM lane_measurements m WHERE m.frame_id=f.id)=2 '
                                 'ORDER BY f.captured_at DESC, f.id DESC LIMIT 1').fetchone()
        latest = [db.serialize(dict(r)) for r in conn.execute('SELECT * FROM results WHERE frame_id=? ORDER BY lane',
                  (latest_id[0],))] if latest_id else []
        stats = []
        for row in conn.execute(f'''SELECT lane,COUNT(*) sample_count,AVG(occupancy) mean,
                MAX(occupancy) peak, SUM(level='clear') clear, SUM(level='moderate') moderate,
                SUM(level='congested') congested FROM results WHERE {clause} GROUP BY lane''', args):
            stat = dict(row)
            stat['peak_at'] = db.iso_time(conn.execute(f'SELECT captured_at FROM results WHERE {clause} AND lane=? '
                'ORDER BY occupancy DESC,captured_at DESC LIMIT 1', [*args, stat['lane']]).fetchone()[0])
            peak_hour = conn.execute(f'''SELECT strftime('%H',captured_at,'unixepoch','+7 hours') hour,
                AVG(occupancy) mean,COUNT(*) sample_count FROM results WHERE {clause} AND lane=?
                GROUP BY hour ORDER BY mean DESC,hour LIMIT 1''', [*args, stat['lane']]).fetchone()
            stat['peak_hour'] = dict(peak_hour) if peak_hour else None
            stats.append(stat)
        timeline = series(conn, clause, args, bucket)
        # This panel compares both sides independently of the lane filter used
        # by the timeline/history. Its date range still follows the dashboard.
        heat_clause, heat_args = where(start, end)
        heatmap = [dict(r) for r in conn.execute(f'''SELECT lane,
            date(captured_at,'unixepoch','+7 hours') date,
            CAST(strftime('%H',captured_at,'unixepoch','+7 hours') AS INTEGER) hour,
            AVG(occupancy) occupancy, COUNT(*) sample_count FROM results WHERE {heat_clause}
            GROUP BY lane,date,hour ORDER BY date,hour,lane''', heat_args)]
        # Differences use matching frames, so incomplete historical samples
        # cannot make one side appear artificially busier than the other.
        comparison = [dict(r) for r in conn.execute('''SELECT
            date(f.captured_at,'unixepoch','+7 hours') date,
            CAST(strftime('%H',f.captured_at,'unixepoch','+7 hours') AS INTEGER) hour,
            AVG(l.occupancy) AS left, AVG(r.occupancy) AS right,
            AVG(l.occupancy-r.occupancy) difference, COUNT(*) sample_count
            FROM frames f
            JOIN lane_measurements l ON l.frame_id=f.id AND l.lane='left'
            JOIN lane_measurements r ON r.frame_id=f.id AND r.lane='right'
            WHERE f.captured_at>=? AND f.captured_at<?
            GROUP BY date,hour ORDER BY date,hour''', [start, end])]
        frames = conn.execute(f'SELECT COUNT(DISTINCT frame_id) FROM results WHERE {clause}', args).fetchone()[0]
    return dict(revision=revision, latest=latest, summary=stats, series=timeline, heatmap=heatmap,
                heatmap_comparison=comparison,
                frame_count=frames, bucket_seconds=bucket, timezone='Asia/Bangkok',
                range={'from': db.iso_time(start), 'to': db.iso_time(end)}, lane=lane)


def history(start, end, lane, level, page, page_size):
    clause, args = where(start, end, lane, level)
    with db.connect() as conn:
        conn.execute('BEGIN')
        total = conn.execute(f'SELECT COUNT(*) FROM results WHERE {clause}', args).fetchone()[0]
        rows = conn.execute(f'SELECT * FROM results WHERE {clause} '
                            'ORDER BY captured_at DESC,frame_id DESC,lane LIMIT ? OFFSET ?',
                            [*args, page_size, (page-1)*page_size])
        return dict(total=total, page=page, page_size=page_size, items=[db.serialize(dict(r)) for r in rows])


def detail(frame_id):
    with db.connect() as conn:
        rows = [db.serialize(dict(r)) for r in conn.execute('SELECT * FROM results WHERE frame_id=? ORDER BY lane', (frame_id,))]
        if not rows:
            return None
        metadata = conn.execute('SELECT processing_config FROM frames WHERE id=?', (frame_id,)).fetchone()[0]
        return dict(frame_id=frame_id, lanes=rows, processing_config=json.loads(metadata) if metadata else None)
