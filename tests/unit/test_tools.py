"""The release checks in tools/ fail loudly: no web/ folder, an unknown licence text, a broken uv call."""

from __future__ import annotations

import shutil
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

import check_licences
import check_offline
import check_weight


def test_check_offline_fails_without_web_and_without_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_offline, "ROOT", tmp_path)
    monkeypatch.setattr(check_offline, "FOLDERS", [tmp_path / "web", tmp_path / "report"])
    assert check_offline.main() == 1
    assert "web/ is missing" in capsys.readouterr().out
    (tmp_path / "web").mkdir()
    assert check_offline.main() == 1
    assert "nothing was checked" in capsys.readouterr().out
    (tmp_path / "web" / "index.html").write_text("<p>hello</p>", encoding="utf-8")
    assert check_offline.main() == 0


def test_check_weight_fails_without_web_and_without_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_weight, "ROOT", tmp_path)
    monkeypatch.setattr(check_weight, "WEB", tmp_path / "web")
    assert check_weight.main() == 1
    assert "web/ is missing" in capsys.readouterr().out
    (tmp_path / "web").mkdir()
    assert check_weight.main() == 1
    assert "holds no file" in capsys.readouterr().out
    (tmp_path / "web" / "index.html").write_text("<p>hello</p>", encoding="utf-8")
    assert check_weight.main() == 0


def fake_distribution(*classifiers: str) -> SimpleNamespace:
    meta = Message()
    meta["Name"] = "odd"
    for classifier in classifiers:
        meta["Classifier"] = classifier
    return SimpleNamespace(metadata=meta, version="1")


def test_an_unknown_licence_classifier_keeps_its_raw_text() -> None:
    dist = fake_distribution("License :: OSI Approved :: GNU General Public License v3 (GPLv3)")
    operator, ids, source = check_licences.licence_of(dist)  # type: ignore[arg-type]
    assert (operator, ids, source) == ("", ["GNU General Public License v3 (GPLv3)"], "classifier")
    assert not check_licences.verdict(operator, ids, check_licences.ALLOWED)
    known = fake_distribution("License :: OSI Approved :: MIT License")
    assert check_licences.licence_of(known)[1] == ["MIT"]  # type: ignore[arg-type]


def test_a_failing_uv_export_prints_its_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is not on the PATH")
    monkeypatch.setattr(check_licences, "ROOT", tmp_path)  # a folder without a project, so uv export fails
    with pytest.raises(SystemExit) as caught:
        check_licences.runtime_names()
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert error.startswith("check_licences: uv export")
    assert len(error.strip().splitlines()) > 1, error
