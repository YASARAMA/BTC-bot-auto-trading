import hashlib
import json
from pathlib import Path

import pytest

from bot.ui.updater import Updater, Version, read_build_info


def release(tag="v0.9.0-build.42", with_sums=True, names=("BTCBot.exe", "BTCBot-console.exe")):
    assets = [{"name": n, "browser_download_url": f"https://dl/{n}", "size": 10} for n in names]
    if with_sums:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": "https://dl/SHA256SUMS.txt", "size": 1})
    return {"tag_name": tag, "name": f"BTC Bot {tag}", "body": "notes", "published_at": "2026-09-13T12:00:00Z",
            "html_url": f"https://github.com/x/y/releases/{tag}", "assets": assets}


def test_version_parse_and_order():
    assert Version.parse("v0.3.0-build.7") == Version(0, 3, 0, 7)
    assert Version.parse("0.3.0") == Version(0, 3, 0, 0)
    assert Version.parse("nope") is None and Version.parse(None) is None
    assert Version.parse("v0.3.0-build.8").key > Version.parse("v0.3.0-build.7").key
    assert Version.parse("v0.4.0").key > Version.parse("v0.3.9-build.99").key
    assert str(Version(0, 3, 0, 7)) == "v0.3.0 build 7" and "dev" in str(Version(0, 3, 0))


def test_build_info_fallback(tmp_path):
    info = read_build_info(tmp_path, "0.4.0")
    assert info == {"version": "0.4.0", "build": 0, "commit": None, "built_at": None}
    p = tmp_path / "bot" / "ui" / "build_info.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"version": "0.4.0", "build": 12, "commit": "abc", "built_at": "t"}))
    assert read_build_info(tmp_path, "0.4.0")["build"] == 12


def make_updater(tmp_path, current="v0.4.0-build.5", frozen=True, data=None, files=None, events=None):
    exe = tmp_path / "BTCBot.exe"
    exe.write_bytes(b"OLD-EXE")
    files = files or {}
    downloads = []

    def fetch_json(url, token):
        assert url.endswith("/releases/latest")
        if isinstance(data, Exception):
            raise data
        return data

    def download(url, dest, token, progress=None):
        downloads.append(url)
        name = url.rsplit("/", 1)[-1]
        dest.write_bytes(files[name])
        if progress:
            progress(len(files[name]), len(files[name]))
        return dest

    restarted = []
    u = Updater(repo="x/y", current=Version.parse(current), app_dir=tmp_path, exe_path=exe, frozen=frozen,
                fetch_json=fetch_json, download=download, restart=lambda p: restarted.append(p),
                on_event=(events.append if events is not None else None))
    return u, exe, downloads, restarted


def test_check_reports_newer_release_once(tmp_path):
    events = []
    u, exe, _, _ = make_updater(tmp_path, data=release(), events=events)
    st = u.check()
    assert st["available"] and st["latest"]["tag"] == "v0.9.0-build.42" and st["error"] is None and st["can_install"]
    u.check()
    assert len(events) == 1 and events[0]["event"] == "update_available"
    older, *_ = make_updater(tmp_path, current="v1.0.0-build.1", data=release())
    assert older.check()["available"] is False


def test_check_handles_errors(tmp_path):
    u, *_ = make_updater(tmp_path, data=RuntimeError("HTTP 404"))
    st = u.check()
    assert st["available"] is False and "404" in st["error"] and st["state"] == "error"
    u2, *_ = make_updater(tmp_path, data={"tag_name": "weird", "assets": []})
    assert "not vX.Y.Z" in u2.check()["error"]


def test_install_verifies_checksum_swaps_exe_and_restarts(tmp_path):
    new = b"NEW-EXE-BYTES"
    sums = f"{hashlib.sha256(new).hexdigest()}  BTCBot.exe\n"
    files = {"BTCBot.exe": new, "SHA256SUMS.txt": sums.encode()}
    events = []
    u, exe, downloads, restarted = make_updater(tmp_path, data=release(names=("BTCBot.exe",)), files=files, events=events)
    calls = []
    out = u.install(before_restart=lambda: calls.append("stop"))
    assert out["installed"] and out["tag"] == "v0.9.0-build.42"
    assert exe.read_bytes() == new
    assert (tmp_path / "BTCBot.old.exe").read_bytes() == b"OLD-EXE"
    assert restarted == [exe] and calls == ["stop"] and u.state == "restarting"
    assert any(e["event"] == "update_installed" for e in events)
    u.cleanup_old()
    assert not (tmp_path / "BTCBot.old.exe").exists()


def test_install_refuses_bad_checksum_and_keeps_exe(tmp_path):
    files = {"BTCBot.exe": b"NEW", "SHA256SUMS.txt": b"deadbeef  BTCBot.exe\n"}
    u, exe, _, restarted = make_updater(tmp_path, data=release(names=("BTCBot.exe",)), files=files)
    with pytest.raises(ValueError, match="checksum mismatch"):
        u.install()
    assert exe.read_bytes() == b"OLD-EXE" and restarted == [] and u.state == "error"


def test_install_updates_console_sibling_too(tmp_path):
    files = {"BTCBot.exe": b"A", "BTCBot-console.exe": b"B",
             "SHA256SUMS.txt": (f"{hashlib.sha256(b'A').hexdigest()}  BTCBot.exe\n{hashlib.sha256(b'B').hexdigest()}  BTCBot-console.exe\n").encode()}
    u, exe, downloads, restarted = make_updater(tmp_path, data=release(), files=files)
    (tmp_path / "BTCBot-console.exe").write_bytes(b"old-console")
    u.install()
    assert exe.read_bytes() == b"A" and (tmp_path / "BTCBot-console.exe").read_bytes() == b"B"
    assert (tmp_path / "BTCBot-console.old.exe").exists()


def test_install_refused_when_not_frozen_or_not_newer(tmp_path):
    u, *_ = make_updater(tmp_path, frozen=False, data=release())
    with pytest.raises(RuntimeError, match="packaged app"):
        u.install()
    u2, *_ = make_updater(tmp_path, current="v9.0.0", data=release())
    with pytest.raises(RuntimeError, match="no newer"):
        u2.install()
