# Dashboard validation — 21 October 2026

## Automated checks

- `python -m unittest discover -s web/tests -v`: **16 tests passed**.
- `python -m compileall -q web run_monitor.py run_dashboard.py collector.py`: passed.
- `python -m pip check`: no broken requirements.
- Real HTTP SSE: two subscribers received a committed revision within 1 second in the recorded runs; reconnect returned the current revision. The final full-suite run measured **430.9 ms** for notification delivery to both clients.

Tests use temporary databases and do not populate the project database with synthetic measurements.

## Read performance

`python -m web.tests.benchmark` used 8,640 frames / 17,280 lane measurements (30 days at a 5-minute interval), with 5 concurrent clients and 40 dashboard/history requests:

| Metric | Measured |
|---|---|
| Cold dashboard API | 119.8 ms |
| Mixed API p95 | 183.0 ms |
| Maximum request | 199.7 ms |

These are local API timings, not a measurement of image loading, browser rendering, or internet hosting performance.

## Actual pipeline and browser checks

- Imported the existing CSV twice: 14 frames / 28 lane rows on both imports, with zero changes on the second import.
- Used the existing Pipeline to recover seven previously unanalyzed images and prepare real overlays/thumbnails.
- Observed an actual camera round at 12:59 update both Edge and the in-app browser without reloading either page: left 16.4%, right 16.0%, history grew from 42 to 44 rows.
- Verified raw/overlay switching, history pagination, lane/level filters, empty results, and a frame inspector containing all 19 original CSV fields.
- Checked desktop at 1440 px and mobile at 390 px: document width matched the viewport, with horizontal scrolling confined to tables/heatmap where needed.
- Checked browser developer logs: no application JavaScript errors during the UI test.
- Verified launcher startup and Ctrl+C shutdown, with the test listener on port 8010 removed afterward.
- Found an already-running legacy collector and added/tested `--watch-only` so existing collection can continue without starting a second camera collector. The original collector was preserved.

Screenshots: [full dashboard](dashboard-preview.png), [overview](dashboard-overview.png).

The collected images, timestamps, and traffic values remain project data. Website work did not change the original classification algorithm.
