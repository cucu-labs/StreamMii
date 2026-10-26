from collections import deque
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "text", "webvtt", "microdvd", "subviewer", "sami", "stl"}
BITMAP_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}


class ConversionError(RuntimeError):
    pass


def number(value, default=0.0):
    try:
        result = float(Fraction(str(value).replace(":", "/")))
        return result if math.isfinite(result) else default
    except (ValueError, ZeroDivisionError):
        return default


def timestamp(value):
    """Read FFmpeg's HH:MM:SS duration tags without treating them as ratios."""
    if isinstance(value, str) and re.fullmatch(r"\d+:\d{2}:\d{2}(?:\.\d+)?", value.strip()):
        hours, minutes, seconds = value.strip().split(":")
        if int(minutes) < 60 and float(seconds) < 60:
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return None
    return number(value, None)


def stream_interval(stream, format_info):
    """Return this stream's presentation start/end, never a subtitle's duration."""
    if "_packet_interval" in stream:
        return stream["_packet_interval"]
    start = number(stream.get("start_time"), None)
    if start is None:
        start_pts = number(stream.get("start_pts"), None)
        time_base = number(stream.get("time_base"))
        start = start_pts * time_base if start_pts is not None and time_base > 0 else number(format_info.get("start_time"))

    # Matroska writers disagree about DURATION: FFmpeg writes an end
    # timestamp, while other writers can store an elapsed length. With a
    # nonzero start, only trust an identifiable FFmpeg tag without foreign
    # track statistics. Ambiguous tags fall back to measuring actual packets.
    if set(format_info.get("format_name", "").split(",")) & {"matroska", "webm"}:
        tags = {key.upper(): value for key, value in stream.get("tags", {}).items()}
        format_tags = {key.upper(): value for key, value in format_info.get("tags", {}).items()}
        ffmpeg_writer = str(format_tags.get("ENCODER", "")).casefold().startswith("lavf")
        foreign_statistics = any(key.startswith("_STATISTICS_") for key in tags)
        end = timestamp(tags.get("DURATION"))
        if end is not None and end > start and (start == 0 or (ffmpeg_writer and not foreign_statistics)):
            return start, end
    duration = number(stream.get("duration"))
    if duration <= 0:
        duration = number(stream.get("duration_ts")) * number(stream.get("time_base"))
    if duration > 0:
        return start, start + duration
    return None


def media_streams(info, audio=False):
    """Streams selected by the conversion commands, in output order."""
    if audio:
        return info.audio[:1]
    return ([info.video] if info.video is not None else []) + info.audio


