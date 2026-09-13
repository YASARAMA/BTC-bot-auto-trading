"""Offline license keys.

A key looks like BTCB-7QD2M-KF9XA-3HJ4P-R8WZC. The app ships only the SHA-256 hash of
each valid key, so the executable carries nothing an attacker could use to mint new keys,
and validation needs no server and no internet.

What a license gates: live trading. Paper trading, backtesting, the charts and the AI
strategy all work unlicensed, so anyone can evaluate the bot safely before paying.

This is honest licensing, not copy protection: keys can be shared, and anyone able to
edit the code can bypass the check. It exists to make ownership clear, not to be
unbreakable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

PREFIX = "BTCB"
GROUPS = 4
GROUP_LEN = 5
# Crockford base32 without I, L, O and U: no character can be confused with another.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KEY_RE = re.compile(rf"^{PREFIX}(-[{ALPHABET}]{{{GROUP_LEN}}}){{{GROUPS}}}$")
LICENSE_FILE = "license.json"
HASHES_FILE = Path(__file__).resolve().parent / "license_hashes.json"


def normalize(key: str) -> str:
    """Upper-case, strip spaces, re-insert dashes, and fix common typos (O->0, I->1)."""
    raw = re.sub(r"[^0-9A-Za-z]", "", key or "").upper()
    raw = raw.replace("O", "0").replace("I", "1").replace("L", "1").replace("U", "V")
    if raw.startswith(PREFIX):
        raw = raw[len(PREFIX):]
    if len(raw) != GROUPS * GROUP_LEN:
        return key.strip().upper()
    groups = [raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN)]
    return "-".join([PREFIX, *groups])


def key_hash(key: str) -> str:
    return hashlib.sha256(normalize(key).encode("ascii", "ignore")).hexdigest()


def generate_key(rng: secrets.SystemRandom | None = None) -> str:
    r = rng or secrets.SystemRandom()
    body = "".join(r.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return normalize(PREFIX + body)


def generate_keys(count: int) -> list[str]:
    keys: set[str] = set()
    while len(keys) < count:
        keys.add(generate_key())
    return sorted(keys)


def load_valid_hashes(path: Path | None = None) -> set[str]:
    p = Path(path or HASHES_FILE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(h).lower() for h in data.get("hashes", [])}
    except FileNotFoundError:
        return set()
    except Exception as exc:  # noqa: BLE001 - a broken file must not stop the app
        log.warning("license hash file unreadable: %s", exc)
        return set()


@dataclass
class License:
    key: str = ""
    activated_at: str | None = None
    valid: bool = False
    reason: str = "no license key entered"

    @property
    def masked(self) -> str:
        """The key with its middle groups hidden, safe to show in the UI and logs."""
        if not self.key:
            return ""
        parts = self.key.split("-")
        return "-".join([parts[0], *(["•" * GROUP_LEN] * max(0, len(parts) - 2)), parts[-1]]) if len(parts) > 2 else self.key

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["masked"] = self.masked
        d.pop("key", None)  # never hand the raw key back to the browser
        return d


class LicenseManager:
    """Reads, validates and stores the license for one app directory."""

    def __init__(self, app_dir: Path, hashes_path: Path | None = None) -> None:
        self.path = Path(app_dir) / "data" / LICENSE_FILE
        self.hashes = load_valid_hashes(hashes_path)
        self.license = self._load()

    # ----- storage -----------------------------------------------------------------
    def _load(self) -> License:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return License()
        except Exception as exc:  # noqa: BLE001
            return License(reason=f"license file unreadable: {exc}")
        key = normalize(str(data.get("key", "")))
        lic = self.check(key)
        lic.activated_at = data.get("activated_at") or lic.activated_at
        return lic

    def _save(self, lic: License) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"key": lic.key, "activated_at": lic.activated_at}, indent=2), encoding="utf-8")

    # ----- validation ---------------------------------------------------------------
    def check(self, key: str) -> License:
        normalized = normalize(key)
        if not normalized:
            return License(reason="no license key entered")
        if not KEY_RE.match(normalized):
            return License(key=normalized, reason=f"that does not look like a key ({PREFIX}-XXXXX-XXXXX-XXXXX-XXXXX)")
        if not self.hashes:
            return License(key=normalized, valid=False, reason="this build carries no license list")
        if key_hash(normalized) not in self.hashes:
            return License(key=normalized, reason="this key is not in this build's list of valid keys")
        return License(key=normalized, valid=True, reason="license valid",
                       activated_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))

    def activate(self, key: str) -> License:
        lic = self.check(key)
        if lic.valid:
            self._save(lic)
            log.warning("license activated: %s", lic.masked)
        self.license = lic if lic.valid else self.license
        return lic

    def deactivate(self) -> License:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.license = License()
        return self.license

    # ----- policy --------------------------------------------------------------------
    @property
    def licensed(self) -> bool:
        return self.license.valid

    def require_for_live(self) -> None:
        """Raise unless this copy is licensed. Paper trading never calls this."""
        if not self.licensed:
            raise PermissionError(
                "Live trading needs a license key. Paper trading, backtesting and the AI "
                "strategy work without one: enter a key under Settings to enable live orders.")

    def status(self) -> dict[str, Any]:
        return {**self.license.to_dict(), "licensed": self.licensed, "keys_in_build": len(self.hashes),
                "file": str(self.path)}


def write_hash_file(keys: Iterable[str], path: Path, note: str = "") -> dict[str, Any]:
    """Write the public hash list that ships inside the app."""
    hashes = sorted({key_hash(k) for k in keys})
    payload = {
        "note": note or "SHA-256 of every valid license key. The keys themselves are not here.",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "count": len(hashes), "hashes": hashes,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
