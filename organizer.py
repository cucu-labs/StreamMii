from dataclasses import dataclass
from pathlib import Path
import os
import re

from config import ADULT_MODES, AUDIO_EXTS, CATEGORIES, SUB_EXTS, VIDEO_EXTS
from metadata_utils import MediaName, fetch_metadata, initial, parse_name, safe_name

SPORTS = {
    "Team Sports": ("Football", "Basketball", "Volleyball", "Rugby", "Baseball", "Softball", "Handball"),
    "Combat Sports": ("Boxing", "MMA", "Wrestling", "Karate"),
    "Winter Sports": ("Skiing", "Snowboarding", "Ice Skating", "Bobsledding"),
    "Water Sports": ("Surfing", "Rowing", "Kayaking", "Synchronized Swimming"),
    "Motor Sports": ("F1", "MotoGP"),
    "Individual Sports": ("Athletics", "Tennis", "Golf", "Pool", "Swimming", "Badminton", "Table Tennis", "Cycling", "Gymnastics"),
    "Equestrian": ("Show Jumping", "Dressage"), "Other": ("Cricket", "Hockey"),
}
SPORT_ALIASES = {"formula 1": "F1", "formula one": "F1", "mixed martial arts": "MMA", "track and field": "Athletics", "athletics (track and field)": "Athletics"}


@dataclass(frozen=True)
class OrganizeOptions:
    category: str = "auto"
    sport: str | None = None
    adult_mode: str | None = None
    creators: tuple[str, ...] = ()
    title: str | None = None
    year: int | None = None
    season: int | None = None

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError("Unknown category")
        if self.category == "sports" and not (self.sport or "").strip():
            raise ValueError("Sports requires --sport")
        if self.category == "adult" and self.adult_mode not in ADULT_MODES:
            raise ValueError("Adult requires --adult-mode")
        if self.category == "adult" and self.adult_mode in ("solo", "group", "lgbt-solo", "lgbt-group") and not any(n.strip() for n in self.creators):
            raise ValueError("This adult mode requires --creator (repeat for groups)")
        if self.year is not None and not 1900 <= self.year <= 2099:
            raise ValueError("Year must be from 1900 to 2099")
        if self.season is not None and not 0 <= self.season <= 99:
            raise ValueError("Season must be from 0 to 99")


