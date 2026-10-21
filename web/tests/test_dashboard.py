import csv
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
from pipeline import classify
from result_schema import FIELDS
from web import db, queries
from web.app import app
from web.import_csv import import_csv


def sample_rows(timestamp='2026-10-21 12:00:00', name='120000', motion=None):
    return [dict(timestamp=timestamp,file=f'data/raw/{timestamp[:10]}/{name}.jpg',lane=lane,
        occupancy=occ,occupancy_raw=occ,occ_far=occ,occ_mid=occ,occ_near=occ,
        occupancy_kmeans=.1,edge_density=.15,motion=motion,brightness=120,contrast=59,
        is_night=0,threshold=42,cam_dx=0,cam_dy=0,cam_score=.75,level=level)
        for lane,occ,level in [('left',.169,'clear'),('right',.224,'moderate')]]


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(config,'DB_PATH',Path(self.temp.name)/'traffic.db')
        self.patch.start()
        db.init_db()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.range = {'from':'2026-10-21','to':'2026-10-22'}

    def tearDown(self):
        self.client_context.__exit__(None,None,None)
        self.patch.stop()
        self.temp.cleanup()

    def seed(self):
        return db.upsert_frame(sample_rows())

    def test_csv_round_trip_and_idempotent_import(self):
        path=Path(self.temp.name)/'old.csv'
        with path.open('w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(sample_rows())
        self.assertEqual(import_csv(path,False)['changed'],1)
        self.assertEqual(import_csv(path,False)['changed'],0)
        response=self.client.get('/api/export.csv',params=self.range)
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),2)
        self.assertEqual(list(rows[0]),FIELDS)
        self.assertEqual(rows[0]['motion'],'')
        for original, exported in zip(sample_rows(),rows):
            for key in FIELDS:
                if key in ('timestamp','file','lane','level'):
                    self.assertEqual(original[key],exported[key])
                elif original[key] is not None:
                    self.assertEqual(float(original[key]),float(exported[key]))

    def test_empty_database(self):
        data=self.client.get('/api/dashboard',params=self.range).json()
        self.assertEqual(data['latest'],[])
        self.assertEqual(data['summary'],[])
        self.assertEqual(data['series'],[])

    def test_complete_latest_and_atomic_lanes(self):
        self.seed()
        db.upsert_frame(sample_rows('2026-10-21 12:05:00','120500')[:1])
        data=self.client.get('/api/dashboard',params=self.range).json()
        self.assertEqual(len(data['latest']),2)
        self.assertEqual({row['timestamp'] for row in data['latest']},{'2026-10-21 12:00:00'})
        self.assertEqual(db.state()['latest_at'],db.parse_time('2026-10-21 12:00:00'))
        self.assertIsNone(data['latest'][0]['motion'])

    def test_date_boundaries_timezone_and_filters(self):
        db.upsert_frame(sample_rows('2026-10-20 23:59:59','235959'))
        self.seed()
        db.upsert_frame(sample_rows('2026-10-22 00:00:00','000000'))
        data=self.client.get('/api/history',params={**self.range,'lane':'right','level':'moderate'}).json()
        self.assertEqual(data['total'],1)
        self.assertEqual(data['items'][0]['captured_at'],'2026-10-21T12:00:00+07:00')

    def test_statistics_heatmap_and_full_detail(self):
        frame_id,_,_=self.seed()
        data=self.client.get('/api/dashboard',params=self.range).json()
        self.assertEqual(data['frame_count'],1)
        self.assertAlmostEqual(data['summary'][0]['mean'],.169)
        self.assertEqual(data['heatmap'][0]['hour'],12)
        self.assertEqual(data['heatmap'][0]['sample_count'],1)
        detail=self.client.get(f'/api/frames/{frame_id}').json()
        self.assertTrue(set(FIELDS).issubset(detail['lanes'][0]))

    def test_update_in_place_invalidates_revision_and_cache(self):
        frame_id,_,revision=self.seed()
        first=self.client.get('/api/dashboard',params=self.range).json()
        rows=sample_rows();rows[0]['occupancy']=.3
        updated_id,changed,new_revision=db.upsert_frame(rows)
        self.assertEqual(frame_id,updated_id)
        self.assertTrue(changed)
        self.assertGreater(new_revision,revision)
        second=self.client.get('/api/dashboard',params=self.range).json()
        self.assertNotEqual(first['revision'],second['revision'])
        self.assertEqual(second['latest'][0]['occupancy'],.3)
        self.assertEqual(self.client.get('/api/history',params=self.range).json()['total'],2)

    def test_heatmap_compares_both_lanes_when_other_panels_filter_one(self):
        self.seed()
        data=self.client.get('/api/dashboard',params={**self.range,'lane':'left'}).json()
        self.assertEqual({item['lane'] for item in data['series']},{'left'})
        self.assertEqual({item['lane'] for item in data['heatmap']},{'left','right'})
        comparison=data['heatmap_comparison'][0]
        self.assertEqual(comparison['sample_count'],1)
        self.assertAlmostEqual(comparison['left'],.169)
        self.assertAlmostEqual(comparison['right'],.224)
        self.assertAlmostEqual(comparison['difference'],-.055)

    def test_heatmap_difference_uses_matching_frames_and_keeps_unpaired_data(self):
        self.seed()
        extra=sample_rows('2026-10-21 12:05:00','120500')[:1]
        extra[0]['occupancy']=.9
        db.upsert_frame(extra)
        data=self.client.get('/api/dashboard',params=self.range).json()
        left=next(item for item in data['heatmap'] if item['lane']=='left')
        self.assertEqual(left['sample_count'],2)
        self.assertAlmostEqual(left['occupancy'],(.169+.9)/2)
        comparison=data['heatmap_comparison'][0]
        self.assertEqual(comparison['sample_count'],1)
        self.assertAlmostEqual(comparison['difference'],-.055)

    def test_pagination_and_missing_images(self):
        self.seed()
        first=self.client.get('/api/history',params={**self.range,'page_size':1}).json()
        second=self.client.get('/api/history',params={**self.range,'page_size':1,'page':2}).json()
        self.assertNotEqual(first['items'][0]['lane'],second['items'][0]['lane'])
        self.assertIsNone(first['items'][0]['images']['raw'])
        self.assertEqual(self.client.get('/api/frames/1/image').status_code,404)

    def test_input_validation(self):
        for params in [{'from':'oops'},{**self.range,'lane':'hack'},{**self.range,'page_size':999},
                       {'from':'2026-10-22','to':'2026-10-21'}]:
            self.assertEqual(self.client.get('/api/history',params=params).status_code,422)
        rows=sample_rows();rows[0]['occupancy']=float('nan')
        with self.assertRaises(ValueError):db.upsert_frame(rows)
        rows=sample_rows();rows[1]['threshold']=99
        with self.assertRaises(ValueError):db.upsert_frame(rows)
        self.assertIsNone(db.safe_media_path('../../config.py'))

    def test_classification_keeps_motion_rules_and_boundaries(self):
        self.assertEqual(classify(.1999,.0),'clear')
        self.assertEqual(classify(.2,None),'moderate')
        self.assertEqual(classify(.45,None),'moderate')
        self.assertEqual(classify(.4501,None),'congested')
        self.assertEqual(classify(.5,.8),'moderate')
        self.assertEqual(classify(.3,.2),'congested')

    def test_concurrent_readers_and_writer(self):
        self.seed();failures=[]
        def writer():
            try:
                for n in range(8):
                    db.upsert_frame(sample_rows(f'2026-10-21 12:{n+1:02}:00',f'12{n+1:02}00'))
            except Exception as exc:failures.append(exc)
        thread=threading.Thread(target=writer);thread.start()
        for _ in range(8):
            data=queries.dashboard(db.parse_time('2026-10-21'),db.parse_time('2026-10-22'),'all')
            self.assertEqual(len(data['latest']),2)
            self.assertEqual(data['latest'][0]['frame_id'],data['latest'][1]['frame_id'])
        thread.join();self.assertEqual(failures,[])


if __name__=='__main__':
    unittest.main()
