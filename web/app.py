"""Serve the local UI, read APIs, images, exports, and live notifications."""
import asyncio
import csv
import datetime as dt
import io
import json
import time
import threading
import logging
import sqlite3
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from result_schema import FIELDS
from web import db, queries

STATIC = Path(__file__).parent / 'static'
cache = OrderedDict()
cache_lock = threading.RLock()


async def watch_database(app):
    previous_revision, last_status = None, 0
    while True:
        try:
            status = await asyncio.to_thread(db.state)
        except (sqlite3.Error, OSError):
            logging.exception('Database monitor temporarily unavailable; retrying')
            await asyncio.sleep(1)
            continue
        events = []
        if status['data_revision'] != previous_revision:
            previous_revision = status['data_revision']
            with cache_lock:
                cache.clear()
            events.append(('measurements_updated', {'revision': previous_revision}))
        if time.monotonic()-last_status >= 10:
            last_status = time.monotonic()
            events.append(('system_status', status))
        for event in events:
            for queue in list(app.state.clients):
                if queue.full():
                    # A missed signal is safe: the next sync/refetch reads current DB state.
                    queue.get_nowait()
                queue.put_nowait(event)
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    app.state.clients = set()
    task = asyncio.create_task(watch_database(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Rangsit Traffic Observatory', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


def selected_range(from_value, to_value):
    now = dt.datetime.now(db.BANGKOK)
    start = db.parse_time(from_value) if from_value else int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end = db.parse_time(to_value) if to_value else int((now.replace(hour=0, minute=0, second=0, microsecond=0)+dt.timedelta(days=1)).timestamp())
    if end <= start or end-start > 366*86400:
        raise ValueError('Choose a positive date range of at most 366 days')
    return start, end


def range_or_422(from_value, to_value):
    try:
        return selected_range(from_value, to_value)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/api/config')
def configuration():
    return dict(lanes=config.LANE_NAMES_TH, levels=config.LEVEL_TH, interval_sec=config.INTERVAL_SEC,
                timezone='Asia/Bangkok', thresholds=dict(moderate=config.OCC_MODERATE, congested=config.OCC_CONGESTED),
                stale_sec=config.WEB_STALE_SEC,
                quality=dict(min_score=config.LANDMARK_MIN_SCORE, shift_tolerance=config.CAMERA_SHIFT_TOL))


@app.get('/api/dashboard')
def dashboard(from_value: str | None = Query(None, alias='from'), to: str | None = None,
              lane: Literal['all', 'left', 'right'] = 'all'):
    start, end = range_or_422(from_value, to)
    with db.connect() as conn:
        revision = conn.execute('SELECT data_revision FROM system_state WHERE id=1').fetchone()[0]
    key = (str(config.DB_PATH), revision, start, end, lane)
    with cache_lock:
        cached = cache.get(key)
    if cached is not None:
        return cached
    result = queries.dashboard(start, end, lane)
    # Key by the revision read in the same transaction as the data.
    key = (str(config.DB_PATH), result['revision'], start, end, lane)
    with cache_lock:
        cache[key] = result
        while len(cache) > 24:
            cache.popitem(last=False)
    return result


@app.get('/api/trend')
def trend(from_value: str | None = Query(None, alias='from'), to: str | None = None,
          lane: Literal['all', 'left', 'right'] = 'all'):
    return queries.trend(*range_or_422(from_value, to), lane)


@app.get('/api/history')
def history(from_value: str | None = Query(None, alias='from'), to: str | None = None,
            lane: Literal['all', 'left', 'right'] = 'all',
            level: Literal['clear', 'moderate', 'congested'] | None = None,
            page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    return queries.history(*range_or_422(from_value, to), lane, level, page, page_size)


@app.get('/api/frames/{frame_id}')
def detail(frame_id: int):
    result = queries.detail(frame_id)
    if result is None:
        raise HTTPException(404, 'Frame not found')
    return result


@app.get('/api/frames/{frame_id}/image')
def image(frame_id: int, variant: Literal['raw', 'overlay', 'thumbnail'] = 'raw', v: int | None = None):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM frames WHERE id=?', (frame_id,)).fetchone()
    path = db.safe_media_path(row[{'raw': 'raw_path', 'overlay': 'overlay_path', 'thumbnail': 'thumbnail_path'}[variant]]) if row else None
    if path is None:
        raise HTTPException(404, 'Image unavailable')
    return FileResponse(path, headers={'Cache-Control': 'private, max-age=0, must-revalidate'})


@app.get('/api/export.csv')
def export(from_value: str | None = Query(None, alias='from'), to: str | None = None,
           lane: Literal['all', 'left', 'right'] = 'all',
           level: Literal['clear', 'moderate', 'congested'] | None = None):
    start, end = range_or_422(from_value, to)
    clause, args = queries.where(start, end, lane, level)

    def stream():
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        yield '\ufeff'
        writer.writerow(FIELDS)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        with db.connect() as conn:
            cursor = conn.execute(f"SELECT {','.join(FIELDS)} FROM results WHERE {clause} "
                                  'ORDER BY captured_at,frame_id,lane', args)
            while rows := cursor.fetchmany(250):
                writer.writerows(rows)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
    return StreamingResponse(stream(), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename="rangsit-traffic.csv"'})


@app.get('/api/health')
def health():
    return dict(web='online', database='online', **db.state())


def event_text(name, data):
    identifier = f"id: {data['revision']}\n" if name == 'measurements_updated' else ''
    return f'{identifier}event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


@app.get('/api/events')
async def events(request: Request):
    queue = asyncio.Queue(maxsize=8)
    request.app.state.clients.add(queue)

    async def stream():
        try:
            status = await asyncio.to_thread(db.state)
            yield 'retry: 2000\n' + event_text('sync', {'revision': status['data_revision'], 'status': status})
            while not await request.is_disconnected():
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield event_text(name, data)
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            request.app.state.clients.discard(queue)
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