def is_within(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def is_link(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def discover(inputs, library, state_dir=None, category="auto", recursive=True):
    """Snapshot inputs before writing. Never follow directory or file symlinks."""
    library = Path(library).resolve()
    roots = [Path(p).expanduser().resolve() for p in inputs]
    found = {}
    allowed = AUDIO_EXTS if category == "audio" else VIDEO_EXTS if category != "auto" else VIDEO_EXTS | AUDIO_EXTS
    excluded = [library] + ([Path(state_dir).resolve()] if state_dir else [])

    def accepted(path):
        return (not is_link(path) and path.is_file() and path.suffix.lower() in allowed
                and not any(is_within(path, e) for e in excluded)
                and ".streammii-" not in path.name.lower())

    for original, root in zip(inputs, roots):
        if is_link(Path(original).expanduser()):
            raise ValueError(f"Symlink inputs are not supported: {original}")
        if not root.exists():
            raise ValueError(f"Input does not exist: {root}")
        if is_within(root, library):
            raise ValueError("Input must be outside the library folder")
        if root.is_file():
            if accepted(root):
                found[os.path.normcase(str(root))] = root
            continue
        def walk_error(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if not is_link(Path(directory) / d)
                             and not any(is_within(Path(directory) / d, e) for e in excluded))
            for name in sorted(files):
                path = Path(directory) / name
                if accepted(path):
                    found[os.path.normcase(str(path))] = path
            if not recursive:
                dirs[:] = []
    return sorted(found.values(), key=lambda p: str(p).casefold())


def find_sidecars(source):
    source = Path(source)
    found = []
    for path in source.parent.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUB_EXTS:
            continue
        stem, base = path.stem.casefold(), source.stem.casefold()
        if stem == base or stem.startswith(base + "."):
            found.append(path)
    return sorted(found, key=lambda p: p.name.casefold())


def sidecar_suffix(source, subtitle):
    return safe_name(Path(subtitle).name[len(Path(source).stem):], limit=60)


def subtitle_target(source, subtitle, destination):
    tail = Path(subtitle).name[len(Path(source).stem):]
    return Path(destination).with_name(Path(destination).stem + "." + safe_name(tail.lstrip("."), limit=60))


def plan_destination(source, library, options=None, tags=None, convert=True, online_metadata=False, omdb_api_key=None):
    options = options or OrganizeOptions()
    source, library = Path(source), Path(library).resolve()
    tags = {str(k).lower(): str(v) for k, v in (tags or {}).items()}
    info = parse_name(source, options.category, options.title, options.year, options.season)
    category = options.category
    audio = category == "audio" or (category == "auto" and source.suffix.lower() in AUDIO_EXTS)
    if category == "auto":
        category = "audio" if audio else "tv" if info.episodes else "movies"
    ext = (".mp3" if audio else ".mkv") if convert else source.suffix.lower()
    title = info.title
    year = info.year
    if online_metadata and not options.title and category in ("movies", "tv", "anime", "k-drama"):
        metadata = fetch_metadata(title, year, omdb_api_key, "movie" if category == "movies" else "series")
        if metadata:
            title = metadata.title
            if year is None:
                year = metadata.year
    if category == "movies":
        label = safe_name(f"{title} ({year})" if year else title)
        parts, filename = ("Movies", initial(title), label), label
    elif category in ("tv", "anime", "k-drama"):
        folder = {"tv": "TV Shows", "anime": "Anime", "k-drama": "K-Drama"}[category]
        season = info.season if info.season is not None else 1
        parts = (folder, initial(title), title, f"Season {season:02d}")
        filename = f"{title} - {info.episode_code}" if info.episode_code else safe_name(source.stem)
    elif category == "audio":
        artist = safe_name(tags.get("album_artist") or tags.get("albumartist") or tags.get("artist") or "Unknown Artist")
        album = safe_name(tags.get("album") or source.parent.name or "Unknown Album")
        track = re.match(r"\d+", tags.get("track", ""))
        name = safe_name(options.title or tags.get("title") or source.stem)
        filename = f"{int(track[0]):02d} - {name}" if track else name
        parts = ("Audio", initial(artist), artist, album)
    elif category == "sports":
        sport = SPORT_ALIASES.get(options.sport.casefold(), options.sport)
        group = next((g for g, names in SPORTS.items() if sport.casefold() in [n.casefold() for n in names]), "Other")
        sport = next((n for names in SPORTS.values() for n in names if n.casefold() == sport.casefold()), sport)
        parts, filename = ("Sports", group, safe_name(sport)), safe_name(source.stem)
    elif category == "adult":
        mode = options.adult_mode
        creators = sorted({safe_name(c) for c in options.creators if c.strip()}, key=str.casefold)
        people = safe_name(" + ".join(creators), "Unknown")
        if mode in ("solo", "lgbt-solo"):
            parts = ("Adult",) + (("LGBT",) if mode.startswith("lgbt") else ()) + ("Solo", initial(people), people)
        elif mode in ("group", "lgbt-group"):
            parts = ("Adult",) + (("LGBT",) if mode.startswith("lgbt") else ()) + ("Group", people)
        elif mode == "jav":
            code = re.search(r"(?i)([a-z]{2,10})[-_ ]?(\d{2,7})", source.stem.rsplit("@", 1)[-1])
            label = f"{code[1].upper()}-{code[2]}" if code else safe_name(source.stem)
            parts = ("Adult", "JAV", label)
        else:
            parts = ("Adult", "Hentai", initial(title), title)
        filename = safe_name(source.stem)
    else:
        parts, filename = ("Documentaries", initial(title), title), safe_name(source.stem)
    result = library.joinpath(*(safe_name(p) for p in parts), safe_name(filename, limit=130) + ext)
    if not is_within(result, library):
        raise ValueError("Destination escapes the library")
    return result


def available_destination(destination, source, sidecars=()):
    destination = Path(destination)
    for index in range(1, 100000):
        candidate = destination if index == 1 else destination.with_name(f"{destination.stem} ({index}){destination.suffix}")
        paths = [candidate] + [subtitle_target(source, sub, candidate) for sub in sidecars]
        # refuse dangling symlinks as well as ordinary occupied names
        if len({str(p).casefold() for p in paths}) != len(paths):
            raise ValueError("Subtitle names collide after sanitizing")
        if all(not os.path.lexists(p) for p in paths):
            return candidate
    raise FileExistsError("Too many destination collisions")


def cleanup_empty_parents(directory, boundary):
    directory, boundary = Path(directory).resolve(), Path(boundary).resolve()
    while directory != boundary and directory.is_relative_to(boundary):
        try:
            directory.rmdir()
        except OSError:
            break
        directory = directory.parent
