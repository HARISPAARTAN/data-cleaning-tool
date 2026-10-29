#!/usr/bin/env python3
"""Vendor portfolio-kit's dist/ at a tag into web/kit/, or check that web/kit/ still matches it.

Reference copy from https://github.com/HARISPAARTAN/portfolio-kit (tools/sync_kit.py). Consumers copy this
file into their own tools/ folder. Python 3.10 or newer, standard library only.

  python tools/sync_kit.py --tag v0.1.0          copy dist/ at v0.1.0 into web/kit/ and write web/kit/VERSION
  python tools/sync_kit.py --check               compare web/kit/ with dist/ at the tag named in web/kit/VERSION
  python tools/sync_kit.py --check --kit .kit    the same against another clone (the consumer's CI)

The kit clone defaults to $PORTFOLIO_KIT, then the portfolio-kit folder next to the repository that holds this
script (<repo>/tools/sync_kit.py, so <repo>/../portfolio-kit).
An existing destination is replaced only when it is an earlier copy of the kit (MANIFEST.json with the name
portfolio-kit, and VERSION) or an empty folder. The new copy is written to a temporary sibling folder and
moved into place once it is complete and matches its MANIFEST.json, so a failed run leaves the old copy alone.
Exit codes: 0 match or copied, 1 differences or a refusal (unsafe archive, bad manifest, destination that is
not a kit copy), 2 usage, git or file system error.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import NoReturn

KIT_NAME = "portfolio-kit"


class Refused(Exception):
    """The copy or the check cannot go on; main() prints the message and exits with 1."""


def default_kit() -> str:
    """$PORTFOLIO_KIT, else the sibling clone of the repository this script lives in (<repo>/tools/sync_kit.py)."""
    env = os.environ.get("PORTFOLIO_KIT")
    if env:
        return env
    parents = Path(__file__).resolve().parents
    return str(parents[2] / KIT_NAME) if len(parents) > 2 else KIT_NAME


def die(message: str) -> NoReturn:
    print(f"sync_kit: {message}", file=sys.stderr)
    sys.exit(2)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_tag(tag: str) -> None:
    """A tag that starts with '-' would be read by git as an option."""
    if tag.startswith("-"):
        die(f"invalid tag {tag!r}: it must not start with '-'")


def git_archive(kit: str, tag: str) -> bytes:
    check_tag(tag)
    try:
        return subprocess.run(["git", "-C", kit, "archive", "--format=tar", tag, "dist"], check=True, capture_output=True).stdout
    except FileNotFoundError:
        die("git is not on PATH")
    except subprocess.CalledProcessError as exc:
        die(f"cannot read dist/ at {tag} from {kit}: {exc.stderr.decode(errors='replace').strip()}")


def archive_files(raw: bytes) -> dict[str, bytes]:
    """Read a tar archive of dist/ into {path below dist/: bytes}; every member name is validated first."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if "\\" in member.name or ":" in member.name or "\x00" in member.name or path.is_absolute() or ".." in path.parts:
                    raise Refused(f"unsafe path in the archive: {member.name!r}")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise Refused(f"not a regular file in the archive: {member.name!r}")
                if len(path.parts) < 2 or path.parts[0] != "dist":
                    raise Refused(f"unexpected path in the archive: {member.name!r}")
                name = path.relative_to("dist").as_posix()
                if name in files:
                    raise Refused(f"duplicate path in the archive: {member.name!r}")
                files[name] = archive.extractfile(member).read()
    except tarfile.TarError as exc:
        raise Refused(f"cannot read the archive: {exc}") from exc
    return files


def kit_files(kit: str, tag: str) -> dict[str, bytes]:
    """Read dist/ at the tag with git archive and return {path below dist/: bytes}."""
    files = archive_files(git_archive(kit, tag))
    if "MANIFEST.json" not in files:
        raise Refused(f"dist/MANIFEST.json is missing at {tag}")
    return files


def load_manifest(raw: bytes, where: str) -> dict:
    """Parse a MANIFEST.json and check the keys that compare() and the destination guard rely on."""
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise Refused(f"{where} is not valid JSON: {exc}") from exc
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if (
        not isinstance(entries, list)
        or manifest.get("name") != KIT_NAME
        or not all(
            isinstance(e, dict) and isinstance(e.get("file"), str) and isinstance(e.get("bytes"), int) and isinstance(e.get("sha256"), str)
            for e in entries
        )
    ):
        raise Refused(f'{where} is not a {KIT_NAME} manifest (needs "name": "{KIT_NAME}" and files with file, bytes and sha256)')
    return manifest


def compare(manifest: dict, files: dict[str, bytes]) -> list[str]:
    """List the differences between a MANIFEST.json and {path: bytes}."""
    want = {entry["file"]: entry for entry in manifest["files"]}
    problems = []
    for name in sorted(files):
        entry = want.pop(name, None)
        data = files[name]
        if entry is None:
            problems.append(f"not in MANIFEST.json: {name}")
        elif entry["bytes"] != len(data) or entry["sha256"] != sha256(data):
            problems.append(f"changed: {name}")
    problems.extend(f"missing: {name}" for name in sorted(want))
    return problems


