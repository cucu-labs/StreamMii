import argparse
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import uuid

from config import ADULT_MODES, AUDIO_EXTS, CATEGORIES, VERSION, Settings, load_config
from ffmpeg_utils import ConversionError, Converter, encoder_names, preservation_warnings, probe, require_tools
from logging_utils import Journal, LibraryLock, artifact_matches, artifact_record, fingerprint, job_key, unchanged
from organizer import (OrganizeOptions, available_destination, discover, find_sidecars,
                       is_within, plan_destination, subtitle_target)


def parser():
    result = argparse.ArgumentParser(description="StreamMii — convert media and build a WiiMC library safely.")
    result.add_argument("--version", action="version", version=f"StreamMii {VERSION}")
    sub = result.add_subparsers(dest="command")
    doctor = sub.add_parser("doctor", help="Check FFmpeg, ffprobe and available encoders")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--test-gpu", action="store_true", help="Actually test each advertised GPU encoder with synthetic video")
    run = sub.add_parser("run", help="Convert or organize a batch (originals kept by default)")
    run.add_argument("inputs", nargs="+", type=Path, help="One or more files/folders")
    run.add_argument("--output", "-o", required=True, type=Path, help="Destination library; must be separate from inputs")
    run.add_argument("--config", type=Path, help="Optional JSON settings file")
    run.add_argument("--category", choices=CATEGORIES, default="auto")
    run.add_argument("--sport", help="Sport name; known names use the matching sports group")
    run.add_argument("--adult-mode", choices=ADULT_MODES)
    run.add_argument("--creator", action="append", default=[], help="Creator name; repeat for a group")
    run.add_argument("--title", help="Override movie/show title for this batch")
    run.add_argument("--year", type=int)
    run.add_argument("--season", type=int, help="Season override (0 means specials)")
    run.add_argument("--encoder", choices=("cpu", "amd", "nvidia", "intel"))
    run.add_argument("--profile", choices=("h264", "mpeg4"))
    run.add_argument("--video-bitrate", type=int, help="Video bitrate in kbps (default 1000)")
    run.add_argument("--max-fps", type=float, help="Frame rate cap, 1–30; lower rates are preserved")
    run.add_argument("--subtitles", choices=("keep", "none"))
    run.add_argument("--timeout", type=float, help="Maximum seconds per conversion/verification; 0 is unlimited")
    run.add_argument("--online-metadata", action="store_true", default=None, help="Opt in to OMDb movie-title lookups; set STREAMMII_OMDB_API_KEY")
    run.add_argument("--dry-run", action="store_true", help="Show destinations without writing anything")
    run.add_argument("--organize-only", action="store_true", help="Copy files into the library without re-encoding")
    run.add_argument("--delete-originals", action="store_true", help="Delete each unchanged media source only after verified publication and durable journal commit; subtitles are kept")
    run.add_argument("--allow-lossy-deletion", action="store_true", help="With --delete-originals, accept loss of subtitle formatting, unsupported subtitles, attachments, cover artwork, or extra audio tracks")
    run.add_argument("--force", action="store_true", help="Process again, creating a new numbered copy")
    run.add_argument("--no-recursive", action="store_true")
    run.add_argument("--state-dir", type=Path, help="Journal/log directory (default OUTPUT/.streammii)")
    run.add_argument("--report", type=Path, help="Also save a JSON report to this new file; existing files are refused")
    run.add_argument("--quiet", action="store_true", help="Hide progress; retain result lines and errors")
    return result


def settings_from_args(args):
    if getattr(args, "menu_settings", None) is not None:
        return args.menu_settings
    settings = load_config(args.config)
    values = {name: getattr(args, name) for name in settings.to_dict() if getattr(args, name, None) is not None}
    return replace(settings, **values)


def assert_destination_safe(destination, library):
    destination, library = Path(destination), Path(library).resolve()
    if not is_within(destination, library):
        raise ValueError("Destination points outside the library")
    current = destination
    while current != library:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError(f"Refusing a symlink/junction in the output path: {current}")
        current = current.parent


