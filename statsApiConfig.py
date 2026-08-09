"""Locate the Rocket League install and read its Stats API settings.

The Stats API this app connects to (localhost:49123) is disabled by default.
It is enabled per *install*, not per user:

    <install>\\TAGame\\Config\\DefaultStatsAPI.ini

        [TAGame.MatchStatsExporter_TA]
        Port=49123
        WebPort=49124
        PacketSendRate=1      ; 0 disables the feature entirely

So a refused connection on 49123 has two very different causes — the game
isn't running, or PacketSendRate=0 — and only this file can tell them apart.

Install detection covers Steam and Epic. Every candidate path is confirmed by
looking for DefaultStatsAPI.ini on disk, so a wrong guess from the registry or
a stale launcher manifest is discarded rather than reported.
"""

import json
import os
import re
from pathlib import Path

import psutil

STEAM_APP_ID = "252950"
PROCESS_NAME = "RocketLeague.exe"

CONFIG_SECTION = "TAGame.MatchStatsExporter_TA"

# The game caps the send rate at 120; 0 disables the exporter. One packet per
# second is plenty — the tracker only reads the roster and end-of-match stats
# out of UpdateState, so a higher rate is pure overhead in both processes.
MIN_PACKET_RATE = 1
MAX_PACKET_RATE = 120
RECOMMENDED_PACKET_RATE = 1

CONFIG_RELPATH = Path("TAGame") / "Config" / "DefaultStatsAPI.ini"
EXE_RELPATH = Path("Binaries") / "Win64" / PROCESS_NAME

# UE3 copies Default*.ini into the user config dir on first run. Rocket League
# does this for TAGame.ini, TASystemSettings.ini and friends; a StatsAPI copy
# is not normally generated, but prefer it if one exists rather than reporting
# a value the game may not be using.
USER_CONFIG_RELPATH = (
    Path("My Games") / "Rocket League" / "TAGame" / "Config" / "TAStatsAPI.ini"
)


# --------------------------------------------------------------------------
# Install detection
# --------------------------------------------------------------------------

def find_installs():
    """Return a list of (install_dir, source) for every Rocket League install
    found, most trustworthy first, de-duplicated.

    `source` is one of "process", "epic", "steam" — useful in diagnostics so
    the user can be told which install we actually inspected.
    """
    found = []
    seen = set()

    for path, source in _running_install() + _epic_installs() + _steam_installs():
        if not _is_install_dir(path):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        found.append((path, source))

    return found


def _is_install_dir(path):
    try:
        return (path / CONFIG_RELPATH).is_file()
    except OSError:
        return False


def _running_install():
    """The install backing a currently running RocketLeague.exe.

    The most reliable source by far — it is the copy actually in use, and it
    needs no registry or launcher metadata — but only available while the game
    is open. `exe()` can raise AccessDenied for processes we don't own.
    """
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if proc.info["name"] != PROCESS_NAME:
                continue
            exe = proc.info["exe"]
            if exe:
                # <root>\Binaries\Win64\RocketLeague.exe
                return [(Path(exe).parents[2], "process")]
        except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
            continue
    return []


def _epic_installs():
    """Epic records every install in two places under ProgramData. Read both
    and let _is_install_dir sort out which entries are Rocket League — the
    launcher's own AppName for it ("Sugar") is undocumented and not worth
    depending on.
    """
    data_dir = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data"
    locations = []

    installed = _read_json(data_dir / "LauncherInstalled.dat")
    if isinstance(installed, dict):
        for entry in installed.get("InstallationList") or []:
            if isinstance(entry, dict) and entry.get("InstallLocation"):
                locations.append(entry["InstallLocation"])

    try:
        manifests = sorted((data_dir / "Manifests").glob("*.item"))
    except OSError:
        manifests = []
    for manifest in manifests:
        entry = _read_json(manifest)
        if isinstance(entry, dict) and entry.get("InstallLocation"):
            locations.append(entry["InstallLocation"])

    return [(Path(loc), "epic") for loc in locations]


