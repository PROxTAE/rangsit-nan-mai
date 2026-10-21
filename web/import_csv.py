"""Idempotent migration of the original 19-column results CSV."""
import argparse
import csv
from collections import defaultdict

import config
from result_schema import FIELDS
from web import db
from web.media import existing_assets


def import_csv(path, thumbnails=True):
    db.init_db()
    groups = defaultdict(list)
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'Missing CSV columns: {sorted(missing)}')
        for row in reader:
            normalized = db.normalize_row(row)
            groups[normalized['file']].append(normalized)
    changed = 0
    # Validate all groups before making changes to avoid silently importing bad rows.
    for filename, rows in groups.items():
        if len(rows) != len({r['lane'] for r in rows}):
            raise ValueError(f'Duplicate lane in CSV: {filename}')
        if any(any(r[k] != rows[0][k] for k in ['timestamp', *db.FRAME_FIELDS]) for r in rows):
            raise ValueError(f'Inconsistent shared values: {filename}')
    for filename, rows in groups.items():
        _, updated, _ = db.upsert_frame(rows, existing_assets(filename, thumbnails))
        changed += int(updated)
    return {'frames': len(groups), 'rows': sum(map(len, groups.values())), 'changed': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', default=str(config.OCCUPANCY_CSV))
    args = parser.parse_args()
    print(import_csv(args.path))


if __name__ == '__main__':
    main()
