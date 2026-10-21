"""CSV contract shared by the offline tools and dashboard (no plotting imports)."""

FIELDS = ["timestamp", "file", "lane", "occupancy", "occupancy_raw", "occ_far", "occ_mid", "occ_near",
          "occupancy_kmeans", "edge_density", "motion", "brightness", "contrast", "is_night",
          "threshold", "cam_dx", "cam_dy", "cam_score", "level"]
FRAME_FIELDS = ["brightness", "contrast", "is_night", "threshold", "cam_dx", "cam_dy", "cam_score"]
METRIC_FIELDS = ["occupancy", "occupancy_raw", "occ_far", "occ_mid", "occ_near",
                 "occupancy_kmeans", "edge_density", "motion", "level"]
