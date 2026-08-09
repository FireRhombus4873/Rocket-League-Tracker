"""
Tier 2 — Rocket League install detection and the Stats API config file.

Two halves, both pure logic with no Qt:

*Writing* is where the risk is. `set_packet_send_rate` edits a file the app
doesn't own, in the user's game install, so the tests lean on byte-level
assertions rather than parsed values — a round-trip that changes the rate and
puts it back must reproduce the original file *exactly*, and a non-UTF-8 byte
or a BOM must survive untouched. That's the whole reason the function works on
bytes, and it's not observable from a decoded comparison.

*Detection* fans out over Steam and Epic metadata. Every source is faked inside
tmp_path: the Epic side by pointing %PROGRAMDATA% at a staged tree, Steam by
stubbing `_steam_root` (the real one reads the registry), and the live-process
source by stubbing `psutil.process_iter`. Nothing here touches the real machine
— including `_documents_dir`, which an autouse fixture redirects so a
`TAStatsAPI.ini` in the developer's own Documents can't change a result.
"""
import codecs
import json

import pytest

import statsApiConfig as sac


# The real file, verbatim: CRLF, three comments, and no trailing newline.
REAL_CONFIG = (
    b"[TAGame.MatchStatsExporter_TA]\r\n"
    b"\r\n"
    b"; Port the client will listen for tcp connections on (must be different than WebPort, set to 0 to disable)\r\n"
    b"Port=49123\r\n"
    b"\r\n"
    b"; Port the client will listen for web connections on (must be different than Port, set to 0 to disable)\r\n"
    b"WebPort=49124\r\n"
    b"\r\n"
    b"; How many times per second the game sends the update state (capped at 120, 0 disables this feature)\r\n"
    b"PacketSendRate=1"
)
DISABLED_CONFIG = REAL_CONFIG.replace(b"PacketSendRate=1", b"PacketSendRate=0")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_documents(tmp_path, monkeypatch):
    """Point `_documents_dir` at an empty directory.

    `_effective_config_path` prefers a user-level TAStatsAPI.ini over the one in
    the install. Without this, a developer who happens to have that file would
    get different results from everyone else.
    """
    empty = tmp_path / "documents"
    empty.mkdir()
    monkeypatch.setattr(sac, "_documents_dir", lambda: empty)
    return empty


@pytest.fixture
def install(tmp_path):
    """A minimal but valid install tree, with the API switched off."""
    return _make_install(tmp_path / "epic" / "rocketleague", DISABLED_CONFIG)


def _make_install(root, config=DISABLED_CONFIG):
    config_file = root / sac.CONFIG_RELPATH
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_bytes(config)
    return root


def _config_of(root):
    return root / sac.CONFIG_RELPATH


class _FakeProc:
    """Stand-in for a psutil.Process as `process_iter(["name", "exe"])` yields.

    `__getattr__` refuses everything else so the test fails loudly if
    `_running_install` ever starts depending on more of the psutil surface than
    the two attrs it asks for.
    """

    def __init__(self, name, exe):
        self.info = {"name": name, "exe": exe}

    def __getattr__(self, item):
        raise AttributeError(item)


class _DeniedProc:
    """A process psutil won't let us inspect — common for other users' processes
    and for anything at all during system boot."""

    @property
    def info(self):
        raise sac.psutil.AccessDenied()


# ==========================================================================
# set_packet_send_rate — writing
# ==========================================================================

def test_set_rate_replaces_the_value(install):
    sac.set_packet_send_rate(_config_of(install), 1)
    assert b"PacketSendRate=1" in _config_of(install).read_bytes()


def test_set_rate_round_trip_is_byte_identical(install):
    """0 -> 1 must reproduce the shipped file exactly, not merely equivalently.

    The strongest available guarantee that nothing outside the digits moved.
    """
    sac.set_packet_send_rate(_config_of(install), 1)
    assert _config_of(install).read_bytes() == REAL_CONFIG


def test_set_rate_preserves_crlf_comments_and_other_keys(install):
    sac.set_packet_send_rate(_config_of(install), 120)
    out = _config_of(install).read_bytes()

    assert out.count(b"\r\n") == DISABLED_CONFIG.count(b"\r\n")
    assert out.count(b";") == DISABLED_CONFIG.count(b";")
    assert b"Port=49123" in out and b"WebPort=49124" in out
    assert not out.endswith(b"\n")  # no trailing newline invented
    # The only difference is the digits themselves.
    assert out.replace(b"PacketSendRate=120", b"PacketSendRate=0") == DISABLED_CONFIG