def read_packet_intervals(path, info, ffprobe):
    """Fill missing A/V timing from packets, with bounded memory and runtime.

    Some containers have neither stream durations nor Matroska DURATION tags.
    Their overall duration is ambiguous when another track ends later. Probe
    packets only in that case rather than assuming every track is full length.
    """
    missing = {s["index"]: s for s in media_streams(info) if stream_interval(s, info.format) is None}
    if not missing:
        return
    command = [ffprobe, "-v", "error", "-show_packets", "-show_entries",
               "packet=stream_index,pts_time,dts_time,duration_time", "-of", "compact=p=0:nk=0", str(path)]
    bounds = {}
    try:
        # Long inputs can have millions of packets. Do not capture their
        # complete listing in RAM; the temporary file is removed on exit.
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as packets:
            result = subprocess.run(command, stdout=packets, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", timeout=30, check=False)
            if result.returncode:
                raise ConversionError(f"Cannot measure media tracks: {Path(path).name}: {result.stderr[-1500:].strip()}")
            packets.seek(0)
            for line in packets:
                fields = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
                index = number(fields.get("stream_index"), None)
                if index not in missing:
                    continue
                start = number(fields.get("pts_time"), None)
                if start is None:
                    start = number(fields.get("dts_time"), None)
                if start is None:
                    continue
                duration = max(0, number(fields.get("duration_time")))
                end = start + duration
                previous = bounds.get(index)
                bounds[index] = (min(start, previous[0]), max(end, previous[1])) if previous else (start, end)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConversionError(f"Cannot measure media tracks: {Path(path).name}: {error}") from error
    for index, (start, end) in bounds.items():
        if end > start:
            missing[index]["_packet_interval"] = (start, end)


@dataclass
class MediaInfo:
    streams: list
    format: dict

    @property
    def video(self):
        return next((s for s in self.streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)

    @property
    def audio(self):
        return [s for s in self.streams if s.get("codec_type") == "audio"]

    @property
    def subtitles(self):
        return [s for s in self.streams if s.get("codec_type") == "subtitle"]

    @property
    def duration(self):
        intervals = [stream_interval(s, self.format) for s in media_streams(self)]
        if intervals and all(interval is not None for interval in intervals):
            return max(end for _, end in intervals) - min(start for start, _ in intervals)
        duration = number(self.format.get("duration"))
        if duration <= 0:
            duration = max((number(s.get("duration")) for s in self.streams), default=0)
        return duration

    @property
    def tags(self):
        tags = dict(self.format.get("tags", {}))
        if self.audio:
            tags.update(self.audio[0].get("tags", {}))
        return tags


def probe(path, ffprobe="ffprobe"):
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        if result.returncode:
            raise ConversionError(f"Cannot read media: {Path(path).name}: {result.stderr[-1500:].strip()}")
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            raise ConversionError(f"No media streams found: {path}")
        info = MediaInfo(streams, data.get("format", {}))
        read_packet_intervals(path, info, ffprobe)
        return info
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise ConversionError(f"ffprobe failed for {Path(path).name}: {error}") from error


def require_tools(settings):
    for command in (settings.ffmpeg, settings.ffprobe):
        if not shutil.which(command):
            raise ConversionError(f"Missing {command}. Install FFmpeg with ffprobe and add it to PATH, or set its full path in config.")


def encoder_names(ffmpeg="ffmpeg"):
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConversionError(f"Cannot list FFmpeg encoders: {error}") from error
    if result.returncode:
        raise ConversionError("FFmpeg could not list its encoders")
    return set(re.findall(r"^\s*[A-Z.]{6}\s+(\S+)", result.stdout, re.M))


def geometry(stream):
    width, height = number(stream.get("width")), number(stream.get("height"))
    if width <= 0 or height <= 0:
        raise ConversionError("Video has invalid dimensions")
    sar = number(stream.get("sample_aspect_ratio"), 1) or 1
    ratio = width * sar / height
    rotation = number(stream.get("tags", {}).get("rotate"))
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = number(side["rotation"])
    if round(rotation) % 180 == 90:
        ratio = 1 / ratio
    # Exact 640x360 (wide) / 640x480 (4:3 or portrait) canvas, with letterboxing.
    canvas = (640, 360) if ratio >= 1.55 else (640, 480)
    cw, ch = canvas
    if ratio >= cw / ch:
        scaled = (cw, max(2, min(ch, round(cw / ratio / 2) * 2)))
    else:
        scaled = (max(2, min(cw, round(ch * ratio / 2) * 2)), ch)
    return canvas, scaled


def video_filter(stream, settings):
    (cw, ch), (sw, sh) = geometry(stream)
    filters = []
    if stream.get("field_order") not in (None, "unknown", "progressive"):
        filters.append("bwdif=mode=send_frame:parity=auto:deint=interlaced")
    filters += [f"scale={sw}:{sh}:flags=lanczos", "setsar=1", f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2", "format=yuv420p"]
    # VFR footage can average below the cap while containing faster sections.
    # r_frame_rate describes their timestamp cadence; checking both prevents
    # those sections from bypassing the cap, while preserving lower-rate CFR.
    fps = max(number(stream.get("avg_frame_rate")), number(stream.get("r_frame_rate")))
    if fps > settings.max_fps + 0.001:
        filters.append(f"fps={settings.max_fps:g}")
    return ",".join(filters)


def build_video_command(source, destination, info, settings, encoder, sidecars=()):
    if info.video is None:
        raise ConversionError("Input has no video stream (album artwork is not video)")
    command = [settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "warning", "-n", "-xerror", "-i", str(source)]
    for subtitle in sidecars:
        command += ["-i", str(subtitle)]
    command += ["-map", f"0:{info.video['index']}", "-map", "0:a?"]
    selected = []
    if settings.subtitles == "keep":
        for sub in info.subtitles:
            if sub.get("codec_name") in TEXT_SUB_CODECS | BITMAP_SUB_CODECS:
                command += ["-map", f"0:{sub['index']}"]
                selected.append("srt" if sub["codec_name"] in TEXT_SUB_CODECS else "copy")
        for index, _ in enumerate(sidecars, 1):
            command += ["-map", f"{index}:s:0"]
            selected.append("srt")
    command += ["-map_metadata", "0", "-map_chapters", "0", "-vf", video_filter(info.video, settings),
                "-c:v", encoder, "-pix_fmt", "yuv420p", "-b:v", f"{settings.video_bitrate}k", "-bf", "0", "-g", "60"]
    if settings.profile == "h264":
        profile = "constrained_baseline" if encoder == "h264_amf" else "baseline"
        command += ["-profile:v", profile, "-level:v", "3.0", "-maxrate", f"{settings.video_bitrate}k", "-bufsize", f"{settings.video_bitrate * 2}k"]
        if encoder == "libx264":
            command += ["-preset", "medium", "-refs", "1"]
    else:
        command += ["-vtag", "XVID"]
    command += ["-c:a", "aac", "-b:a", f"{settings.audio_bitrate}k", "-ac", "2", "-ar", "48000"]
    for index, codec in enumerate(selected):
        command += [f"-c:s:{index}", codec]
    start = sum(s.get("codec_name") in TEXT_SUB_CODECS | BITMAP_SUB_CODECS for s in info.subtitles) if settings.subtitles == "keep" else 0
    if settings.subtitles == "keep":
        for index, subtitle in enumerate(sidecars, start):
            suffix = Path(subtitle).stem[len(Path(source).stem):].strip(".")
            if suffix:
                command += [f"-metadata:s:s:{index}", f"title={suffix}"]
            lang = suffix.split(".")[0].lower()
            language = {"en": "eng", "fr": "fra", "de": "deu", "es": "spa", "ja": "jpn", "ko": "kor", "pt": "por", "it": "ita", "ro": "ron", "zh": "zho"}.get(lang, lang)
            if len(language) == 3 and language.isalpha():
                command += [f"-metadata:s:s:{index}", f"language={language}"]
            if "forced" in suffix.lower().split("."):
                command += [f"-disposition:s:{index}", "forced"]
    command += ["-progress", "pipe:1", "-nostats", str(destination)]
    return command


def build_audio_command(source, destination, info, settings):
    if not info.audio:
        raise ConversionError("Input has no audio stream")
    return [settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "warning", "-n", "-xerror", "-i", str(source),
            "-map", f"0:{info.audio[0]['index']}", "-map_metadata", "0", "-vn", "-c:a", "libmp3lame",
            "-b:a", f"{settings.mp3_bitrate}k", "-ac", "2", "-ar", "44100", "-id3v2_version", "3",
            "-progress", "pipe:1", "-nostats", str(destination)]


def run_ffmpeg(command, log_path, duration=0, progress=None, timeout=0):
    """Drain both pipes; keep diagnostics bounded in memory and on disk."""
    diagnostics = deque(maxlen=250)
    events = queue.Queue(maxsize=1)
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")

    def read_progress():
        for line in process.stdout:
            if line.startswith("out_time_us="):
                seconds = number(line.partition("=")[2].strip()) / 1_000_000
                try:
                    events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    events.put_nowait(seconds)
                except queue.Full:
                    pass

    def read_errors():
        for line in process.stderr:
            diagnostics.append(line[:4000])

    readers = [threading.Thread(target=read_progress, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        while process.poll() is None:
            if timeout and time.monotonic() - started > timeout:
                raise ConversionError(f"Conversion exceeded the {timeout:g}s timeout")
            try:
                seconds = events.get(timeout=0.15)
                if progress:
                    progress(min(seconds / duration, 1) if duration > 0 else None)
            except queue.Empty:
                pass
        if process.returncode:
            raise ConversionError(f"FFmpeg exited with code {process.returncode}; see {log_path}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("".join(diagnostics), encoding="utf-8")


def verify_output(path, source_info, settings, audio=False, expected_subtitles=0):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ConversionError("FFmpeg produced no output")
    info = probe(path, settings.ffprobe)
    if audio:
        if len(info.audio) != 1 or info.audio[0].get("codec_name") != "mp3":
            raise ConversionError("Expected MP3 audio")
    else:
        stream = info.video
        codec = "h264" if settings.profile == "h264" else "mpeg4"
        if stream is None or stream.get("codec_name") != codec:
            raise ConversionError(f"Expected {codec} video")
        if settings.profile == "h264" and ("baseline" not in stream.get("profile", "").lower() or stream.get("level", 999) > 30 or stream.get("has_b_frames", 1)):
            raise ConversionError("Output does not satisfy the H.264 baseline/level 3.0 constraints")
        if (stream.get("width"), stream.get("height")) != geometry(source_info.video)[0] or stream.get("pix_fmt") != "yuv420p":
            raise ConversionError("Output dimensions or pixel format are incorrect")
        if abs(number(stream.get("sample_aspect_ratio"), 1) - 1) > 0.001:
            raise ConversionError("Output pixels are not square")
        fps = max(number(stream.get("avg_frame_rate")), number(stream.get("r_frame_rate")))
        if fps > settings.max_fps + 0.1:
            raise ConversionError("Output frame rate exceeds the configured cap")
        if len(info.audio) != len(source_info.audio) or any(s.get("codec_name") != "aac" or s.get("channels", 0) > 2 for s in info.audio):
            raise ConversionError("Output audio tracks are missing or incompatible")
        if len(info.subtitles) != expected_subtitles:
            raise ConversionError("Output subtitle tracks are missing")
    original_streams, converted_streams = media_streams(source_info, audio), media_streams(info, audio)
    if len(original_streams) != len(converted_streams) or not original_streams:
        raise ConversionError("Output media tracks are missing")
    original_intervals = [stream_interval(s, source_info.format) for s in original_streams]
    converted_intervals = [stream_interval(s, info.format) for s in converted_streams]
    if any(interval is None for interval in original_intervals + converted_intervals):
        raise ConversionError("Media track duration is unknown; cannot verify a complete conversion")
    original_start = min(start for start, _ in original_intervals)
    converted_start = min(start for start, _ in converted_intervals)
    for original, converted in zip(original_intervals, converted_intervals):
        duration = original[1] - original[0]
        tolerance = max(0.5, duration * 0.01)
        if abs((converted[1] - converted[0]) - duration) > tolerance:
            raise ConversionError("Output media track duration differs from the source; original was kept")
        if abs((converted[0] - converted_start) - (original[0] - original_start)) > tolerance:
            raise ConversionError("Output media track timing differs from the source; original was kept")
    return info


def decode_check(path, settings, log_path):
    run_ffmpeg([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                "-i", str(path), "-map", "0:v?", "-map", "0:a?", "-f", "null", "-progress", "pipe:1", "-nostats", "-"],
               log_path, timeout=settings.timeout)


def preservation_warnings(info, settings, audio=False):
    warnings = []
    if any(s.get("codec_type") == "attachment" for s in info.streams):
        warnings.append("Embedded attachments are omitted from the converted copy")
    if any(s.get("disposition", {}).get("attached_pic") for s in info.streams):
        warnings.append("Embedded cover artwork is omitted from the converted copy")
    if audio and len(info.audio) > 1:
        warnings.append(f"Only the first audio track is retained in MP3; {len(info.audio) - 1} additional audio track(s) are omitted")
    if not audio and settings.subtitles == "keep":
        styled = sorted({s.get("codec_name") for s in info.subtitles
                         if s.get("codec_name") in TEXT_SUB_CODECS - {"subrip"}})
        if styled:
            warnings.append("Embedded subtitles converted to SRT may lose formatting or positioning: " + ", ".join(styled))
        skipped = sorted({s.get("codec_name") or "unknown" for s in info.subtitles
                          if s.get("codec_name") not in TEXT_SUB_CODECS | BITMAP_SUB_CODECS})
        if skipped:
            warnings.append("Unsupported embedded subtitles omitted: " + ", ".join(skipped))
    return warnings


class Converter:
    def __init__(self, settings, log_dir):
        self.settings = settings
        self.log_dir = Path(log_dir)
        self.encoders = encoder_names(settings.ffmpeg)
        self.failed_encoders = set()

    def candidates(self, audio=False):
        if audio:
            names = ["libmp3lame"]
        elif self.settings.profile == "mpeg4":
            names = ["mpeg4"]
        else:
            gpu = {"nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"}
            names = [gpu[self.settings.encoder]] if self.settings.encoder in gpu else []
            names += ["libx264"]
        return [n for n in names if n in self.encoders and n not in self.failed_encoders]

    def convert(self, source, destination, info, sidecars=(), audio=False, progress=None, warning=None):
        if not audio and info.video and info.video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
            raise ConversionError("HDR input requires an SDR tone-mapped copy first; original kept")
        candidates = self.candidates(audio)
        if not candidates:
            raise ConversionError("No usable encoder found in this FFmpeg build")
        if not audio and self.settings.encoder != "cpu" and self.settings.profile == "h264":
            expected = {"nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"}[self.settings.encoder]
            if expected not in candidates and warning:
                warning(f"{expected} unavailable; using CPU")
        skipped = [s for s in info.subtitles if s.get("codec_name") not in TEXT_SUB_CODECS | BITMAP_SUB_CODECS]
        if warning:
            for message in preservation_warnings(info, self.settings, audio):
                warning(message)
        for encoder in candidates:
            log = self.log_dir / f"{Path(destination).stem}-{encoder}.log"
            command = build_audio_command(source, destination, info, self.settings) if audio else build_video_command(source, destination, info, self.settings, encoder, sidecars)
            try:
                run_ffmpeg(command, log, info.duration, progress, self.settings.timeout)
            except ConversionError:
                Path(destination).unlink(missing_ok=True)
                if encoder.startswith("h264_") and encoder != candidates[-1]:
                    self.failed_encoders.add(encoder)
                    if warning:
                        warning(f"{encoder} failed; using CPU fallback (details: {log})")
                    continue
                raise
            count = 0 if self.settings.subtitles == "none" else len(info.subtitles) - len(skipped) + len(sidecars)
            verify_output(destination, info, self.settings, audio, count)
            decode_check(destination, self.settings, self.log_dir / f"{Path(destination).stem}-verify.log")
            return encoder
        raise ConversionError("All encoder attempts failed")
