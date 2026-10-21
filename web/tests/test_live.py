"""Real HTTP SSE and collector integration; all measurements use temporary DBs."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import config
from collector import collect_once, next_wait
from pipeline import motion_reference, previous_frame
from run_monitor import worker_lock, main as monitor_main, pending_frames
from web import db
from web.tests.test_dashboard import sample_rows


def next_event(lines, wanted):
    event='';data=''
    for line in lines:
        if line.startswith('event: '):event=line[7:]
        if line.startswith('data: '):data=line[6:]
        if not line and event:
            if event==wanted:return json.loads(data)
            event='';data=''
    raise AssertionError('SSE connection ended before '+wanted)


class CollectorTests(unittest.TestCase):
    def test_no_pair_and_duplicate_do_not_publish_partial_files(self):
        phases=[]
        with patch('collector.fetch_snapshot',return_value=b'frame'),patch('collector.save',return_value=Path('frame.jpg')) as save:
            first=collect_once(object(),no_pair=True,on_phase=phases.append)
            self.assertEqual(first['status'],'ready')
            second=collect_once(object(),last_hash=first['last_hash'],no_pair=True)
            self.assertEqual(second['status'],'duplicate')
            self.assertEqual(save.call_count,1)

    def test_pair_timeout_still_returns_primary_frame(self):
        with patch('collector.fetch_snapshot',return_value=b'frame'),patch('collector.wait_next_snapshot',return_value=None) as wait,patch('collector.save',return_value=Path('frame.jpg')):
            self.assertEqual(collect_once(object(),no_pair=False)['status'],'ready')
            wait.assert_called_once()

    def test_default_follows_capture_pair_setting(self):
        with patch('collector.fetch_snapshot',return_value=b'frame'),patch('collector.wait_next_snapshot') as wait,\
                patch('collector.save',return_value=Path('frame.jpg')),patch.object(config,'CAPTURE_PAIR',False):
            collect_once(object())
            wait.assert_not_called()

    def test_duplicate_retries_soon_instead_of_full_interval(self):
        self.assertEqual(next_wait('duplicate',60,1),config.PAIR_POLL_SEC)
        self.assertEqual(next_wait('ready',60,2.5),57.5)
        self.assertEqual(next_wait('ready',60,75),0)

    def test_worker_loop_polls_again_after_duplicate(self):
        results=iter([{'status':'duplicate','path':None,'last_hash':'a'},{'status':'duplicate','path':None,'last_hash':'a'}])
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'DB_PATH',Path(folder)/'dup.db'),\
                patch.object(config,'RAW_DIR',Path(folder)/'raw'),\
                patch.object(sys,'argv',['run_monitor.py','--count','2']),\
                patch('run_monitor.Pipeline'),patch('run_monitor.recover_pending',return_value=True),\
                patch('run_monitor.collect_once',side_effect=lambda *a,**k:next(results)) as collect,\
                patch('run_monitor.time.sleep') as sleep:
            monitor_main()
            self.assertEqual(collect.call_count,2)
            self.assertIs(collect.call_args.args[2],True)                    # no_pair ตามค่าเริ่มต้น CAPTURE_PAIR=False
            sleep.assert_called_once_with(config.PAIR_POLL_SEC)

    def test_worker_lock_rejects_second_owner(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'DB_PATH',Path(folder)/'lock.db'):
            with worker_lock():
                with self.assertRaises(RuntimeError):
                    with worker_lock():pass

    def test_watch_only_never_fetches_or_stops_the_existing_collector(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'DB_PATH',Path(folder)/'watch.db'),\
                patch.object(sys,'argv',['run_monitor.py','--watch-only']),\
                patch('run_monitor.Pipeline'),patch('run_monitor.recover_pending',return_value=True),\
                patch('run_monitor.collect_once') as collect,\
                patch('run_monitor.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):monitor_main()
            collect.assert_not_called()
            self.assertEqual(db.state()['phase'],'stopped')

    def test_pending_without_pair_is_ready_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=root/'data'/'raw';day=raw/'2026-10-21';day.mkdir(parents=True)
            with patch.object(config,'DB_PATH',root/'pending.db'),patch.object(config,'ROOT',root),\
                    patch.object(config,'RAW_DIR',raw),patch.object(config,'CAPTURE_PAIR',False):
                db.init_db();primary=day/'120000.jpg';primary.write_bytes(b'frame')
                self.assertEqual(pending_frames(),[primary])

    def test_pending_waits_for_pair_and_skips_complete_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=root/'data'/'raw';day=raw/'2026-10-21';day.mkdir(parents=True)
            with patch.object(config,'DB_PATH',root/'pending.db'),patch.object(config,'ROOT',root),\
                    patch.object(config,'RAW_DIR',raw),patch.object(config,'CAPTURE_PAIR',True):
                db.init_db();primary=day/'120000.jpg';primary.write_bytes(b'frame')
                self.assertEqual(pending_frames(),[])
                (day/'120000_b.jpg').write_bytes(b'pair')
                self.assertEqual(pending_frames(),[primary])
                db.upsert_frame(sample_rows())
                self.assertEqual(pending_frames(),[])


class MotionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.raw=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def frame(self,day,name):
        path=self.raw/day/f'{name}.jpg';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x')
        return path

    def test_previous_frame_within_gap(self):
        self.frame('2026-10-21','120000');prev=self.frame('2026-10-21','120100')
        self.frame('2026-10-21','120100_b');current=self.frame('2026-10-21','120203')
        self.frame('2026-10-21','120300')                                       # ภาพหลังจากนี้ ไม่ใช้
        self.assertEqual(motion_reference(current),prev)

    def test_pair_preferred_for_old_five_minute_data(self):
        self.frame('2026-10-21','115500');current=self.frame('2026-10-21','120000')
        pair=self.frame('2026-10-21','120000_b')
        self.assertEqual(motion_reference(current),pair)

    def test_gap_too_large_or_first_frame_gives_none(self):
        self.assertIsNone(motion_reference(self.frame('2026-10-21','120000')))
        self.assertIsNone(motion_reference(self.frame('2026-10-21','120200')))   # ห่าง 120 วินาที

    def test_first_frame_of_day_uses_yesterday(self):
        prev=self.frame('2026-10-20','235930');current=self.frame('2026-10-21','000020')
        self.assertEqual(previous_frame(current),prev)
        self.assertEqual(motion_reference(current),prev)


class LiveHTTPTests(unittest.TestCase):
    def test_two_clients_receive_commit_and_reconnect_sync(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'DB_PATH',Path(folder)/'live.db'):
            db.init_db()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            environment={**os.environ,'TRAFFIC_DB_PATH':str(config.DB_PATH)}
            with open(Path(folder)/'server.log','w') as log:
                process=subprocess.Popen([sys.executable,'-m','uvicorn','web.app:app','--host','127.0.0.1',
                    '--port',str(port),'--workers','1'],cwd=config.ROOT,env=environment,stdout=log,stderr=log)
                url=f'http://127.0.0.1:{port}'
                try:
                    for _ in range(60):
                        try:
                            requests.get(url+'/api/health',timeout=.5).raise_for_status();break
                        except requests.RequestException:time.sleep(.1)
                    else:self.fail('Live test server did not start')
                    with requests.get(url+'/api/events',stream=True,timeout=(2,5)) as first,requests.get(url+'/api/events',stream=True,timeout=(2,5)) as second:
                        first_lines=first.iter_lines(chunk_size=1,decode_unicode=True)
                        second_lines=second.iter_lines(chunk_size=1,decode_unicode=True)
                        self.assertEqual(next_event(first_lines,'sync')['revision'],0)
                        self.assertEqual(next_event(second_lines,'sync')['revision'],0)
                        _,_,revision=db.upsert_frame(sample_rows())
                        committed=time.perf_counter()
                        for lines in (first_lines,second_lines):
                            while next_event(lines,'measurements_updated')['revision']!=revision:pass
                        elapsed=time.perf_counter()-committed
                        self.assertLess(elapsed,2)
                        print(f'\nSSE: commit-to-two-clients {elapsed*1000:.1f} ms',flush=True)
                    _,_,new_revision=db.upsert_frame(sample_rows('2026-10-21 12:05:00','120500'))
                    with requests.get(url+'/api/events',stream=True,timeout=(2,5),headers={'Last-Event-ID':str(revision)}) as reconnect:
                        self.assertEqual(next_event(reconnect.iter_lines(chunk_size=1,decode_unicode=True),'sync')['revision'],new_revision)
                    response=requests.get(url+'/api/dashboard',params={'from':'2026-10-21','to':'2026-10-22'},timeout=2)
                    self.assertEqual(response.json()['latest'][0]['timestamp'],'2026-10-21 12:05:00')
                finally:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':unittest.main()
