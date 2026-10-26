from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from config import Settings, VERSION
from console_utils import ASCII_ART, purple_console
from organizer import SPORTS, is_within

APP_DIR = Path(__file__).resolve().parent
PREFERENCES_FILE = APP_DIR / "user_settings.json"
MODES = {
    "1": ("Movies / TV Shows (auto detect)", "auto"),
    "2": ("Audio", "audio"),
    "3": ("Adult", "adult"),
    "4": ("Documentaries", "documentaries"),
    "5": ("K-Drama", "k-drama"),
    "6": ("Anime", "anime"),
    "7": ("Sports", "sports"),
}
ENCODERS = {"amd": "AMD", "nvidia": "NVIDIA", "cpu": "CPU", "intel": "Intel"}


@dataclass
class Preferences:
    source: Path
    library: Path
    settings: Settings = Settings()
    delete_originals: bool = False
    encoder_choice_needed: bool = False

    def validate(self):
        self.source = Path(self.source).expanduser().resolve()
        self.library = Path(self.library).expanduser().resolve()
        if is_within(self.source, self.library):
            raise ValueError("Choose an output folder separate from the input folder.")
        if not isinstance(self.delete_originals, bool):
            raise ValueError("Invalid setting for keeping originals.")


def save_preferences(preferences, path=PREFERENCES_FILE):
    preferences.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": VERSION, "input_folder": str(preferences.source),
            "output_folder": str(preferences.library), "settings": preferences.settings.to_dict(include_secrets=True),
            "delete_originals": preferences.delete_originals}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_preferences(path=PREFERENCES_FILE):
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        raise ValueError("Saved settings are incomplete.")
    for name in ("input_folder", "output_folder"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise ValueError("Saved folder settings are incomplete.")
    settings = dict(data["settings"])
    needs_choice = settings.get("encoder") in (None, "auto")
    if needs_choice:
        settings["encoder"] = "cpu"
    preferences = Preferences(Path(data["input_folder"]), Path(data["output_folder"]),
                              Settings(**settings), data.get("delete_originals", False), needs_choice)
    preferences.validate()
    return preferences


def choose(prompt, choices, default=None):
    while True:
        value = input(prompt).strip()
        if not value and default is not None:
            value = default
        if value in choices:
            return value
        print("Please enter one of the numbers shown.")


def folder_prompt(label, default, must_exist=False):
    while True:
        raw = input(f"{label}\n[{default}]\n> ").strip().strip('"')
        folder = Path(raw or default).expanduser().resolve()
        if folder.exists() and not folder.is_dir():
            print("Please choose a folder, not a file.")
            continue
        if must_exist and not folder.is_dir():
            if not raw:
                folder.mkdir(parents=True, exist_ok=True)
                return folder
            print("That folder was not found. Check the path or press Enter for the default.")
            continue
        return folder


def encoder_menu(allow_back=False):
    print("\nChoose the encoder you want to use:")
    print("1 > AMD\n2 > NVIDIA (NVENC)\n3 > CPU fallback\n4 > Intel (QSV)")
    if allow_back:
        print("0 > Back")
    value = choose("> ", tuple("01234") if allow_back else tuple("1234"))
    return {"1": "amd", "2": "nvidia", "3": "cpu", "4": "intel"}.get(value)


def read_api_key():
    if sys.stdin.isatty():
        from getpass import getpass
        return getpass("OMDb API key (Enter to skip): ").strip()
    return input("OMDb API key (Enter to skip): ").strip()


def omdb_menu(settings):
    while True:
        status = "enabled" if settings.online_metadata else "off"
        print(f"\nOMDb movie / TV metadata: {status}")
        print("The API key is saved in your local settings and never shown in reports.")
        print("1 > Add / replace API key\n2 > Turn lookups on / off\n3 > Remove saved API key\n0 > Back")
        value = choose("> ", tuple("0123"))
        if value == "0":
            return settings
        if value == "1":
            key = read_api_key()
            if key:
                settings = replace(settings, omdb_api_key=key, online_metadata=True)
                print("API key set. Movie and TV title lookups are enabled.")
        elif value == "2":
            if not settings.omdb_api_key and not os.environ.get("STREAMMII_OMDB_API_KEY"):
                print("Add an API key first.")
            else:
                settings = replace(settings, online_metadata=not settings.online_metadata)
        else:
            settings = replace(settings, omdb_api_key="", online_metadata=False)
            print("Saved API key removed.")


def first_launch(path=PREFERENCES_FILE):
    print("\nWelcome to StreamMii! Let's choose your media folders.")
    print("You can paste a folder path or drag a folder into this window.")
    base = Path(path).parent
    source = folder_prompt("Folder with your media", base / "Incoming", must_exist=True)
    while True:
        library = folder_prompt("Folder for your Wii library", base / "Library")
        preferences = Preferences(source, library)
        try:
            preferences.validate()
            break
        except ValueError as error:
            print(error)
    encoder = encoder_menu()
    print("\nOptional: add your OMDb API key for movie and TV title lookups.")
    print("The key is saved in your local settings. Press Enter to skip.")
    key = read_api_key()
    preferences.settings = replace(preferences.settings, encoder=encoder, omdb_api_key=key, online_metadata=bool(key))
    save_preferences(preferences, path)
    print(f"\nSettings saved. Encoder: {ENCODERS[encoder]}. Originals will be kept.")
    return preferences


def settings_menu(preferences, path):
    while True:
        s = preferences.settings
        print("\nSETTINGS")
        print(f"1 > Input folder: {preferences.source}")
        print(f"2 > Wii library folder: {preferences.library}")
        print(f"3 > Encoder: {ENCODERS[s.encoder]}")
        print(f"4 > Video format: {'H.264' if s.profile == 'h264' else 'MPEG-4'}")
        print(f"5 > Originals: {'delete after verified success' if preferences.delete_originals else 'keep'}")
        print(f"6 > Subtitles: {'keep' if s.subtitles == 'keep' else 'omit'}")
        print(f"7 > Maximum frame rate: {s.max_fps:g} fps")
        print(f"8 > Video quality: {s.video_bitrate} kbps")
        print(f"9 > OMDb API key / metadata: {'on (key set)' if s.online_metadata and s.omdb_api_key else 'on (environment key)' if s.online_metadata else 'off'}")
        print("0 > Back")
        choice = choose("> ", tuple("0123456789"))
        if choice == "0":
            return
        previous = replace(preferences)
        try:
            if choice == "1":
                preferences.source = folder_prompt("Input folder", preferences.source, must_exist=True)
            elif choice == "2":
                preferences.library = folder_prompt("Wii library folder", preferences.library)
            elif choice == "3":
                value = encoder_menu(allow_back=True)
                if value is None:
                    continue
                preferences.settings = replace(s, encoder=value)
            elif choice == "4":
                print("\n1 > H.264 (GPU or CPU)\n2 > MPEG-4 (CPU)\n0 > Back")
                value = choose("> ", tuple("012"))
                if value == "0":
                    continue
                preferences.settings = replace(s, profile="h264" if value == "1" else "mpeg4")
            elif choice == "5":
                print("\n1 > Keep originals (recommended)\n2 > Delete originals only after verified conversion and organization\n0 > Back")
                value = choose("> ", tuple("012"))
                if value == "0":
                    continue
                preferences.delete_originals = value == "2"
            elif choice == "6":
                print("\n1 > Keep subtitles\n2 > Omit subtitles from the Wii copy\n0 > Back")
                value = choose("> ", tuple("012"))
                if value == "0":
                    continue
                preferences.settings = replace(s, subtitles="keep" if value == "1" else "none")
            elif choice == "7":
                print("\n1 > Up to 30 fps (recommended)\n2 > Up to 25 fps\n3 > Up to 24 fps\n0 > Back")
                value = choose("> ", tuple("0123"))
                if value == "0":
                    continue
                preferences.settings = replace(s, max_fps={"1": 30, "2": 25, "3": 24}[value])
            elif choice == "8":
                print("\n1 > 1000 kbps (recommended)\n2 > 750 kbps (smaller files)\n3 > 1500 kbps (larger files)\n0 > Back")
                value = choose("> ", tuple("0123"))
                if value == "0":
                    continue
                preferences.settings = replace(s, video_bitrate={"1": 1000, "2": 750, "3": 1500}[value])
            else:
                preferences.settings = omdb_menu(s)
            save_preferences(preferences, path)
            print("Saved.")
        except (OSError, ValueError) as error:
            preferences.source, preferences.library = previous.source, previous.library
            preferences.settings, preferences.delete_originals = previous.settings, previous.delete_originals
            print(f"Could not save: {error}")


def sport_menu():
    groups = list(SPORTS)
    while True:
        print("\nSPORTS")
        for index, group in enumerate(groups, 1):
            print(f"{index} > {group}")
        print("0 > Back")
        group = choose("> ", [str(i) for i in range(len(groups) + 1)])
        if group == "0":
            return None
        names = SPORTS[groups[int(group) - 1]]
        for index, name in enumerate(names, 1):
            print(f"{index} > {name}")
        print("0 > Back")
        sport = choose("> ", [str(i) for i in range(len(names) + 1)])
        if sport != "0":
            return names[int(sport) - 1]


def adult_menu():
    print("\nADULT\n1 > Solo\n2 > Group\n3 > Hentai\n4 > JAV\n5 > LGBT\n0 > Back")
    choice = choose("> ", tuple("012345"))
    if choice == "0":
        return None
    modes = {"1": "solo", "2": "group", "3": "hentai", "4": "jav"}
    if choice == "5":
        print("\n1 > Solo\n2 > Group\n0 > Back")
        choice = choose("> ", tuple("012"))
        if choice == "0":
            return None
        mode = "lgbt-solo" if choice == "1" else "lgbt-group"
    else:
        mode = modes[choice]
    creators = []
    if mode in ("solo", "group", "lgbt-solo", "lgbt-group"):
        while not creators:
            prompt = "Creator name (0 to go back): " if mode.endswith("solo") else "Creator names, separated by commas (0 to go back): "
            text = input(prompt).strip()
            if text == "0":
                return None
            creators = [n.strip() for n in (text.split(",") if mode.endswith("group") else [text]) if n.strip()]
            if not creators:
                print("Please enter a creator name.")
    return mode, creators


def batch_menu(preferences, mode):
    # Import here so the conversion engine can use the menu without a circular import.
    from streammii import parser, run_batch
    label, category = MODES[mode]
    args = parser().parse_args(["run", str(preferences.source), "--output", str(preferences.library)])
    args.category = category
    args.video_only = mode == "1"
    args.menu_settings = preferences.settings
    args.delete_originals = preferences.delete_originals
    if category == "sports":
        args.sport = sport_menu()
        if args.sport is None:
            return
    elif category == "adult":
        adult = adult_menu()
        if adult is None:
            return
        args.adult_mode, args.creator = adult
    while True:
        print(f"\n{label.upper()}")
        print(f"From: {preferences.source}\nTo:   {preferences.library}")
        if args.title:
            print(f"Title: {args.title}")
        print("1 > Convert and organize\n2 > Preview conversion (no media changes)\n3 > Organize without converting\n4 > Correct title / year / season\n5 > Preview organization only (no media changes)\n0 > Back")
        choice = choose("> ", tuple("012345"))
        if choice == "0":
            return
        if choice == "4":
            title = input("Title (Enter to use detected name): ").strip()
            year = input("Year (Enter to detect): ").strip()
            season = input("Season, 0 for specials (Enter to detect): ").strip()
            try:
                from organizer import OrganizeOptions
                overrides = OrganizeOptions(category, args.sport, args.adult_mode, tuple(args.creator),
                                            title or None, int(year) if year else None, int(season) if season else None)
                args.title, args.year, args.season = overrides.title, overrides.year, overrides.season
            except ValueError as error:
                print(f"Could not use those values: {error}")
            continue
        args.dry_run = choice in ("2", "5")
        args.organize_only = choice in ("3", "5")
        print("\nScanning your media...")
        try:
            run_batch(args)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            print(f"Could not complete this batch: {error}")
        if args.dry_run:
            action = "3 to organize without converting" if args.organize_only else "1 to convert"
            print(f"\nPreview finished. Choose {action}, or 0 to go back.")
        else:
            input("\nPress Enter to return to the main menu.")
            return


def tools_menu(preferences):
    from streammii import doctor, parser
    while True:
        print("\nTOOLS / HELP\n1 > Check FFmpeg and test GPU support\n2 > How to use StreamMii\n0 > Back")
        choice = choose("> ", tuple("012"))
        if choice == "0":
            return
        if choice == "1":
            try:
                # Use the saved executable locations without writing a temporary config.
                from ffmpeg_utils import require_tools
                require_tools(preferences.settings)
                args = parser().parse_args(["doctor", "--test-gpu"])
                args.menu_settings = preferences.settings
                doctor(args)
            except (OSError, ValueError, RuntimeError) as error:
                print(f"Setup check: {error}")
        else:
            print("\nPut media in the input folder shown on the main menu.")
            print("Choose a category, then choose Convert and organize.")
            print("Preview shows the planned folders without converting or moving media.")
            print("Originals are kept unless you change that in Settings.")
            print("Completed files are skipped automatically on the next run.")
            print("Press Ctrl+C during conversion to cancel safely.")
            print(f"Current input: {preferences.source}\nCurrent library: {preferences.library}")
        input("\nPress Enter to continue.")


def menu_loop(preferences_path=PREFERENCES_FILE):
    with purple_console():
        return _menu_loop(preferences_path)


def _menu_loop(preferences_path):
    print(ASCII_ART)
    print(f"\nStreamMii {VERSION}\nMedia made ready for your Wii.")
    try:
        preferences = load_preferences(preferences_path)
    except (ValueError, TypeError, OSError) as error:
        print(f"Saved settings could not be loaded: {error}\nLet's choose them again.")
        preferences = None
    if preferences is None:
        preferences = first_launch(preferences_path)
    elif preferences.encoder_choice_needed:
        print("\nPlease choose your encoder; automatic selection has been removed.")
        preferences.settings = replace(preferences.settings, encoder=encoder_menu())
        preferences.encoder_choice_needed = False
        save_preferences(preferences, preferences_path)
    while True:
        print(f"\nSTREAMMII {VERSION}")
        print(f"Input:   {preferences.source}\nLibrary: {preferences.library}")
        print(f"Encoder: {ENCODERS[preferences.settings.encoder]} | Originals: {'delete after success' if preferences.delete_originals else 'keep'}\n")
        for number, (label, _) in MODES.items():
            print(f"{number} > {label}")
        print("8 > Settings\n9 > Tools / Help\n0 > Exit")
        try:
            choice = choose("> ", tuple("0123456789"))
            if choice == "0":
                print("See you next time!")
                return 0
            if choice == "8":
                settings_menu(preferences, preferences_path)
            elif choice == "9":
                tools_menu(preferences)
            else:
                batch_menu(preferences, choice)
        except KeyboardInterrupt:
            print("\nBack to the main menu.")
        except EOFError:
            print("\nGoodbye.")
            return 0
