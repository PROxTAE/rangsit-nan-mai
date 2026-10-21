"""Prepare files before publishing their paths in a database transaction."""
import os
import uuid

import config


def relative(path):
    return path.relative_to(config.ROOT).as_posix()


def atomic_image(path, image, quality=None):
    from preprocess import save_image
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.stem}-{uuid.uuid4().hex}.tmp{path.suffix}')
    try:
        save_image(temporary, image, quality)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def make_thumbnail(source, target):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(str(source), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot read image: {source}')
    height, width = image.shape[:2]
    scale = min(240 / width, 1)
    atomic_image(target, cv2.resize(image, (round(width*scale), round(height*scale))))


def existing_assets(source_file, thumbnails=True):
    source = (config.ROOT / source_file).resolve()
    if not source.is_relative_to(config.RAW_DIR.resolve()):
        raise ValueError(f'Image path outside data/raw: {source_file}')
    overlay = config.OVERLAY_DIR / source.parent.name / source.name
    thumb = config.THUMB_DIR / source.parent.name / source.name
    if thumbnails and source.is_file() and not thumb.is_file():
        make_thumbnail(source, thumb)
    pair = source.with_name(source.stem+'_b'+source.suffix)
    return {key: relative(path) if path.is_file() else None
            for key, path in [('raw_path', source), ('pair_path', pair),
                              ('overlay_path', overlay), ('thumbnail_path', thumb)]}
