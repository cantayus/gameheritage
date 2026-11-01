"""Facts about one image file: hash, true format, size, perceptual hash."""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

FORMAT_EXTS = {
    "JPEG": {".jpg", ".jpeg", ".jfif", ".jpe"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "GIF": {".gif"},
    "BMP": {".bmp"},
    "TIFF": {".tif", ".tiff"},
    "MPO": {".jpg", ".jpeg"},
}
MIME = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}
EXT_FOR_FORMAT = {
    "JPEG": ".jpg",
    "MPO": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tif",
}


@dataclass
class FileFacts:
    bytes: int
    sha256: str
    readable: bool
    true_format: str = ""
    width: int | None = None
    height: int | None = None
    n_frames: int = 1
    phash: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _phash(img: Image.Image) -> str:
    try:
        import imagehash
    except ImportError:  # optional dependency ([curate] extra)
        return ""
    return str(imagehash.phash(img.convert("RGB")))


def inspect_file(path: str | Path, *, compute_phash: bool = True) -> FileFacts:
    """Hash a file and, if it is a raster image, read its real format and size."""
    path = Path(path)
    size = path.stat().st_size
    digest = sha256_file(path)
    if size == 0:
        return FileFacts(0, digest, False, error="empty file")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as img:
                fmt = img.format or ""
                w, h = img.size
                frames = getattr(img, "n_frames", 1)
                img.load()
                ph = _phash(img) if compute_phash else ""
        return FileFacts(size, digest, True, fmt, w, h, frames, ph)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        return FileFacts(size, digest, False, error=f"{type(exc).__name__}: {exc}"[:200])


def extension_mismatch(filename: str, true_format: str) -> bool:
    """True when the file extension does not match the detected image format."""
    if not true_format:
        return False
    ext = Path(filename).suffix.lower()
    return ext not in FORMAT_EXTS.get(true_format, {f".{true_format.lower()}"})


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two hex perceptual hashes."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")
