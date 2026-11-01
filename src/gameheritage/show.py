"""Side-by-side view of a pair, for notebooks."""

from __future__ import annotations

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .sources.http import HttpClient


def _open(paths: list[str], url: str, roots: list[Path], client: HttpClient) -> Image.Image:
    for root in roots:
        for rel in paths:
            if rel and (root / rel).exists():
                return Image.open(root / rel).convert("RGB")
    if not url:
        raise FileNotFoundError("image has neither a local file nor a URL")
    resp = client.request("GET", url)
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def side_by_side(
    pair,
    *,
    height: int = 420,
    local_roots: list[str | Path] | None = None,
    client: HttpClient | None = None,
) -> Image.Image:
    """Compose the game image (left) and real image (right) with credit captions.

    ``pair`` is one row of ``Catalog.matches()`` or ``Catalog.pairs()`` (a Series
    or dict). The project's web copies are used when the row has them, otherwise
    the original file; ``local_roots`` are folders searched first for the images'
    ``local_path`` / ``drive_path`` (curators' own copies). Returns a PIL image,
    which Jupyter displays inline; ``.save("pair.png")`` keeps it with its credits.
    """
    p = dict(pair)
    roots = [Path(r) for r in (local_roots or [])]
    if client is None:
        from .sources.http import public_client

        client = public_client()
    panels = []
    for side in ("game", "real"):
        paths = [p.get(f"{side}_local_path", ""), p.get(f"{side}_drive_path", "")]
        url = p.get(f"{side}_web_url", "") or p.get(f"{side}_file_url", "")
        im = _open(paths, url, roots, client)
        im = im.resize((max(1, int(im.width * height / im.height)), height))
        panels.append(im)
    gap, pad = 12, 8
    font = ImageFont.load_default()
    widths = [im.width for im in panels]
    caps = [
        f"{p.get('game_title', '')}: {p.get('in_game_name', '')}. {p.get('game_credit_line', '')}",
        f"{p.get('site_name', '')}. {p.get('real_credit_line', '')}",
    ]
    # Credits are printed in full: a saved image must carry its attribution.
    wrapped = [textwrap.wrap(c, max(20, w // 7)) for c, w in zip(caps, widths)]
    cap_h = 14 * max(len(w) for w in wrapped) + 2 * pad
    canvas = Image.new("RGB", (sum(widths) + gap, height + cap_h), "white")
    x = 0
    draw = ImageDraw.Draw(canvas)
    for im, lines in zip(panels, wrapped):
        canvas.paste(im, (x, 0))
        for j, line in enumerate(lines):
            draw.text((x + pad, height + pad + 14 * j), line, fill="black", font=font)
        x += im.width + gap
    return canvas


def show(pair, **kwargs) -> Image.Image:
    """Alias of :func:`side_by_side`."""
    return side_by_side(pair, **kwargs)


def save(pair, path: str | Path, **kwargs) -> Path:
    img = side_by_side(pair, **kwargs)
    img.save(path)
    return Path(path)
