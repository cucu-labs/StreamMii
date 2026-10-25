from contextlib import contextmanager
import sys

try:
    from colorama import Fore, Style, just_fix_windows_console
except ImportError:
    Fore = Style = None
    just_fix_windows_console = None

ASCII_ART = r"""   _____ __                            __  ____ _
  / ___// /_________  ____ _____ ___  /  |/  (_|_)
  \__ \/ __/ ___/ _ \/ __ `/ __ `__ \/ /|_/ / / /
 ___/ / /_/ /  /  __/ /_/ / / / / / / /  / / / /
/____/\__/_/   \___/\__,_/_/ /_/ /_/_/  /_/_/_/
"""


@contextmanager
def purple_console():
    if just_fix_windows_console is not None:
        just_fix_windows_console()
    enabled = Fore is not None and sys.stdout.isatty()
    if enabled:
        print(Fore.LIGHTMAGENTA_EX, end="", flush=True)
    try:
        yield
    finally:
        if enabled:
            print(Style.RESET_ALL, end="", flush=True)