def publish(source, destination, library):
    source, destination = Path(source), Path(destination)
    assert_destination_safe(destination, library)
    destination.parent.mkdir(parents=True, exist_ok=True)
    assert_destination_safe(destination, library)
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        if os.name == "nt":
            os.rename(source, destination)
            return
        created = False
        try:
            with destination.open("xb") as target:
                created = True
                with source.open("rb") as original:
                    shutil.copyfileobj(original, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
    source.unlink()
    if os.name != "nt":
        directory = destination.parent
        while directory.is_relative_to(Path(library)):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if directory == Path(library):
                break
            directory = directory.parent


def durable_file(path):
    with Path(path).open("r+b") as handle:
        os.fsync(handle.fileno())


def copy_to_stage(source, destination):
    with Path(source).open("rb") as original, Path(destination).open("xb") as target:
        shutil.copyfileobj(original, target, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())


def check_deletion_preservation(info, settings, args, audio):
    if args.organize_only or not args.delete_originals:
        return
    losses = preservation_warnings(info, settings, audio)
    if losses and not getattr(args, "allow_lossy_deletion", False):
        raise ConversionError("Original cannot be deleted: " + "; ".join(losses) +
                              ". Keep originals, or explicitly accept these losses with --allow-lossy-deletion")


def recover_pending(journal, key, library):
    """Remove only unchanged artifacts owned by an incomplete publication."""
    entry = journal.lookup(key)
    if not entry or entry[0] != "pending":
        return
    artifacts = entry[1].get("artifacts", [])
    existing = []
    for record in artifacts:
        path = Path(record["path"])
        assert_destination_safe(path, library)
        if os.path.lexists(path):
            if not artifact_matches(record):
                raise OSError(f"Interrupted job output has changed; refusing to remove it: {path}")
            existing.append((path, record))
    for path, record in existing:
        if not artifact_matches(record):
            raise OSError(f"Interrupted job output changed during recovery: {path}")
        path.unlink()
    journal.put(key, "retry", entry[1])


def delete_verified_original(source, snapshot, result, journal, key):
    def warning(message):
        result.setdefault("warnings", []).append(message)
        print(f"  Warning: {message}", file=sys.stderr)

    result["original_deleted"] = False
    if unchanged(snapshot) and all(artifact_matches(a) for a in result["artifacts"]):
        try:
            source.unlink()
            result["original_deleted"] = True
        except OSError as error:
            warning(f"Library copy is complete, but the original could not be deleted: {error}")
    else:
        warning("Source or output changed before deletion; original kept")
    try:
        journal.put(key, "complete", result)
    except sqlite3.Error:
        warning("Could not update the deletion annotation in the journal")


def process_one(source, library, options, settings, args, journal, converter, state):
    audio = options.category == "audio" or (options.category == "auto" and source.suffix.lower() in AUDIO_EXTS)
    snapshot = fingerprint(source)
    sidecars = find_sidecars(source) if not audio and settings.subtitles == "keep" else []
    subtitle_snapshots = [fingerprint(p) for p in sidecars]
    payload = {"version": VERSION, "source": snapshot, "subtitles": subtitle_snapshots,
               "settings": settings.to_dict(), "organization": asdict(options),
               "library": str(library), "convert": not args.organize_only}
    key = job_key(payload)
    if not args.force:
        completed = journal.completed(key)
        if completed:
            if args.delete_originals:
                if not args.organize_only:
                    check_deletion_preservation(probe(source, settings.ffprobe), settings, args, audio)
                delete_verified_original(source, snapshot, completed, journal, key)
            return {**completed, "source": str(source), "status": "skipped", "destination": completed["destination"],
                    "reason": "Source, settings and verified library files match the journal"}
        recover_pending(journal, key, library)
    info = probe(source, settings.ffprobe)
    desired = plan_destination(source, library, options, info.tags, not args.organize_only,
                               settings.online_metadata, settings.omdb_api_key)
    destination = available_destination(desired, source, sidecars)
    assert_destination_safe(destination, library)
    warnings = []

    def warning(message):
        warnings.append(message)
        print(f"  Warning: {message}", file=sys.stderr)

    if info.duration <= 0 and not args.organize_only:
        raise ConversionError("Input duration is unknown; cannot safely verify a complete conversion")
    check_deletion_preservation(info, settings, args, audio)
    stage_root = library / ".streammii-staging"
    assert_destination_safe(stage_root, library)
    stage_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{key[:12]}-", dir=stage_root) as temp:
        temp = Path(temp)
        staged_media = temp / ("media" + destination.suffix)
        if args.organize_only:
            copy_to_stage(source, staged_media)
            if artifact_record(staged_media)["sha256"] != snapshot["sha256"]:
                raise OSError("Source changed during copy")
            encoder = "copy"
        else:
            def progress(fraction):
                if not args.quiet and sys.stdout.isatty() and fraction is not None:
                    print(f"\r  Converting {fraction * 100:5.1f}%", end="", flush=True)
            converter.log_dir = state / "logs" / key / uuid.uuid4().hex[:8]
            encoder = converter.convert(source, staged_media, info, sidecars, audio, progress, warning)
            if not args.quiet and sys.stdout.isatty():
                print()
            durable_file(staged_media)
        staged = [(staged_media, destination)]
        for index, sub in enumerate(sidecars):
            staged_sub = temp / f"subtitle-{index}{sub.suffix.lower()}"
            copy_to_stage(sub, staged_sub)
            if artifact_record(staged_sub)["sha256"] != subtitle_snapshots[index]["sha256"]:
                raise OSError("Subtitle changed during copy")
            staged.append((staged_sub, subtitle_target(source, sub, destination)))
        if not unchanged(snapshot) or not all(unchanged(s) for s in subtitle_snapshots):
            raise OSError("Source or subtitle changed during processing; original kept")
        artifacts = [artifact_record(stage, final) for stage, final in staged]
        result = {"source": str(source), "status": "converted" if not args.organize_only else "organized",
                  "destination": str(destination), "encoder": encoder, "artifacts": artifacts,
                  "warnings": warnings, "original_deleted": False}
        journal.put(key, "pending", result)
        published = []
        try:
            for stage, final in staged:
                publish(stage, final, library)
                published.append(final)
            if not all(artifact_matches(a) for a in artifacts):
                raise OSError("Published files failed integrity checks")
            journal.put(key, "complete", result)
        except BaseException:
            for final in published:
                record = next(a for a in artifacts if a["path"] == str(final))
                if artifact_matches(record):
                    final.unlink(missing_ok=True)
            raise
    if args.delete_originals:
        delete_verified_original(source, snapshot, result, journal, key)
    return result


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def run_batch(args):
    settings = settings_from_args(args)
    options = OrganizeOptions(args.category, args.sport, args.adult_mode, tuple(args.creator), args.title, args.year, args.season)
    library = args.output.expanduser().resolve()
    state = args.state_dir.expanduser().resolve() if args.state_dir else library / ".streammii"
    if state == library:
        raise ValueError("State directory must be a separate subfolder or outside the library")
    if not args.state_dir:
        assert_destination_safe(state / "jobs.sqlite3", library)
    if not args.dry_run and args.report and os.path.lexists(args.report):
        raise FileExistsError(f"Report already exists: {args.report}")
    sources = discover(args.inputs, library, state, options.category, not args.no_recursive)
    if getattr(args, "video_only", False):
        sources = [source for source in sources if source.suffix.lower() not in AUDIO_EXTS]
    require_tools(settings)
    if settings.online_metadata and not (settings.omdb_api_key or os.environ.get("STREAMMII_OMDB_API_KEY")):
        print("Warning: no OMDb API key is configured; using local metadata", file=sys.stderr)
    if args.dry_run:
        reserved = set()
        for source in sources:
            info = probe(source, settings.ffprobe)
            sidecars = find_sidecars(source) if settings.subtitles == "keep" and source.suffix.lower() not in AUDIO_EXTS else []
            destination = plan_destination(source, library, options, info.tags, not args.organize_only,
                                           settings.online_metadata, settings.omdb_api_key)
            candidate = available_destination(destination, source, sidecars)
            index = 2
            while str(candidate).casefold() in reserved:
                candidate = available_destination(destination.with_name(f"{destination.stem} ({index}){destination.suffix}"), source, sidecars)
                index += 1
            assert_destination_safe(candidate, library)
            reserved.add(str(candidate).casefold())
            print(f"PLAN {source}\n  -> {candidate}")
        print(f"Preview: {len(sources)} media file(s). No files written. Existing journal entries are not consulted in preview.")
        return 0
    if not sources:
        print("No matching media found. Nothing changed.")
        return 0
    report = {"version": VERSION, "started": datetime.now(timezone.utc).isoformat(),
              "library": str(library), "results": [], "summary": {"converted": 0, "organized": 0, "skipped": 0, "failed": 0}, "cancelled": False}
    report_path = state / "reports" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".json")
    with ExitStack() as stack:
        stack.enter_context(LibraryLock(library))
        journal = stack.enter_context(Journal(state))
        converter = None if args.organize_only else Converter(settings, state / "logs")
        if settings.profile == "mpeg4" and settings.encoder != "cpu":
            print("MPEG-4 profile uses the CPU encoder.")
        try:
            for index, source in enumerate(sources, 1):
                print(f"[{index}/{len(sources)}] {source.name}")
                try:
                    result = process_one(source, library, options, settings, args, journal, converter, state)
                except (OSError, ValueError, ConversionError, sqlite3.Error) as error:
                    result = {"source": str(source), "status": "failed", "error": str(error)}
                    print(f"  FAILED: {error}", file=sys.stderr)
                report["results"].append(result)
                report["summary"][result["status"]] += 1
                if result["status"] != "failed":
                    print(f"  {result['status'].upper()}: {result['destination']}")
        except KeyboardInterrupt:
            report["cancelled"] = True
            print("\nCancelled. Completed library files are saved; the current source is kept.", file=sys.stderr)
        finally:
            report["finished"] = datetime.now(timezone.utc).isoformat()
            write_report(report_path, report)
            if args.report:
                write_report(args.report, report)
    summary = report["summary"]
    print("Done: " + ", ".join(f"{value} {name}" for name, value in summary.items()))
    print(f"Report: {report_path}")
    return 130 if report["cancelled"] else 1 if summary["failed"] else 0


