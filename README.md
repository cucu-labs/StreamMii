# StreamMii 0.0.5

StreamMii converts media into formats intended for WiiMC playback and organizes the results into a library.

Also see [@motan1337](https://github.com/motan1337)'s alternative version, [StreamMii-Monolithic](https://github.com/motan1337/StreamMii-Monolithic).

## Install and launch

Use Python 3.14 (recommended) and an FFmpeg build that includes `ffmpeg` and `ffprobe`. Both executables must be on your `PATH`, or their paths must be set in a JSON configuration file. The normal CPU conversion paths require `libx264`, `aac`, and `libmp3lame`; the optional MPEG-4 profile uses `mpeg4`. GPU encoding also requires a compatible FFmpeg build, GPU, and driver.

Open a terminal in the StreamMii folder, then run:

```powershell
python -m pip install -r requirements.txt
python streammii.py doctor
python streammii.py
```

The first launch asks for an input folder, an output library, an encoder, and an optional OMDb API key. Press Enter at the folder prompts to use `Incoming` and `Library` beside the application. Put your media in the input folder before converting. Preferences are saved in `user_settings.json` beside the application, so this folder must be writable.

To test whether the GPU encoders can actually run:

```powershell
python streammii.py doctor --test-gpu
```

H.264 encoding supports AMD AMF, NVIDIA NVENC, Intel QSV, and CPU encoding through libx264. If the selected GPU encoder is unavailable or encoding fails, StreamMii can fall back to libx264. The MPEG-4 profile uses the CPU. Quality and speed depend on the encoder, hardware, source, and settings.

## Using the menu

Choose a media category, then choose an operation:

1. **Convert and organize:** create a converted library copy.
2. **Preview conversion:** show planned conversion destinations without writing files.
3. **Organize without converting:** copy the original media into the library with its existing format and extension.
4. **Correct title / year / season:** apply overrides to the entire selected batch.
5. **Preview organization only:** show destinations for copying without conversion.

Choose a distinct output library; input files cannot be inside the library. Scans are recursive by default and exclude the output library and state directory. Organize-only mode does not make otherwise incompatible media playable on the Wii.

Settings control the encoder, format, subtitles, frame rate, bitrate, metadata lookups, and whether originals are kept. The default is to keep originals. Ctrl+C cancels processing; completed library files remain available.

## CLI examples

While you can simply use the `streammii.py` file to use the more familiar menu, I thought I'd add these examples so it's easier to pick what you want.

Preview and convert a folder:

```powershell
python streammii.py run "C:\StreamMii\Incoming" --output "C:\StreamMii\WiiLibrary" --dry-run
python streammii.py run "C:\StreamMii\Incoming" --output "C:\StreamMii\WiiLibrary" --encoder nvidia
```

Preview and copy files without conversion:

```powershell
python streammii.py run "C:\StreamMii\Incoming" --output "C:\StreamMii\WiiLibrary" --organize-only --dry-run
python streammii.py run "C:\StreamMii\Incoming" --output "C:\StreamMii\WiiLibrary" --organize-only
```

Choose a category or correct a batch's metadata:

```powershell
python streammii.py run "C:\StreamMii\Album" --output "C:\StreamMii\WiiLibrary" --category audio
python streammii.py run "C:\StreamMii\Show" --output "C:\StreamMii\WiiLibrary" --category tv --title "Show Title" --season 1
python streammii.py run "C:\StreamMii\Races" --output "C:\StreamMii\WiiLibrary" --category sports --sport F1
python streammii.py run --help
```

Multiple input files or folders can be supplied in one command. Use `--no-recursive` to scan only the specified folder level. Sports requires `--sport`; adult categories require `--adult-mode` and for solo/group modes one or more `--creator` values.

## Default output settings

| Parameter | Video conversion |
| :--- | :--- |
| Container | MKV |
| Video codec | H.264; optional MPEG-4 profile |
| Resolution | 640×360 canvas for wide video; 640×480 otherwise, with padding to preserve aspect ratio |
| Scaling | Lanczos |
| Video bitrate | 1000 kbps |
| Frame rate | Up to 30 FPS; lower rates are preserved |
| Audio | AAC stereo, 128 kbps/track, 48 kHz |

**Audio-only conversion produces MP3**, using the first audio stream, at 192 kbps, stereo, 44.1 kHz by default. It does not produce AAC. HDR video is rejected.

## Preview, resume, and originals

Preview probes the input files and prints planned destinations. It does not convert, copy, delete, or write reports. It does not consult the completion journal, so a later run may skip completed files even though they appeared in the preview.

Normal runs store a journal, logs, and JSON reports under `Library/.streammii`. Matching completed jobs are skipped only while the source, settings, and recorded output files still match. Changing settings or using `--force` can create another numbered copy. Existing media destinations are not overwritten. `--state-dir` selects another state location, and `--report` writes an additional report to a new file.

Use `--delete-originals`, or the menu's originals setting, only when you want each source media file removed after successful publication and verification. Original subtitle sidecars are kept. If deletion fails, a subsequent matching run can retry deletion without converting the media again.

Conversion deliberately changes image quality, dimensions, audio quality, and channel layout. Its verification checks successful decoding and expected output properties; it does not establish that the result is an archival replacement or guarantee playback on every Wii setup.

The deletion safeguard also checks for detected content losses, including omitted embedded attachments or cover artwork, loss of extra audio streams during MP3 conversion, unsupported embedded subtitles, and subtitle formatting changes. Such losses prevent deleting the original unless explicitly accepted with `--allow-lossy-deletion`. Keep originals to retain that content. `--subtitles none` explicitly requests subtitle omission and is not separately blocked by this safeguard.

Subtitle handling with the default `--subtitles keep`:

* Matching `.srt`, `.ass`, `.ssa`, and `.vtt` sidecars are copied beside the video and also embedded as SRT during conversion.
* Supported embedded text subtitles are converted to SRT; styling or positioning may change.
* Supported embedded bitmap subtitles are copied. Unsupported embedded subtitle codecs are omitted with a warning.
* Embedded attachments are not copied during conversion. Organize-only mode copies the original media bytes instead.

## Metadata and configuration

Local filename parsing is available without an online service. OMDb movie/TV title lookups are optional: enable them in the menu or use `--online-metadata` with the `STREAMMII_OMDB_API_KEY` environment variable. A key entered in the menu is stored as plaintext in local `user_settings.json`; it is excluded from reports.

CLI runs use defaults and their own command-line/JSON settings; they do not automatically load the menu's preferences. For example, save this as `streammii-config.json`:

```json
{
  "encoder": "cpu",
  "profile": "h264",
  "video_bitrate": 1000,
  "audio_bitrate": 128,
  "mp3_bitrate": 192,
  "max_fps": 30,
  "subtitles": "keep",
  "ffmpeg": "ffmpeg",
  "ffprobe": "ffprobe",
  "timeout": 0,
  "online_metadata": false
}
```

```powershell
python streammii.py run "C:\StreamMii\Incoming" --output "C:\StreamMii\WiiLibrary" --config streammii-config.json
python streammii.py doctor --config streammii-config.json
```

All JSON settings are optional. CLI flags override their corresponding settings. `timeout` is the maximum number of seconds for each conversion or verification step; `0` means unlimited. The CLI configuration contains settings directly, unlike the menu's `user_settings.json` wrapper.

## Categories

Movies, TV shows, audio, documentaries, K-drama, anime, sports, and adult content are supported. Adult modes are solo, group, hentai, JAV, LGBT solo, and LGBT group. The menu groups sports into team, combat, winter, water, motor, individual, equestrian, and other sports; the CLI also accepts a custom sport name.
