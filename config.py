from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path

VERSION = "0.0.5"
VIDEO_EXTS = frozenset(".mkv .mp4 .avi .mov .wmv .flv .webm .m4v .mpg .mpeg .ts .m2ts .vob .ogv".split())
AUDIO_EXTS = frozenset(".mp3 .flac .wav .aac .m4a .ogg .opus .wma .aiff .alac".split())
SUB_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt"})
CATEGORIES = ("auto", "movies", "tv", "audio", "adult", "documentaries", "k-drama", "anime", "sports")
ADULT_MODES = ("solo", "group", "hentai", "jav", "lgbt-solo", "lgbt-group")


@dataclass(frozen=True)
class Settings:
    encoder: str = "cpu"
    profile: str = "h264"
    video_bitrate: int = 1000
    audio_bitrate: int = 128
    mp3_bitrate: int = 192
    max_fps: float = 30.0
    subtitles: str = "keep"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    timeout: float = 0.0
    online_metadata: bool = False
    omdb_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        for name, allowed in (("encoder", ("cpu", "amd", "nvidia", "intel")),
                              ("profile", ("h264", "mpeg4")), ("subtitles", ("keep", "none"))):
            if getattr(self, name) not in allowed:
                raise ValueError(f"Invalid {name}: choose {', '.join(allowed)}")
        for name, low, high in (("video_bitrate", 100, 5000), ("audio_bitrate", 32, 320),
                                ("mp3_bitrate", 32, 320), ("max_fps", 1, 30), ("timeout", 0, 604800)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{name} must be a number from {low} to {high}")
        for name in ("video_bitrate", "audio_bitrate", "mp3_bitrate"):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} must be an integer")
        if not isinstance(self.online_metadata, bool):
            raise ValueError("online_metadata must be true or false")
        if not isinstance(self.omdb_api_key, str):
            raise ValueError("OMDb API key must be text")
        for name in ("ffmpeg", "ffprobe"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be an executable name or path")

    def to_dict(self, include_secrets=False):
        data = asdict(self)
        if not include_secrets:
            data.pop("omdb_api_key", None)
        return data


def load_config(path=None):
    if path is None:
        return Settings()
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    unknown = set(data) - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}.")
    return Settings(**data)