def test_set_rate_preserves_bom_and_undecodable_bytes(tmp_path):
    """The reason the function works on bytes.

    Decoding with errors="ignore" and re-encoding would drop the 0xFF and
    normalise the BOM away. Neither is visible in a parsed comparison.
    """
    weird = codecs.BOM_UTF8 + DISABLED_CONFIG.replace(b"; Port", b"; \xffPort")
    root = _make_install(tmp_path / "weird", weird)

    sac.set_packet_send_rate(_config_of(root), 4)
    out = _config_of(root).read_bytes()

    assert out.startswith(codecs.BOM_UTF8)
    assert b"\xff" in out
    assert out == weird.replace(b"PacketSendRate=0", b"PacketSendRate=4")


@pytest.mark.parametrize("rate", [1, 2, 30, 120])
def test_set_rate_accepts_the_whole_valid_range(install, rate):
    sac.set_packet_send_rate(_config_of(install), rate)
    cfg = sac.read_stats_api_config(install)
    assert cfg["packetSendRate"] == rate


def test_set_rate_updates_every_occurrence(tmp_path):
    """A hand-edited file could hold two of these. Which one UE3 honours isn't
    worth depending on, so both must end up at the requested value."""
    doubled = DISABLED_CONFIG + b"\r\nPacketSendRate=99\r\n"
    root = _make_install(tmp_path / "doubled", doubled)

    sac.set_packet_send_rate(_config_of(root), 7)
    out = _config_of(root).read_bytes()

    assert out.count(b"PacketSendRate=7") == 2
    assert b"PacketSendRate=99" not in out
    assert b"PacketSendRate=0" not in out


@pytest.mark.parametrize("case_variant", [
    b"packetsendrate=0",
    b"PACKETSENDRATE=0",
    b"PacketSendRate = 0",
    b"\tPacketSendRate=0",
])
def test_set_rate_matches_regardless_of_case_and_spacing(tmp_path, case_variant):
    root = _make_install(tmp_path / "variant", b"[TAGame.MatchStatsExporter_TA]\r\n" + case_variant)
    sac.set_packet_send_rate(_config_of(root), 5)
    assert sac.read_stats_api_config(root)["packetSendRate"] == 5


def test_set_rate_replaces_a_negative_value(tmp_path):
    """A negative value is still a value to replace, not a parse failure."""
    root = _make_install(tmp_path / "neg", b"[TAGame.MatchStatsExporter_TA]\r\nPacketSendRate=-1\r\n")
    sac.set_packet_send_rate(_config_of(root), 1)
    assert sac.read_stats_api_config(root)["packetSendRate"] == 1


# ── Insertion: the key isn't there at all ───────────────────────────────────

def test_set_rate_inserts_under_the_section_header(tmp_path):
    stripped = DISABLED_CONFIG.replace(b"\r\nPacketSendRate=0", b"")
    root = _make_install(tmp_path / "nokey", stripped)

    sac.set_packet_send_rate(_config_of(root), 1)
    out = _config_of(root).read_bytes()

    assert out.startswith(b"[TAGame.MatchStatsExporter_TA]\r\nPacketSendRate=1\r\n")
    assert b"WebPort=49124" in out  # the rest of the file survived
    assert sac.read_stats_api_config(root)["packetSendRate"] == 1


def test_set_rate_writes_section_and_key_into_an_empty_file(tmp_path):
    root = _make_install(tmp_path / "empty", b"")
    sac.set_packet_send_rate(_config_of(root), 5)
    assert _config_of(root).read_bytes() == b"[TAGame.MatchStatsExporter_TA]\nPacketSendRate=5\n"


def test_set_rate_appends_section_when_header_is_missing(tmp_path):
    root = _make_install(tmp_path / "nosection", b"; stray comment\r\n")
    sac.set_packet_send_rate(_config_of(root), 3)
    out = _config_of(root).read_bytes()

    assert out.startswith(b"; stray comment\r\n")
    assert out.endswith(b"[TAGame.MatchStatsExporter_TA]\r\nPacketSendRate=3\r\n")


def test_set_rate_adds_a_newline_before_appending_to_an_unterminated_file(tmp_path):
    root = _make_install(tmp_path / "unterminated", b"; no trailing newline")
    sac.set_packet_send_rate(_config_of(root), 3)
    out = _config_of(root).read_bytes()

    assert out.startswith(b"; no trailing newline\n[")
    assert sac.read_stats_api_config(root)["packetSendRate"] == 3


