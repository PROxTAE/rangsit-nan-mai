import unittest

import cv2
import numpy as np

import config
import segment
from pipeline import zone_occupancy


class GlareTests(unittest.TestCase):
    """ภาพสังเคราะห์: ถนนมืดเรียบ + ก้อนแสงไฟขาวเรียบ (มีขอบจางลง) + รถที่มีลวดลาย"""

    def setUp(self):
        self.bg = np.full((200, 200), 80, np.uint8)
        frame = self.bg.copy()
        cv2.circle(frame, (60, 100), 40, 150, -1)       # ขอบแสงที่จางลง
        cv2.circle(frame, (60, 100), 25, 250, -1)       # แกนแสงจ้า
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        car = np.indices((30, 20)).sum(axis=0) % 2 * 200 + 30   # ลายตารางหมากรุก = มีขอบ
        frame[90:120, 150:170] = car
        self.frame = frame
        self.roi = np.full_like(self.bg, 255)

    def test_mask_covers_smooth_glare_and_its_halo_but_not_textured_car(self):
        glare = segment.glare_mask(self.frame, self.bg, 40, self.roi)
        self.assertTrue(glare[100, 60])                  # แกนแสง
        self.assertTrue(glare[100, 60 - 35])             # ขอบแสงที่จางลง
        self.assertFalse(glare[105, 160])                # รถ
        self.assertFalse(glare[20, 20])                  # ถนนว่าง

    def test_occupancy_ignores_glare_unless_it_fills_the_zone(self):
        lane = np.full((100, 100), 255, np.uint8)
        zones = {'all': lane.copy()}
        binary = np.zeros_like(lane)
        binary[:, :30] = 255                             # รถ 30 จาก 50 คอลัมน์ที่มองเห็น + แสงอีกครึ่งเลน
        binary[:, 50:] = 255
        glare = np.zeros_like(lane)
        glare[:, 50:] = 255
        self.assertAlmostEqual(zone_occupancy(binary, lane, zones)[0], 0.8)
        self.assertAlmostEqual(zone_occupancy(binary, lane, zones, glare)[0], 0.6)
        glare[:, :] = 255                                # มองไม่เห็นเลย -> นับแบบเดิม
        glare[:, :int(100 * config.GLARE_MIN_VISIBLE) - 1] = 0
        self.assertAlmostEqual(zone_occupancy(binary, lane, zones, glare)[0], 0.8)