def doctor(args):
    settings = getattr(args, "menu_settings", None) or load_config(args.config)
    require_tools(settings)
    names = encoder_names(settings.ffmpeg)
    print(f"StreamMii {VERSION} | Python {sys.version.split()[0]}")
    for tool in (settings.ffmpeg, settings.ffprobe):
        print(f"Found: {shutil.which(tool)}")
    for encoder in ("libx264", "mpeg4", "libmp3lame", "aac", "h264_nvenc", "h264_amf", "h264_qsv"):
        print(f"{encoder}: {'included in FFmpeg' if encoder in names else 'not included'}")
    if args.test_gpu:
        import subprocess
        for encoder in ("h264_nvenc", "h264_amf", "h264_qsv"):
            if encoder not in names:
                continue
            try:
                result = subprocess.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                                         "color=size=640x360:rate=25:duration=0.2", "-c:v", encoder,
                                         "-profile:v", "constrained_baseline" if encoder == "h264_amf" else "baseline",
                                         "-level:v", "3.0", "-bf", "0", "-f", "null", "-"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
            except subprocess.TimeoutExpired:
                print(f"{encoder} runtime: test timed out")
                continue
            print(f"{encoder} runtime: {'PASS' if result.returncode == 0 else 'unavailable on this machine'}")
            if result.returncode:
                print("  " + result.stderr.strip().replace("\n", " ")[:400])
    else:
        print("An included GPU encoder still needs compatible hardware and drivers. Use --test-gpu to check.")
    return 0 if {"aac", "libmp3lame", "libx264"} <= names else 1


def main(argv=None):
    cli = parser()
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if not arguments:
            from menu import menu_loop
            return menu_loop()
        args = cli.parse_args(arguments)
        if args.command == "doctor":
            return doctor(args)
        if args.command == "run":
            return run_batch(args)
        cli.print_help()
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