# ── Validation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [0, -1, 121, 1000])
def test_set_rate_rejects_out_of_range(install, rate):
    with pytest.raises(ValueError, match="between 1 and 120"):
        sac.set_packet_send_rate(_config_of(install), rate)


@pytest.mark.parametrize("rate", [True, False, "1", 1.0, None, [1]])
def test_set_rate_rejects_non_int(install, rate):
    # bool is an int subclass, so it needs rejecting explicitly — True would
    # otherwise sail through the range check and write "PacketSendRate=True".
    with pytest.raises(ValueError, match="must be an int"):
        sac.set_packet_send_rate(_config_of(install), rate)


@pytest.mark.parametrize("rate", [121, -5, "9", None])
def test_set_rate_validates_before_touching_the_file(install, rate):
    """A rejected rate must not reach the user's config.

    The rates here are all ones that would be *visible* in the file if the
    write went ahead. Testing this with 0 proves nothing: the fixture already
    reads PacketSendRate=0, so a stray write is byte-identical to no write.
    """
    with pytest.raises(ValueError):
        sac.set_packet_send_rate(_config_of(install), rate)
    assert _config_of(install).read_bytes() == DISABLED_CONFIG


def test_set_rate_propagates_write_failures_without_damaging_the_original(install, monkeypatch):
    """Both stores install under a UAC-protected directory, so this is the
    expected path for an unelevated user, not an exotic one."""
    def boom(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sac.os, "replace", boom)

    with pytest.raises(PermissionError):
        sac.set_packet_send_rate(_config_of(install), 1)

    assert _config_of(install).read_bytes() == DISABLED_CONFIG


def test_set_rate_cleans_up_its_temp_file_on_failure(install, monkeypatch):
    monkeypatch.setattr(sac.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("nope")))

    with pytest.raises(OSError):
        sac.set_packet_send_rate(_config_of(install), 1)

    leftovers = list(_config_of(install).parent.glob("*.rlt-tmp"))
    assert leftovers == []


# ==========================================================================
# read_stats_api_config — reading
# ==========================================================================

def test_read_config_reads_all_three_values(tmp_path):
    root = _make_install(tmp_path / "read", REAL_CONFIG)
    cfg = sac.read_stats_api_config(root)

    assert cfg["packetSendRate"] == 1
    assert cfg["port"] == 49123
    assert cfg["webPort"] == 49124
    assert cfg["path"] == _config_of(root)


def test_read_config_does_not_mistake_webport_for_port(tmp_path):
    """Regression: an unanchored search for "Port" matches the WebPort line.
    Here WebPort comes first, so a broken pattern reports port 49124."""
    reordered = (
        b"[TAGame.MatchStatsExporter_TA]\r\n"
        b"WebPort=49124\r\n"
        b"Port=49123\r\n"
        b"PacketSendRate=1\r\n"
    )
    cfg = sac.read_stats_api_config(_make_install(tmp_path / "order", reordered))
    assert cfg["port"] == 49123
    assert cfg["webPort"] == 49124


def test_read_config_reports_missing_keys_as_none(tmp_path):
    """None and 0 must stay distinguishable: "we couldn't tell" is not "off"."""
    root = _make_install(tmp_path / "sparse", b"[TAGame.MatchStatsExporter_TA]\r\nPort=49123\r\n")
    cfg = sac.read_stats_api_config(root)

    assert cfg["packetSendRate"] is None
    assert cfg["webPort"] is None
    assert cfg["port"] == 49123


def test_read_config_returns_none_when_there_is_no_file(tmp_path):
    assert sac.read_stats_api_config(tmp_path / "nothing-here") is None


def test_read_config_survives_undecodable_bytes(tmp_path):
    root = _make_install(tmp_path / "bad-bytes", REAL_CONFIG.replace(b"; Port", b"; \xffPort"))
    assert sac.read_stats_api_config(root)["packetSendRate"] == 1


def test_read_config_prefers_a_user_level_override(tmp_path, monkeypatch):
    """If the game ever generates its own copy under Documents, that's the file
    it reads — reporting the install copy's value would be a lie."""
    documents = tmp_path / "docs"
    override = documents / sac.USER_CONFIG_RELPATH
    override.parent.mkdir(parents=True)
    override.write_bytes(b"[TAGame.MatchStatsExporter_TA]\r\nPort=49123\r\nPacketSendRate=42\r\n")
    monkeypatch.setattr(sac, "_documents_dir", lambda: documents)

    root = _make_install(tmp_path / "install", REAL_CONFIG)  # says rate=1
    cfg = sac.read_stats_api_config(root)

    assert cfg["packetSendRate"] == 42
    assert cfg["path"] == override


