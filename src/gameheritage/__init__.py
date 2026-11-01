"""gameheritage: historical structures in video games, side by side with the real places.

Quick start::

    import gameheritage as gh

    cat = gh.load()                                  # the catalogue (installed with the package)
    cat.search("Notre-Dame")                         # places and games by name
    unity = cat.matches(game="Assassin's Creed Unity")
    medieval = cat.matches(era="Middle Ages")
    gh.show(unity.iloc[0])                           # game and real image, side by side
    gh.download(unity, "images/")                    # web copies + ATTRIBUTION.csv
    gh.describe("sites")                             # what every column means
    print(gh.cite())

The website with the same data: https://cantayus.github.io/video-game-digital-heritage/
"""

__version__ = "0.1.0"

from .catalog import Catalog, fetch_release, load
from .dictionary import describe
from .download import download
from .show import show

WEBSITE = "https://cantayus.github.io/video-game-digital-heritage/"
REPOSITORY = "https://github.com/cantayus/gameheritage"

CITATION_TEXT = (
    "Caliskan, C., & Aleksandraviciute, M. (2026). Digital heritage in video games: "
    "Historical structures in video games and their real-world counterparts "
    "(Version {version}) [Data set and Python package]. University of Rochester. {url}"
)

CITATION_BIBTEX = """@misc{{caliskan_aleksandraviciute_gameheritage_{year},
  author    = {{Caliskan, Cantay and Aleksandraviciute, Mija}},
  title     = {{Digital heritage in video games: Historical structures in video games
               and their real-world counterparts}},
  year      = {{{year}}},
  version   = {{{version}}},
  publisher = {{University of Rochester}},
  url       = {{{url}}},
  note      = {{Data set and Python package (gameheritage)}}
}}"""


def cite(style: str = "text") -> str:
    """Citation for the dataset and package. ``style``: ``"text"`` (APA) or ``"bibtex"``.

    Credit individual images with ``Catalog.credits()`` or the ``credit_line``
    column, which give each image's author, source, license and how it was obtained.
    """
    if style == "bibtex":
        return CITATION_BIBTEX.format(year=2026, version=__version__, url=REPOSITORY)
    if style != "text":
        raise ValueError("style must be 'text' or 'bibtex'")
    return CITATION_TEXT.format(version=__version__, url=REPOSITORY)


__all__ = [
    "REPOSITORY",
    "WEBSITE",
    "Catalog",
    "__version__",
    "cite",
    "describe",
    "download",
    "fetch_release",
    "load",
    "show",
]
