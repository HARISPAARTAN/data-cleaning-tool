# Handover

What the team that runs the tool needs day to day: adding a profile for a new partner file, pinning encodings for strict runs, scheduling, refreshing the fixtures and golden files, and releasing. Commands are run from the repository root of a clone after `uv sync --all-extras`; with an installed tool, leave out `uv run`.

## Adding a partner profile

1. Look at the file before writing anything:

   ```bash
   uv run clean-data profile inbox/partner_c.csv --html out/partner_c-profile.html
   ```

   Note the encoding and how it was found, the delimiter, the header row, the normalised header names, the type guesses with their shares and the ambiguity notes (day-first or month-first dates, `1.234` in German or English style). A type guess labelled `(native)`, such as `date (native)`, comes from native Excel date cells and has no text pattern.

2. Write a skeleton and edit it:

   ```bash
   uv run clean-data init inbox/partner_c.csv --out profiles/partner_c.yaml
   ```

   The skeleton has the detected `input` settings, a `rename` comment listing every normalised name, one `columns` entry per non-empty column with the guessed type, and `dedupe: {exact: true}`. Add what only a person can decide: output names in `headers.rename`, value maps (`country`), the validation rules (`required`, `min`, `max`, `regex`, `allowed`), missing-value strategies, key rules for de-duplication, `quality.max_reject_rate`, and `report.redact` for e-mail and name columns. Every key is in the [profile reference](profile-reference.md), which the leading comment of the skeleton names, and [profile.schema.json](profile.schema.json) gives an editor with YAML language support the key completion once the profile starts with the line `# yaml-language-server: $schema=../docs/profile.schema.json` (the path as seen from the profile; the committed profiles have it, the generated skeleton does not, so add it). For a workbook the skeleton writes the header row that all selected sheets share (an explicit `--header-row` is kept) and `auto` when they differ. Line-break characters in names and comments, including U+0085, U+2028 and U+2029, are escaped, so the skeleton always validates. `init` on a file without usable columns (no records, or every column empty) exits 3.

3. Dry-run with explanations until the report reads right:

   ```bash
   uv run clean-data clean inbox/partner_c.csv --profile profiles/partner_c.yaml --dry-run --explain --report out/partner_c.html
   ```

   Check the row flow, the rules with their examples, the rejects preview and the columns before and after. A dry run writes only the report.

4. Check what a scheduled run would do: `--dry-run --strict` has the exit code of a real strict run without writing any data.

5. Commit the profile. If the partner file is fuzzy-matched (`dedupe.fuzzy`), leave `action: flag` until someone has reviewed the clusters in a few reports; switch to `remove` only then.

## Pinning encodings for `--strict`

With `encoding: auto`, a file without a BOM that is not valid UTF-8 is decoded by detection, and detection is a warning, which `--strict` turns into exit 4. That is deliberate: a scheduled job should not depend on a guess. Pin what `profile` found:

```yaml
input:
  encoding: cp1252
  delimiter: "\t"
```

Western files with bytes in 0x80 to 0x9F (curly quotes, the euro sign as 0x80) are `cp1252`; files without them can be `latin-1`. A pinned encoding fails with exit 3 and the byte offset only when the bytes of a later file cannot be decoded with it. `latin-1` decodes any byte and `cp1252` almost every byte, so a file that arrives in another encoding can still come through with wrong characters and no error; check the `profile` output when a partner changes its export. UTF-8 files with or without a BOM need no pin.

## Scheduling

The tool never prompts and never needs a network, so a scheduler only runs one command line and looks at the exit code: 0 success, 4 quality gate or strict failure (the rejects file and the reports are written, the cleaned file is not), 2, 3 or 5 a profile, input or output problem, 1 a bug. Install it once with `uv tool install` (see the README); `uv tool dir --bin` prints the folder of the `clean-data` executable.

Windows Task Scheduler, every Monday at 06:00 (one line; the folder `D:\cleaning` is an example):

```powershell
schtasks /Create /TN "clean partner_a" /SC WEEKLY /D MON /ST 06:00 /TR "D:\cleaning\run-partner-a.cmd"
```

with `D:\cleaning\run-partner-a.cmd`. Create the `logs` folder once (`mkdir D:\cleaning\logs`): a `--log-file` in a missing folder is a usage error, exit 2.

```bat
cd /d D:\cleaning
clean-data --log-file logs\partner_a.log clean inbox\partner_a.csv --profile profiles\partner_a.yaml --strict --out clean\partner_a.csv --rejects clean\partner_a_rejects.csv --report clean\partner_a.html --report clean\partner_a.json
```

cron on the Linux server, the same schedule (create the folder once with `mkdir -p /srv/cleaning/logs`):

