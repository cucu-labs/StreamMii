from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from guessit import guessit as guessit_parse
except ImportError:
    guessit_parse = None

RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)
EPISODE = re.compile(r"(?i)(?<![a-z0-9])(?:s(?P<s>\d{1,2})[ ._-]*e(?P<e>\d{1,3})(?P<more>(?:[ ._-]*e\d{1,3})*)|(?P<xs>\d{1,2})x(?P<xe>\d{1,3})|(?:episode|ep)[ ._-]*(?P<ep>\d{1,3}))(?!\d)")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
RELEASE = re.compile(r"(?i)\b(?:2160p|1080[pi]|720p|480p|360p|bluray|blu.ray|brrip|bdrip|webrip|web[ .-]?dl|hdtv|dvdrip|x26[45]|h[ .]?26[45]|hevc|av1|aac|dts|proper|repack)\b")


def safe_name(name, fallback="Unknown", limit=90):
    text = unicodedata.normalize("NFC", str(name)) if name is not None else ""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if text in ("", ".", ".."):
        text = fallback
    if RESERVED.match(text):
        text = "_" + text
    # Limit UTF-8 bytes as well as characters for removable drives.
    text = text[:limit]
    while len(text.encode("utf-8")) > 180:
        text = text[:-1]
    return text.rstrip(" .") or "Unknown"


def initial(name):
    first = name[0].upper()
    return first if first.isalpha() and len(first) == 1 else "#"


def clean_title(text):
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[._]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -()") or "Unknown"


@dataclass(frozen=True)
class MediaName:
    title: str
    year: int | None = None
    season: int | None = None
    episodes: tuple[int, ...] = ()

    @property
    def episode_code(self):
        if not self.episodes:
            return ""
        return f"S{self.season if self.season is not None else 1:02d}" + "".join(f"E{e:02d}" for e in self.episodes)


def guessit_info(filepath):
    if guessit_parse is None:
        return {}
    try:
        return dict(guessit_parse(Path(filepath).name))
    except (ValueError, TypeError, KeyError):
        return {}


def parse_name(path, category="auto", title=None, year=None, season=None):
    path = Path(path)
    stem = path.stem
    match = EPISODE.search(stem)
    guessed = guessit_info(path)
    episodes = ()
    parsed_season = None
    if match:
        parsed_season = int(match["s"] or match["xs"] or 1)
        episodes = (int(match["e"] or match["xe"] or match["ep"]),)
        episodes += tuple(int(e) for e in re.findall(r"(?i)e(\d+)", match["more"] or ""))
        raw_title = stem[:match.start()]
    else:
        # Common fansub naming: [Group] Series - 012 [quality].
        anime = re.search(r"\s-\s(\d{1,3})(?:\s|\[|$)", stem) if category in ("anime", "k-drama") else None
        if anime:
            episodes, parsed_season = (int(anime[1]),), 1
            raw_title = stem[:anime.start()]
        else:
            raw_title = stem
    y = YEAR.search(raw_title)
    parsed_year = int(y[1]) if y else None
    if y:
        raw_title = raw_title[:y.start()]
    release = RELEASE.search(raw_title)
    if release:
        raw_title = raw_title[:release.start()]
    fallback_title = clean_title(raw_title)
    if not match and not episodes and guessed.get("type") == "episode":
        ep = guessed.get("episode")
        episodes = tuple(ep) if isinstance(ep, list) and all(isinstance(e, int) for e in ep) else ((ep,) if isinstance(ep, int) else ())
        gs = guessed.get("season")
        parsed_season = gs if isinstance(gs, int) else 1
    if not match and guessed.get("title") and not episodes:
        fallback_title = str(guessed["title"])
    if fallback_title == "Unknown" and episodes:
        fallback_title = clean_title(path.parent.name)
    return MediaName(safe_name(title or fallback_title), year if year is not None else parsed_year,
                     season if season is not None else parsed_season, episodes)


@dataclass(frozen=True)
class OnlineMetadata:
    title: str
    year: int | None = None


def fetch_metadata(title, year=None, api_key=None, kind="movie"):
    key = api_key or os.environ.get("STREAMMII_OMDB_API_KEY")
    if not key or not title or kind not in ("movie", "series"):
        return None
    return _fetch_metadata(str(title), year, key, kind)


@lru_cache(maxsize=256)
def _fetch_metadata(title, year, key, kind):
    params = {"t": title, "apikey": key, "type": kind}
    if year:
        params["y"] = str(year)
    try:
        with urlopen("https://www.omdbapi.com/?" + urlencode(params), timeout=6) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8-sig"))
        if isinstance(data, dict) and data.get("Response") == "True" and isinstance(data.get("Title"), str):
            parsed_year = YEAR.search(str(data.get("Year", "")))
            return OnlineMetadata(safe_name(data["Title"]), int(parsed_year[1]) if parsed_year else None)
    except (OSError, ValueError, TypeError):
        pass
    return None


def fetch_movie_metadata(title, year=None, api_key=None):
    result = fetch_metadata(title, year, api_key, "movie")
    return result.title if result else None


def classify_file_by_name(filename):
    return "tv" if EPISODE.search(Path(filename).stem) else "movie"
