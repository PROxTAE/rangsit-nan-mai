"""Isolated 30-day/5-client read benchmark; never writes to the project DB."""
import concurrent.futures
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
from web import db
from web.app import app


def main():
    with tempfile.TemporaryDirectory() as temporary, patch.object(config,'DB_PATH',Path(temporary)/'bench.db'):
        db.init_db()
        start=db.parse_time('2026-09-22');count=30*288
        with db.connect() as conn,conn:
            conn.executemany('INSERT INTO frames(id,source_file,timestamp,captured_at) VALUES(?,?,?,?)',
                ((i+1,f'data/raw/bench/{i}.jpg',db.iso_time(start+i*300),start+i*300) for i in range(count)))
            conn.executemany('INSERT INTO lane_measurements(frame_id,lane,occupancy,level) VALUES(?,?,?,?)',
                ((i+1,lane,.1+(i%50)/100,'moderate') for i in range(count) for lane in ['left','right']))
        params={'from':'2026-09-22','to':'2026-10-22'}
        with TestClient(app) as client:
            def request(path):
                begun=time.perf_counter();response=client.get(path,params=params);response.raise_for_status()
                return (time.perf_counter()-begun)*1000
            cold=request('/api/dashboard')
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                timings=list(pool.map(lambda n:request('/api/dashboard' if n%2 else '/api/history'),range(40)))
            print(json.dumps(dict(frames=count,measurements=count*2,concurrent_clients=5,
                cold_dashboard_ms=round(cold,1),p95_ms=round(sorted(timings)[int(len(timings)*.95)-1],1),
                max_ms=round(max(timings),1)),indent=2))


if __name__=='__main__':main()