def _steam_installs():
    """Steam can install to any of several library folders on any drive, so
    the Steam root only gets us to libraryfolders.vdf, which lists the rest.
    """
    steam_root = _steam_root()
    if steam_root is None:
        return []

    installs = []
    for library in _steam_libraries(steam_root):
        manifest = library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
        text = _read_text(manifest)
        if text is None:
            continue
        match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
        if match:
            installs.append((library / "steamapps" / "common" / match.group(1), "steam"))

    return installs


def _steam_root():
    try:
        import winreg
    except ImportError:  # non-Windows; nothing to find
        return None

    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, subkey, value in keys:
        try:
            with winreg.OpenKey(hive, subkey) as handle:
                path = winreg.QueryValueEx(handle, value)[0]
        except OSError:
            continue
        if path:
            return Path(path)  # SteamPath uses forward slashes; Path copes
    return None


def _steam_libraries(steam_root):
    """Every library folder, including the Steam root itself.

    libraryfolders.vdf has had two shapes: the current one nests a "path" key
    per library, the older one mapped a numeric key straight to the path.
    Match both rather than parsing VDF properly.
    """
    libraries = [steam_root]

    text = _read_text(steam_root / "steamapps" / "libraryfolders.vdf")
    if text:
        libraries += [Path(p) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
        libraries += [Path(p) for p in re.findall(r'"\d+"\s+"([a-zA-Z]:\\\\[^"]*)"', text)]

    return libraries


# --------------------------------------------------------------------------
# Config reading
# --------------------------------------------------------------------------

def read_stats_api_config(install_dir):
    """Read PacketSendRate / Port / WebPort for one install.

    Returns None if no config file is readable. Values missing from the file
    come back as None rather than a guessed default — "we couldn't tell" and
    "it's set to 0" need to stay distinguishable.

    Parsed by regex, not configparser: UE3 ini files allow duplicate keys and
    +/./! key prefixes that configparser rejects outright, and we only want
    three integers.
    """
    path = _effective_config_path(install_dir)
    if path is None:
        return None

    text = _read_text(path)
    if text is None:
        return None

    return {
        "path": path,
        "packetSendRate": _read_int(text, "PacketSendRate"),
        "port": _read_int(text, "Port"),
        "webPort": _read_int(text, "WebPort"),
    }


def _effective_config_path(install_dir):
    user_config = _documents_dir() / USER_CONFIG_RELPATH
    if user_config.is_file():
        return user_config

    install_config = Path(install_dir) / CONFIG_RELPATH
    if install_config.is_file():
        return install_config

    return None


def _documents_dir():
    # Documents is commonly redirected into OneDrive, in which case the
    # USERPROFILE copy is left behind and empty.
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        candidate = Path(onedrive) / "Documents"
        if candidate.is_dir():
            return candidate
    return Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Documents"


def _read_int(text, key):
    # ^ per line so "WebPort" can't satisfy a search for "Port".
    match = re.search(rf"^\s*{key}\s*=\s*(-?\d+)", text, re.IGNORECASE | re.MULTILINE)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------
# Config writing
# --------------------------------------------------------------------------

# Bytes, not str: see set_packet_send_rate.
_RATE_RE = re.compile(rb"^([ \t]*PacketSendRate[ \t]*=[ \t]*)(-?\d+)", re.IGNORECASE | re.MULTILINE)
_SECTION_RE = re.compile(
    rb"^\[" + re.escape(CONFIG_SECTION.encode()) + rb"\][ \t]*\r?\n",
    re.IGNORECASE | re.MULTILINE,
)


def set_packet_send_rate(config_path, rate):
    """Point PacketSendRate at `rate`, leaving the rest of the file alone.

    Raises ValueError for a rate outside MIN..MAX, and OSError (usually
    PermissionError — the Epic and Steam installs both live under a
    UAC-protected directory) if the file can't be written.

    Works on **bytes** throughout rather than decoding to str. This is a file
    we don't own: decoding with errors="ignore" would silently drop any byte
    that isn't valid UTF-8, and re-encoding would normalise the BOM and line
    endings. Substituting into the raw bytes changes the digits and nothing
    else. Comments, key order and CRLF all survive untouched.
    """
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise ValueError(f"rate must be an int, got {rate!r}")
    if not MIN_PACKET_RATE <= rate <= MAX_PACKET_RATE:
        raise ValueError(
            f"rate must be between {MIN_PACKET_RATE} and {MAX_PACKET_RATE}, got {rate}"
        )

    path = Path(config_path)
    original = path.read_bytes()

    # Every occurrence, not just the first: a hand-edited file could hold two
    # PacketSendRate lines, and which one UE3 honours isn't worth depending on.
    # Setting them all to the same value is correct either way.
    updated, replaced = _RATE_RE.subn(rb"\g<1>" + str(rate).encode(), original)
    if not replaced:
        updated = _insert_packet_send_rate(original, rate)

    _write_atomic(path, updated)


def _insert_packet_send_rate(original, rate):
    """Add the key when the file has no PacketSendRate line at all.

    Only reachable on a config that's been hand-edited down to nothing —
    normally the key is present and just set to 0.
    """
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    line = f"PacketSendRate={rate}".encode() + newline

    section = _SECTION_RE.search(original)
    if section:
        return original[:section.end()] + line + original[section.end():]

    lead = original if (not original or original.endswith((b"\n", b"\r"))) else original + newline
    return lead + f"[{CONFIG_SECTION}]".encode() + newline + line


def _write_atomic(original_path, data):
    """Write via a sibling temp file so a failure part-way through can't leave
    the user with a truncated game config. A permissions failure hits the temp
    file first, before the real one is touched at all.
    """
    tmp = original_path.with_name(original_path.name + ".rlt-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, original_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def stats_api_status():
    """One dict describing whether the Stats API is usable.

    {
        "found": bool,            # an install was located at all
        "installDir": Path|None,
        "source": str|None,       # "process" | "epic" | "steam"
        "configPath": Path|None,  # the file the user needs to edit
        "packetSendRate": int|None,
        "port": int|None,
        "enabled": bool,          # packetSendRate > 0 and port > 0
        "otherInstalls": [(Path, source), ...],
    }

    `enabled` is False whenever anything is unknown, so callers can trust it
    without None-guarding. Check "found" to distinguish "disabled" from
    "couldn't find your install".
    """
    installs = find_installs()

    status = {
        "found": False,
        "installDir": None,
        "source": None,
        "configPath": None,
        "packetSendRate": None,
        "port": None,
        "enabled": False,
        "otherInstalls": installs[1:],
    }

    for install_dir, source in installs:
        config = read_stats_api_config(install_dir)
        if config is None:
            continue

        status.update({
            "found": True,
            "installDir": install_dir,
            "source": source,
            "configPath": config["path"],
            "packetSendRate": config["packetSendRate"],
            "port": config["port"],
            "enabled": bool(config["packetSendRate"]) and config["packetSendRate"] > 0
                       and bool(config["port"]) and config["port"] > 0,
        })
        break

    return status


# --------------------------------------------------------------------------
# Small IO helpers — every caller treats unreadable as absent
# --------------------------------------------------------------------------

def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return None


def _read_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    result = stats_api_status()
    if not result["found"]:
        print("No Rocket League install found.")
    else:
        print(f"Install ({result['source']}): {result['installDir']}")
        print(f"Config:  {result['configPath']}")
        print(f"Port:    {result['port']}")
        print(f"Rate:    {result['packetSendRate']}")
        print(f"Enabled: {result['enabled']}")
        for path, source in result["otherInstalls"]:
            print(f"Also found ({source}): {path}")