def test_writes_target_the_same_file_reads_report(tmp_path, monkeypatch):
    """Whatever `read_stats_api_config` calls authoritative is what a write must
    edit, or the dialog would 'succeed' and change nothing."""
    documents = tmp_path / "docs"
    override = documents / sac.USER_CONFIG_RELPATH
    override.parent.mkdir(parents=True)
    override.write_bytes(b"[TAGame.MatchStatsExporter_TA]\r\nPort=49123\r\nPacketSendRate=0\r\n")
    monkeypatch.setattr(sac, "_documents_dir", lambda: documents)

    root = _make_install(tmp_path / "install", REAL_CONFIG)
    sac.set_packet_send_rate(sac.read_stats_api_config(root)["path"], 9)

    assert b"PacketSendRate=9" in override.read_bytes()
    assert _config_of(root).read_bytes() == REAL_CONFIG  # install copy untouched


# ==========================================================================
# stats_api_status — the public entry point
# ==========================================================================

@pytest.fixture
def only_epic(tmp_path, monkeypatch):
    """Make Epic the sole install source, with a staged ProgramData tree."""
    program_data = tmp_path / "ProgramData"
    data = program_data / "Epic" / "EpicGamesLauncher" / "Data"
    data.mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(sac, "_steam_root", lambda: None)
    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([]))
    return data


def _stage_epic(data, *roots):
    data.joinpath("LauncherInstalled.dat").write_text(json.dumps({
        "InstallationList": [
            {"InstallLocation": str(r), "AppName": "Sugar"} for r in roots
        ]
    }))


def test_status_reports_enabled_when_the_api_is_on(tmp_path, only_epic):
    root = _make_install(tmp_path / "rl", REAL_CONFIG)
    _stage_epic(only_epic, root)

    status = sac.stats_api_status()

    assert status["found"] is True
    assert status["enabled"] is True
    assert status["installDir"] == root
    assert status["source"] == "epic"
    assert status["configPath"] == _config_of(root)
    assert status["packetSendRate"] == 1
    assert status["port"] == 49123


@pytest.mark.parametrize("config, reason", [
    (DISABLED_CONFIG, "rate is 0"),
    (REAL_CONFIG.replace(b"Port=49123", b"Port=0"), "tcp port disabled"),
    (b"[TAGame.MatchStatsExporter_TA]\r\nPort=49123\r\n", "rate key absent"),
    (b"[TAGame.MatchStatsExporter_TA]\r\nPacketSendRate=1\r\n", "port key absent"),
])
def test_status_is_not_enabled_when(tmp_path, only_epic, config, reason):
    root = _make_install(tmp_path / "rl", config)
    _stage_epic(only_epic, root)

    status = sac.stats_api_status()

    assert status["found"] is True, reason
    assert status["enabled"] is False, reason
    # Still hands back the file to edit — the dialog needs it.
    assert status["configPath"] == _config_of(root)


def test_status_when_nothing_is_installed(only_epic):
    status = sac.stats_api_status()

    assert status["found"] is False
    assert status["enabled"] is False
    assert status["installDir"] is None
    assert status["configPath"] is None
    assert status["otherInstalls"] == []


def test_status_never_returns_a_none_enabled(only_epic, tmp_path):
    """`enabled` is consumed as a plain bool by main.py with no None-guard."""
    _stage_epic(only_epic, _make_install(tmp_path / "rl", b""))
    assert sac.stats_api_status()["enabled"] is False


def test_status_lists_additional_installs(tmp_path, only_epic):
    first = _make_install(tmp_path / "a" / "rocketleague", REAL_CONFIG)
    second = _make_install(tmp_path / "b" / "rocketleague", REAL_CONFIG)
    _stage_epic(only_epic, first, second)

    status = sac.stats_api_status()

    assert status["installDir"] == first
    assert status["otherInstalls"] == [(second, "epic")]


# ==========================================================================
# Install detection
# ==========================================================================

def test_is_install_dir_requires_the_config_file(tmp_path):
    assert sac._is_install_dir(_make_install(tmp_path / "good")) is True
    assert sac._is_install_dir(tmp_path / "missing") is False

    bare = tmp_path / "bare"
    bare.mkdir()
    assert sac._is_install_dir(bare) is False


