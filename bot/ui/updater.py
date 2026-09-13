"""Self-update from GitHub Releases.

Every CI build of the branch publishes a release tagged vX.Y.Z-build.N with BTCBot.exe,
BTCBot-console.exe and SHA256SUMS.txt. The running app compares its own build info with
the latest release, downloads the matching executable, verifies its SHA-256, swaps it in
next to itself (a running Windows exe can be renamed but not overwritten) and restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bot.common import now_ms

log = logging.getLogger("bot.ui.updater")

TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-build\.(\d+))?$")
USER_AGENT = "BTCBot-updater"
BUILD_INFO_NAME = "build_info.json"


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    build: int = 0

    @classmethod
    def parse(cls, text: str | None) -> "Version | None":
        if not text:
            return None
        m = TAG_RE.match(text.strip())
        if not m:
            return None
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))

    @property
    def key(self) -> tuple[int, int, int, int]:
        return (self.major, self.minor, self.patch, self.build)

    @property
    def semver(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __str__(self) -> str:
        return f"v{self.semver}" + (f" build {self.build}" if self.build else " (dev)")


def read_build_info(bundle_dir: Path, fallback_version: str) -> dict[str, Any]:
    """Build metadata written by CI into bot/ui/build_info.json; dev runs get build 0."""
    path = bundle_dir / "bot" / "ui" / BUILD_INFO_NAME
    info: dict[str, Any] = {"version": fallback_version, "build": 0, "commit": None, "built_at": None}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            info.update({k: data.get(k, info[k]) for k in info})
    except Exception as exc:  # noqa: BLE001
        log.warning("build info unreadable: %s", exc)
    return info


def _default_fetch_json(url: str, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_download(url: str, dest: Path, token: str | None, progress: Callable[[int, int], None] | None = None) -> Path:
    headers = {"Accept": "application/octet-stream", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    part.replace(dest)
    return dest


def _default_restart(exe: Path) -> None:
    kwargs: dict[str, Any] = {"cwd": str(exe.parent), "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(exe)], **kwargs)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Updater:
    def __init__(
        self,
        *,
        repo: str,
        current: Version,
        app_dir: Path,
        exe_path: Path | None,
        frozen: bool,
        fetch_json: Callable[[str, str | None], Any] | None = None,
        download: Callable[..., Path] | None = None,
        restart: Callable[[Path], None] | None = None,
        token_getter: Callable[[], str | None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.repo = repo
        self.current = current
        self.app_dir = Path(app_dir)
        self.exe_path = Path(exe_path) if exe_path else None
        self.frozen = frozen
        self.fetch_json = fetch_json or _default_fetch_json
        self.download = download or _default_download
        self.restart = restart or _default_restart
        self.token_getter = token_getter or (lambda: None)
        self.on_event = on_event
        self.latest: dict[str, Any] | None = None
        self.error: str | None = None
        self.checked_at: int | None = None
        self.state = "idle"  # idle | checking | downloading | installing | restarting | error
        self.progress: tuple[int, int] = (0, 0)
        self.notified_tag: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ----- status -----------------------------------------------------------------------
    @property
    def can_install(self) -> bool:
        return self.frozen and self.exe_path is not None and self.exe_path.exists()

    @property
    def available(self) -> bool:
        if not self.latest:
            return False
        latest = Version.parse(self.latest.get("tag"))
        return bool(latest and latest.key > self.current.key)

    def status(self) -> dict[str, Any]:
        return {
            "current": {"version": self.current.semver, "build": self.current.build, "label": str(self.current)},
            "latest": self.latest,
            "available": self.available,
            "can_install": self.can_install,
            "state": self.state,
            "progress": {"done": self.progress[0], "total": self.progress[1]},
            "checked_at": self.checked_at,
            "error": self.error,
            "repo": self.repo,
        }

    # ----- check ------------------------------------------------------------------------
    def check(self) -> dict[str, Any]:
        self.state = "checking"
        try:
            data = self.fetch_json(f"https://api.github.com/repos/{self.repo}/releases/latest", self.token_getter())
            tag = data.get("tag_name")
            version = Version.parse(tag)
            if version is None:
                raise ValueError(f"release tag {tag!r} is not vX.Y.Z-build.N")
            assets = {a["name"]: a for a in data.get("assets", []) if a.get("name") and a.get("browser_download_url")}
            self.latest = {
                "tag": tag, "version": version.semver, "build": version.build, "label": str(version),
                "name": data.get("name"), "notes": (data.get("body") or "")[:2000],
                "published_at": data.get("published_at"), "url": data.get("html_url"),
                "assets": {n: {"url": a["browser_download_url"], "size": a.get("size")} for n, a in assets.items()},
            }
            self.error = None
            if self.available and self.notified_tag != tag and self.on_event:
                self.notified_tag = tag
                self.on_event({"event": "update_available", "latest": self.latest, "current": str(self.current)})
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("update check failed: %s", self.error)
        finally:
            self.checked_at = now_ms()
            self.state = "error" if self.error else "idle"
        return self.status()

    # ----- install ----------------------------------------------------------------------
    def _verify(self, path: Path, sums_text: str | None) -> None:
        if not sums_text:
            log.warning("release has no SHA256SUMS.txt; installing %s unverified", path.name)
            return
        expected = None
        for line in sums_text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == path.name:
                expected = parts[0].lower()
        if expected is None:
            raise ValueError(f"{path.name} missing from SHA256SUMS.txt")
        actual = sha256_of(path)
        if actual != expected:
            raise ValueError(f"checksum mismatch for {path.name}: expected {expected[:12]}…, got {actual[:12]}…")

    def install(self, before_restart: Callable[[], None] | None = None) -> dict[str, Any]:
        """Download, verify and swap the running executable, then restart. Raises on failure."""
        with self._lock:
            if not self.latest:
                self.check()
            if not self.available:
                raise RuntimeError("no newer version is available")
            if not self.can_install:
                raise RuntimeError("self-update only works in the packaged app; run 'git pull' when running from source")
            assets = self.latest["assets"]
            exe = self.exe_path
            assert exe is not None
            targets = [exe]
            sibling = exe.with_name("BTCBot-console.exe" if exe.name.lower() == "btcbot.exe" else "BTCBot.exe")
            if sibling.exists() and sibling.name in assets:
                targets.append(sibling)
            for t in targets:
                if t.name not in assets:
                    raise RuntimeError(f"release {self.latest['tag']} has no asset named {t.name}")
            token = self.token_getter()
            staging = self.app_dir / "update"
            staging.mkdir(parents=True, exist_ok=True)
            try:
                self.state = "downloading"
                sums = None
                if "SHA256SUMS.txt" in assets:
                    p = self.download(assets["SHA256SUMS.txt"]["url"], staging / "SHA256SUMS.txt", token)
                    sums = p.read_text(encoding="utf-8")
                downloaded: list[tuple[Path, Path]] = []
                for t in targets:
                    dest = staging / t.name
                    self.progress = (0, int(assets[t.name].get("size") or 0))
                    self.download(assets[t.name]["url"], dest, token, lambda d, tot: setattr(self, "progress", (d, tot)))
                    self._verify(dest, sums)
                    downloaded.append((t, dest))
                self.state = "installing"
                for target, new in downloaded:
                    old = target.with_name(target.stem + ".old" + target.suffix)
                    if old.exists():
                        old.unlink()
                    target.rename(old)  # allowed on Windows even while the exe is running
                    new.replace(target)
                self.state = "restarting"
                if before_restart:
                    before_restart()
                self.restart(exe)
                if self.on_event:
                    self.on_event({"event": "update_installed", "latest": self.latest})
                return {"installed": True, "tag": self.latest["tag"]}
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                self.state = "error"
                log.exception("update install failed")
                raise

    def cleanup_old(self) -> None:
        """Delete the previous executable left behind by an update (may still be exiting)."""
        if self.exe_path is None:
            return
        for old in self.exe_path.parent.glob("*.old.exe"):
            try:
                old.unlink()
            except OSError:
                pass

    # ----- background checks ------------------------------------------------------------
    def start_background(self, interval_minutes: float, auto_install: Callable[[], bool],
                         before_restart: Callable[[], None] | None = None, initial_delay: float = 5.0) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            if self._stop.wait(initial_delay):
                return
            while not self._stop.is_set():
                try:
                    self.check()
                    if self.available and self.can_install and auto_install():
                        self.install(before_restart)
                        return
                except Exception:  # noqa: BLE001 - keep checking
                    pass
                if self._stop.wait(max(60.0, interval_minutes * 60.0)):
                    return

        self._thread = threading.Thread(target=loop, daemon=True, name="updater")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