def read_dest(dest: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(dest.rglob("*")):
        name = path.relative_to(dest).as_posix()
        if path.is_file() and name not in ("MANIFEST.json", "VERSION"):
            files[name] = path.read_bytes()
    return files


def ensure_replaceable(dest: Path) -> None:
    """Refuse unless dest is missing, empty, or an earlier copy of the kit (exact names, also on Windows)."""
    where = dest.as_posix()
    if not dest.exists() and not dest.is_symlink():
        return
    if dest.is_symlink() or not dest.is_dir():
        raise Refused(f"refusing to replace {where}: it is not a plain folder")
    names = {entry.name for entry in dest.iterdir()}
    if not names:
        return
    try:
        manifest = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8")) if {"MANIFEST.json", "VERSION"} <= names else None
    except (OSError, ValueError, RecursionError):
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("name") != KIT_NAME:
        raise Refused(f'refusing to replace {where}: it is not an earlier copy of the kit (needs MANIFEST.json with "name": "{KIT_NAME}" and VERSION)')


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    base = root.resolve()
    for name, data in files.items():
        target = (base / name).resolve()
        if target == base or not target.is_relative_to(base):
            raise Refused(f"unsafe path in the archive: {name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def install(staging: Path, dest: Path) -> None:
    """Move staging to dest; the old dest is renamed aside first and put back if the move fails."""
    backup = None
    if dest.exists():
        backup = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".kit-old-"))
        backup.rmdir()
        os.replace(dest, backup)
    try:
        os.replace(staging, dest)
    except BaseException:
        if backup is not None:
            os.replace(backup, dest)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
        if backup.exists():
            print(f"sync_kit: could not remove {backup.as_posix()}; delete it by hand", file=sys.stderr)


def sync(kit: str, tag: str, dest: Path) -> int:
    files = kit_files(kit, tag)
    manifest = load_manifest(files["MANIFEST.json"], f"dist/MANIFEST.json at {tag}")
    payload = {name: data for name, data in files.items() if name != "MANIFEST.json"}
    problems = compare(manifest, payload)
    if problems:
        raise Refused(f"dist/ at {tag} does not match its own MANIFEST.json ({problems[0]})")
    if dest.resolve() in (Path.cwd().resolve(), Path(kit).resolve()):
        die(f"refusing to replace {dest.as_posix()}: choose a folder such as web/kit")
    ensure_replaceable(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".kit-"))
    try:
        if os.name == "posix":  # mkdtemp creates the folder with mode 0700; give it the mode a plain mkdir would
            umask = os.umask(0)
            os.umask(umask)
            staging.chmod(0o777 & ~umask)
        write_tree(staging, {**files, "VERSION": f"{tag}\n".encode()})
        written = compare(manifest, read_dest(staging))
        if written:
            raise Refused(f"the copy of dist/ at {tag} does not match its MANIFEST.json after writing ({written[0]})")
        install(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"copied {len(payload)} files of portfolio-kit {tag} into {dest.as_posix()}")
    return 0


def check(kit: str, tag: str | None, dest: Path) -> int:
    if not (dest / "MANIFEST.json").is_file():
        die(f"{dest.as_posix()}/MANIFEST.json not found: copy the kit first with --tag")
    version = dest / "VERSION"
    pinned = version.read_text(encoding="utf-8", errors="replace").strip() if version.is_file() else None
    tag = tag or pinned
    if not tag:
        die(f"no --tag given and no {dest.as_posix()}/VERSION")
    check_tag(tag)
    local = (dest / "MANIFEST.json").read_bytes()
    manifest = load_manifest(local, f"{dest.as_posix()}/MANIFEST.json")
    problems = [] if pinned == tag else [f"VERSION says {pinned}, expected {tag}"]
    problems += compare(manifest, read_dest(dest))
    if kit_files(kit, tag)["MANIFEST.json"] != local:
        problems.append(f"MANIFEST.json differs from dist/MANIFEST.json at {tag}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"{dest.as_posix()} differs from portfolio-kit {tag} ({len(problems)} problem(s))")
        return 1
    print(f"{dest.as_posix()} matches portfolio-kit {tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vendor portfolio-kit's dist/ into web/kit/, or check the copy.")
    parser.add_argument("--tag", help="tag or commit of portfolio-kit; --check defaults to the tag in <dest>/VERSION")
    parser.add_argument("--kit", default=default_kit(), help="local clone of portfolio-kit (default: $PORTFOLIO_KIT, else ../portfolio-kit next to the repository holding this script)")
    parser.add_argument("--dest", default=Path("web/kit"), type=Path, help="vendored folder (default: web/kit)")
    parser.add_argument("--check", action="store_true", help="compare instead of copying")
    args = parser.parse_args(argv)
    if args.tag is not None:
        check_tag(args.tag)
    try:
        if args.check:
            return check(args.kit, args.tag, args.dest)
        if not args.tag:
            parser.error("--tag is required unless --check is given")
        return sync(args.kit, args.tag, args.dest)
    except Refused as exc:
        print(f"sync_kit: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        die(f"file system error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