# ── Epic ───────────────────────────────────────────────────────────────────

def test_epic_reads_both_launcher_dat_and_item_manifests(tmp_path, only_epic):
    from_dat = _make_install(tmp_path / "dat" / "rocketleague")
    from_item = _make_install(tmp_path / "item" / "rocketleague")

    _stage_epic(only_epic, from_dat)
    manifests = only_epic / "Manifests"
    manifests.mkdir()
    manifests.joinpath("abc.item").write_text(json.dumps({
        "InstallLocation": str(from_item),
        "LaunchExecutable": r"Binaries\Win64\RocketLeague.exe",
        "AppName": "Sugar",
        "DisplayName": "Rocket League",
    }))

    assert sac.find_installs() == [(from_dat, "epic"), (from_item, "epic")]


def test_epic_discards_entries_that_are_not_rocket_league(tmp_path, only_epic):
    """Nothing matches on AppName, so Fortnite must be filtered by the absence
    of DefaultStatsAPI.ini rather than by its name."""
    fortnite = tmp_path / "Fortnite"
    fortnite.mkdir()
    rl = _make_install(tmp_path / "rocketleague")
    _stage_epic(only_epic, fortnite, rl)

    assert sac.find_installs() == [(rl, "epic")]


@pytest.mark.parametrize("payload", [
    "not json at all",
    "{}",
    '{"InstallationList": null}',
    '{"InstallationList": [{"AppName": "Sugar"}]}',   # no InstallLocation
    '{"InstallationList": ["a string, not a dict"]}',
    '["a list, not an object"]',
])
def test_epic_tolerates_malformed_metadata(only_epic, payload):
    only_epic.joinpath("LauncherInstalled.dat").write_text(payload)
    assert sac._epic_installs() == []


def test_epic_absent_entirely(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "does-not-exist"))
    assert sac._epic_installs() == []


# ── Steam ──────────────────────────────────────────────────────────────────

@pytest.fixture
def steam_root(tmp_path, monkeypatch):
    """A Steam root with no libraries configured yet. The real `_steam_root`
    reads the registry, so it's stubbed rather than exercised."""
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    monkeypatch.setattr(sac, "_steam_root", lambda: root)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "no-epic"))
    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([]))
    return root


def _stage_steam_app(library, installdir="rocketleague"):
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    steamapps.joinpath(f"appmanifest_{sac.STEAM_APP_ID}.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"252950"\n\t"installdir"\t\t"%s"\n}\n' % installdir
    )
    return _make_install(steamapps / "common" / installdir)


def test_steam_finds_the_app_in_the_default_library(steam_root):
    expected = _stage_steam_app(steam_root)
    assert sac.find_installs() == [(expected, "steam")]


def test_steam_follows_libraryfolders_to_another_drive(tmp_path, steam_root):
    other = tmp_path / "SteamLibrary"
    expected = _stage_steam_app(other)
    steam_root.joinpath("steamapps", "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n'
        '\t\t\t"252950"\t\t"1234"\n\t\t}\n\t}\n}\n' % str(other).replace("\\", "\\\\")
    )

    assert sac.find_installs() == [(expected, "steam")]


def test_steam_reads_the_legacy_libraryfolders_shape(tmp_path, steam_root):
    """Pre-2021 Steam mapped a numeric key straight to the path."""
    other = tmp_path / "OldLibrary"
    expected = _stage_steam_app(other)
    steam_root.joinpath("steamapps", "libraryfolders.vdf").write_text(
        '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t\t"x"\n\t"1"\t\t"%s"\n}\n'
        % str(other).replace("\\", "\\\\")
    )

    assert sac.find_installs() == [(expected, "steam")]


def test_steam_ignores_a_library_without_the_app(steam_root):
    steam_root.joinpath("steamapps", "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"Z:\\\\gone"\n\t}\n}\n'
    )
    assert sac.find_installs() == []


def test_steam_discards_a_manifest_pointing_at_a_deleted_install(steam_root):
    """Steam leaves the .acf behind if the folder is removed by hand."""
    steamapps = steam_root / "steamapps"
    steamapps.joinpath(f"appmanifest_{sac.STEAM_APP_ID}.acf").write_text(
        '"AppState"\n{\n\t"installdir"\t\t"rocketleague"\n}\n'
    )
    assert sac.find_installs() == []