```text
0 6 * * 1 cd /srv/cleaning && clean-data --log-file logs/partner_a.log clean inbox/partner_a.csv --profile profiles/partner_a.yaml --strict --out clean/partner_a.csv --rejects clean/partner_a_rejects.csv --report clean/partner_a.html --report clean/partner_a.json
```

cron runs with a short `PATH`; use the full path that `uv tool dir --bin` prints if `clean-data` is not found. Downstream jobs read `clean/partner_a.json` (`run.status`, `rows`) rather than parsing logs; its keys are stable within `schema_version: 1`.

## Refreshing fixtures and golden files

The fixtures are generated; never edit them by hand.

```bash
uv run python tools/make_data.py            # fixtures 1 to 4, expected outcomes, MANIFEST.json
uv run python tools/make_data.py --check    # what CI runs: regenerate into a temporary folder and compare
uv run python tools/make_data.py --large    # also writes fixture 5 (not committed)
```

After a change to the cleaning code that is meant to change outputs:

```bash
uv run pytest tests/fixtures --update-golden
git diff --stat tests/golden
git diff tests/golden
```

Read the diff row by row before committing it: a golden change is a behaviour change. The planted counts in `fixtures/expected/` do not change with `--update-golden`; if a fixture test fails on them, the code is wrong or the generator's planting changed. `--update-golden` is the only way to regenerate the golden files, and each fixture test checks the planted counts first, so a wrong count fails before any golden file is written.

## Rebuilding the demo

`web/demo/` and `web/index.html` are rebuilt from fixture 1 and checked byte for byte in CI. The reports name the tool version and the installed package versions, so a code change, a version bump or a dependency update (including Dependabot's) needs a rebuild:

```bash
uv run python tools/build_demo.py
uv run python tools/build_demo.py --check
```

## Updating the portfolio kit

The Pages site uses a pinned tag of portfolio-kit, vendored into `web/kit/`. The script reads the tag from a local clone of portfolio-kit (it runs `git archive`; there is no network access): the `--kit <folder>` option, else the folder in `$PORTFOLIO_KIT`, else the `portfolio-kit` folder next to this repository (`../portfolio-kit`). Clone it there first, and make sure the clone has the tag.

```bash
uv run python tools/sync_kit.py --tag v0.1.0
uv run python tools/sync_kit.py --tag v0.1.0 --check
```

For a new kit tag, run both with the new tag, change the tag in the `checks` job of `.github/workflows/ci.yml`, and check the report tokens test (`tests/unit/test_report.py`), which compares the report's inline colours with `web/kit/tokens.css`.

## Releasing

1. Set the new version in `pyproject.toml`, run `uv lock`, and rebuild the demo (the version is in the reports).
2. Add `## vX.Y.Z (YYYY-MM-DD)` to `CHANGELOG.md`.
3. Run the full checks: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run mypy --strict -p cleandata.core -p cleandata.report`. The default suite includes the soft timing check on fixture 5; above its limits it prints a warning and does not fail; it fails only with `CLEANDATA_TIMING_STRICT=1` (for local tuning) or when a run it makes exits non-zero. CI runs it in one job of its own.
4. Commit with `chore: release X.Y.Z`, tag with `git tag -a vX.Y.Z -m "vX.Y.Z"`, push the commit and the tag.
5. Build and attach the assets. There is no notes file in the repository: the release notes are the `CHANGELOG.md` section of the version, cut into a temporary file when you publish.

   ```bash
   uv build
   NOTES=$(mktemp)
   awk '/^## vX.Y.Z /{f=1; next} /^## /{f=0} f' CHANGELOG.md > "$NOTES"
   gh release create vX.Y.Z dist/data_cleaning_tool-X.Y.Z-py3-none-any.whl dist/data_cleaning_tool-X.Y.Z.tar.gz docs/profile.schema.json web/demo/report.html --title "vX.Y.Z" --notes-file "$NOTES"
   ```

6. Update the install command in both READMEs if the wheel name changed.

## Housekeeping

- The optional extras are `fuzzy` (rapidfuzz) and `parquet` (pyarrow). `uv sync --all-extras` installs both. Without one, a profile with `dedupe.fuzzy` or an `--out` ending in `.parquet` stops with exit 2 and names the install command, and the tests that need the extra are skipped with the reason.
- `.hypothesis/` (the example database of the property tests) and `.venv/` are git-ignored and can be deleted at any time; `uv sync` rebuilds the environment.
- Dependabot pull requests that move the uv version (the `setup-uv` pin and the uv version it installs) may need a manual follow-up: check the workflow files by hand after merging one.
