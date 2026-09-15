import csv
import json
from pathlib import Path

import pytest

from bot.licensing import (
    KEY_RE,
    LicenseManager,
    generate_key,
    generate_keys,
    key_hash,
    load_valid_hashes,
    normalize,
    write_hash_file,
)


def test_generated_keys_have_the_right_shape_and_are_unique():
    keys = generate_keys(200)
    assert len(set(keys)) == 200
    for k in keys:
        assert KEY_RE.match(k), k
        assert k.startswith("BTCB-") and len(k) == 4 + 4 * 6
        assert not set("ILOU") & set(k[5:]), "ambiguous characters must not appear"


def test_normalize_accepts_sloppy_input():
    key = generate_key()
    assert normalize(key.lower()) == key
    assert normalize(key.replace("-", "")) == key
    assert normalize(key.replace("-", " ").lower()) == key
    assert normalize(f"  {key}  ") == key
    # O/I/L are read as 0/1/1 because the alphabet has no such characters
    assert normalize("BTCB-O1234-56789-ABCDE-FGHJK") == "BTCB-01234-56789-ABCDE-FGHJK"


def test_shipped_hash_file_matches_the_private_key_list():
    shipped = load_valid_hashes()
    assert len(shipped) == 100, "this build should carry exactly the 100 issued keys"
    private = Path("licenses-private.csv")
    if not private.exists():
        pytest.skip("the private key list is not on this machine")
    with private.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 100
    assert {key_hash(r["key"]) for r in rows} == shipped
    assert all(r["hash"] == key_hash(r["key"]) for r in rows)


def manager(tmp_path, keys):
    (tmp_path / "data").mkdir(exist_ok=True)
    hashes = tmp_path / "hashes.json"
    write_hash_file(keys, hashes)
    return LicenseManager(tmp_path, hashes)


def test_activation_persists_and_gates_live_trading(tmp_path):
    keys = generate_keys(3)
    m = manager(tmp_path, keys)
    assert not m.licensed
    with pytest.raises(PermissionError, match="Live trading needs a license"):
        m.require_for_live()

    assert m.activate(keys[1]).valid
    assert m.licensed and m.require_for_live() is None
    assert m.license.masked.endswith(keys[1].split("-")[-1]) and keys[1] not in m.license.masked

    again = LicenseManager(tmp_path, tmp_path / "hashes.json")  # restart
    assert again.licensed

    again.deactivate()
    assert not again.licensed
    assert not LicenseManager(tmp_path, tmp_path / "hashes.json").licensed


def test_invalid_keys_are_refused_with_a_reason(tmp_path):
    m = manager(tmp_path, generate_keys(2))
    assert "does not look like a key" in m.check("hello").reason
    assert "not in this build" in m.check(generate_key()).reason
    assert "no license key" in m.check("").reason
    assert not m.licensed


def test_status_never_leaks_the_key(tmp_path):
    keys = generate_keys(1)
    m = manager(tmp_path, keys)
    m.activate(keys[0])
    blob = json.dumps(m.status())
    assert keys[0] not in blob and m.status()["licensed"] is True
    assert m.status()["keys_in_build"] == 1


def test_build_without_a_hash_list_licenses_nothing(tmp_path):
    (tmp_path / "data").mkdir()
    m = LicenseManager(tmp_path, tmp_path / "missing.json")
    assert m.check(generate_key()).reason == "this build carries no license list"
    assert not m.licensed
