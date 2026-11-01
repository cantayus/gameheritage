"""Spot images whose subject is a person rather than a place.

The project collects pictures of places (buildings, sculptures, parks, natural
formations), not character renders or portraits. ``person_score`` runs two of
OpenCV's frontal-face detectors and returns the area of the largest face both
agree on, as a share of the image. Character renders and close-ups score well
above ``PERSON_MIN_AREA``; crowds seen from a distance and buildings do not.

Faces are expected on statues, so callers skip the check for sculpture-like
places (see ``SCULPTURE``). OpenCV is optional (``pip install
opencv-python-headless``); without it every score is 0 and nothing is removed.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

PERSON_MIN_AREA = 0.005  # half a percent of the image
SCULPTURE = re.compile(
    r"statue|sculpture|colossus|relief|bust\b|monument|memorial|sphinx|figure|idol|moai|buddha",
    re.I,
)
_CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")


def _cascade_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("GAMEHERITAGE_MODELS"):
        dirs.append(Path(os.environ["GAMEHERITAGE_MODELS"]))
    try:
        import cv2

        dirs.append(Path(cv2.data.haarcascades))
    except Exception:  # cv2 missing or built without data files
        pass
    dirs.append(Path.cwd() / ".cache" / "models")
    return dirs


@lru_cache(maxsize=1)
def _detectors():
    try:
        import cv2
    except ImportError:
        return None
    found = []
    for name in _CASCADES:
        for d in _cascade_dirs():
            if (d / name).exists():
                c = cv2.CascadeClassifier(str(d / name))
                if not c.empty():
                    found.append(c)
                    break
    return found if len(found) == len(_CASCADES) else None


def person_score(path: str | Path) -> float:
    """Share of the image covered by the largest face both detectors find (0 to 1)."""
    det = _detectors()
    if det is None:
        return 0.0
    import cv2

    im = cv2.imread(str(path))
    if im is None:
        return 0.0
    h, w = im.shape[:2]
    scale = 800 / max(h, w)
    if scale < 1:
        im = cv2.resize(im, (int(w * scale), int(h * scale)))
    gray = cv2.equalizeHist(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
    gh, gw = gray.shape
    side = max(12, int(min(gh, gw) * 0.06))
    boxes = [c.detectMultiScale(gray, 1.1, n, minSize=(side, side)) for c, n in zip(det, (8, 6))]
    best = 0.0
    for x, y, bw, bh in boxes[0]:
        for x2, y2, bw2, bh2 in boxes[1]:
            ix = max(0, min(x + bw, x2 + bw2) - max(x, x2))
            iy = max(0, min(y + bh, y2 + bh2) - max(y, y2))
            if ix * iy >= 0.5 * min(bw * bh, bw2 * bh2):  # both detectors see this face
                best = max(best, bw * bh / (gh * gw))
    return best


def shows_person(path: str | Path, place_type: str = "") -> bool:
    """True when the picture is mostly a person, unless the place is a sculpture."""
    if place_type and SCULPTURE.search(place_type):
        return False
    return person_score(path) >= PERSON_MIN_AREA


def available() -> bool:
    return _detectors() is not None
