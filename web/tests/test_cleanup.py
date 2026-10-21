import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import cleanup
from web import db
from web.tests.test_dashboard import sample_rows

TODAY = dt.date(2026, 12, 31)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(config, key, value) for key, value in dict(
            ROOT=root, DB_PATH=root/'traffic.db', RAW_DIR=root/'data'/'raw',
            OVERLAY_DIR=root/'results'/'overlays', THUMB_DIR=root/'results'/'thumbnails').items()]
        for p in self.patches:
            p.start()
        db.init_db()
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def touch(self, rel, age_sec=0):
        path = self.root/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x'*100)
        if age_sec:
            t = time.time()-age_sec
            os.utime(path, (t, t))
        return path

    def frame(self, day, name='120000'):
        """เฟรมหนึ่งพร้อมไฟล์ครบทุกชนิด คืน path แบบ relative"""
        rel = dict(raw_path=f'data/raw/{day}/{name}.jpg', pair_path=f'data/raw/{day}/{name}_b.jpg',
                   overlay_path=f'results/overlays/{day}/{name}-web-abc.jpg',
                   thumbnail_path=f'results/thumbnails/{day}/{name}-web-abc.jpg')
        for value in rel.values():
            self.touch(value, age_sec=7200)
        db.upsert_frame(sample_rows(f'{day} 12:00:00', name), rel)
        return rel

    def paths(self, day):
        with db.connect() as conn:
            row = conn.execute('SELECT raw_path, pair_path, overlay_path, thumbnail_path, version FROM frames '
                               'WHERE timestamp LIKE ?', (f'{day}%',)).fetchone()
        return dict(row)

    def test_retention_by_age(self):
        old = self.frame('2026-10-01')      # 91 วัน: ลบทุกอย่าง
        mid = self.frame('2026-11-15')      # 46 วัน: ลบแค่ภาพดิบ
        new = self.frame('2026-12-20')      # 11 วัน: เก็บทั้งหมด
        with db.connect() as conn:
            revision = conn.execute('SELECT data_revision FROM system_state').fetchone()[0]
        summary = cleanup.cleanup(today=TODAY)
        self.assertEqual(summary['raw_folders'], ['2026-10-01', '2026-11-15'])
        self.assertEqual(summary['media_folders'], ['2026-10-01'])
        self.assertEqual(summary['frames_updated'], 2)
        for rel in old.values():
            self.assertFalse((self.root/rel).exists())
        self.assertFalse((self.root/mid['raw_path']).exists())
        self.assertTrue((self.root/mid['overlay_path']).exists())
        for rel in new.values():
            self.assertTrue((self.root/rel).exists())
        self.assertEqual({k: v for k, v in self.paths('2026-10-01').items() if k != 'version'},
                         dict.fromkeys(['raw_path', 'pair_path', 'overlay_path', 'thumbnail_path']))
        self.assertIsNone(self.paths('2026-11-15')['raw_path'])
        self.assertEqual(self.paths('2026-11-15')['overlay_path'], mid['overlay_path'])
        self.assertEqual(self.paths('2026-12-20')['raw_path'], new['raw_path'])
        self.assertEqual(self.paths('2026-12-20')['version'], 1)
        self.assertEqual(self.paths('2026-10-01')['version'], 2)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT data_revision FROM system_state').fetchone()[0], revision+1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM lane_measurements').fetchone()[0], 6)

    def test_orphans(self):
        kept = self.frame('2026-12-20')
        day = 'results/overlays/2026-12-20'
        stale = self.touch(f'{day}/120000-web-old.jpg', age_sec=7200)
        fresh = self.touch(f'{day}/120500-web-new.jpg')                     # worker ยังไม่บันทึก DB
        tmp = self.touch(f'{day}/.120000-dead.tmp.jpg', age_sec=7200)
        analyze_out = self.touch(f'{day}/120000.jpg', age_sec=7200)          # จาก analyze.py
        summary = cleanup.cleanup(today=TODAY)
        self.assertEqual(summary['orphans'], 2)
        self.assertFalse(stale.exists())
        self.assertFalse(tmp.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(analyze_out.exists())
        self.assertTrue((self.root/kept['overlay_path']).exists())

    def test_dry_run_changes_nothing(self):
        old = self.frame('2026-10-01')
        orphan = self.touch('results/overlays/2026-12-20/120000-web-old.jpg', age_sec=7200)
        summary = cleanup.cleanup(today=TODAY, dry_run=True)
        self.assertEqual(summary['frames_updated'], 1)
        self.assertEqual(summary['orphans'], 1)
        self.assertGreater(summary['bytes'], 0)
        for rel in old.values():
            self.assertTrue((self.root/rel).exists())
        self.assertTrue(orphan.exists())
        self.assertEqual(self.paths('2026-10-01')['raw_path'], old['raw_path'])
        self.assertEqual(self.paths('2026-10-01')['version'], 1)


if __name__ == '__main__':
    unittest.main()