def test_steam_absent_entirely(tmp_path, monkeypatch):
    monkeypatch.setattr(sac, "_steam_root", lambda: None)
    assert sac._steam_installs() == []


# ── Running process ────────────────────────────────────────────────────────

def test_running_process_resolves_the_install_root(tmp_path, monkeypatch):
    root = _make_install(tmp_path / "live" / "rocketleague")
    exe = root / sac.EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([
        _FakeProc("explorer.exe", r"C:\Windows\explorer.exe"),
        _FakeProc(sac.PROCESS_NAME, str(exe)),
    ]))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "no-epic"))
    monkeypatch.setattr(sac, "_steam_root", lambda: None)

    assert sac.find_installs() == [(root, "process")]


@pytest.mark.parametrize("procs", [
    [],
    [_FakeProc("chrome.exe", r"C:\chrome.exe")],
    [_FakeProc(sac.PROCESS_NAME, None)],  # exe() unavailable
    [_FakeProc(sac.PROCESS_NAME, "RocketLeague.exe")],  # too shallow for parents[2]
])
def test_running_process_yields_nothing_useful(monkeypatch, procs):
    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter(procs))
    assert sac._running_install() == []


def test_running_process_survives_access_denied(tmp_path, monkeypatch):
    """psutil raises for processes we don't own — one bad entry must not abort
    the scan, since it also runs during boot when psutil is flaky."""
    root = _make_install(tmp_path / "live" / "rocketleague")
    exe = root / sac.EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([
        _DeniedProc(),
        _FakeProc(sac.PROCESS_NAME, str(exe)),
    ]))
    assert sac._running_install() == [(root, "process")]


# ── find_installs: ordering and de-duplication ─────────────────────────────

def test_find_installs_orders_process_then_epic_then_steam(tmp_path, monkeypatch):
    live = _make_install(tmp_path / "live" / "rocketleague")
    exe = live / sac.EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([
        _FakeProc(sac.PROCESS_NAME, str(exe)),
    ]))

    program_data = tmp_path / "ProgramData"
    data = program_data / "Epic" / "EpicGamesLauncher" / "Data"
    data.mkdir(parents=True)
    epic = _make_install(tmp_path / "epic" / "rocketleague")
    _stage_epic(data, epic)
    monkeypatch.setenv("PROGRAMDATA", str(program_data))

    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    steam_install = _stage_steam_app(steam)
    monkeypatch.setattr(sac, "_steam_root", lambda: steam)

    assert sac.find_installs() == [
        (live, "process"),
        (epic, "epic"),
        (steam_install, "steam"),
    ]


def test_find_installs_deduplicates_one_install_seen_twice(tmp_path, monkeypatch):
    """The common real case: the game is running *and* Epic lists it. It must
    appear once, credited to the more trustworthy source."""
    root = _make_install(tmp_path / "shared" / "rocketleague")
    exe = root / sac.EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([
        _FakeProc(sac.PROCESS_NAME, str(exe)),
    ]))
    program_data = tmp_path / "ProgramData"
    data = program_data / "Epic" / "EpicGamesLauncher" / "Data"
    data.mkdir(parents=True)
    _stage_epic(data, root, root)  # listed twice by Epic, too
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(sac, "_steam_root", lambda: None)

    assert sac.find_installs() == [(root, "process")]


def test_find_installs_deduplicates_case_insensitively(tmp_path, monkeypatch, only_epic):
    """Windows paths are case-insensitive, and the two sources disagree on
    casing often enough to matter."""
    root = _make_install(tmp_path / "rocketleague")
    _stage_epic(only_epic, root, str(root).upper())

    assert sac.find_installs() == [(root, "epic")]


def test_find_installs_returns_empty_when_every_source_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "nope"))
    monkeypatch.setattr(sac, "_steam_root", lambda: None)
    monkeypatch.setattr(sac.psutil, "process_iter", lambda attrs=None: iter([]))
    assert sac.find_installs() == []


# ==========================================================================
# Constants the UI depends on
# ==========================================================================

def test_rate_bounds_are_sane():
    """StatsApiDialog takes these as its spinbox range and default, with no
    fallbacks of its own — the recommended value has to sit inside the range."""
    assert sac.MIN_PACKET_RATE == 1
    assert sac.MAX_PACKET_RATE == 120  # the game's own cap
    assert sac.MIN_PACKET_RATE <= sac.RECOMMENDED_PACKET_RATE <= sac.MAX_PACKET_RATE
