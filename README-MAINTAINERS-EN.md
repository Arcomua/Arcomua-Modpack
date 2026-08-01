# Arcomua Modpack Workflow Maintainer Guide (v5)

## 1. Branch layout

`Main` is the control branch. It contains shared tooling, GitHub Actions, product configuration, and documentation. It does not contain an active `pack/` directory or release-specific source.

Each version branch contains only:

```text
.github/workflows/publish-version.yml
.gitignore
pack/
release/
```

Version branches use:

```text
cloth/<Minecraft version>
anvil/<Minecraft version>
```

New version branches are created as orphan branches, so they do not inherit all files and history from `Main`. Existing v3 branches can be cleaned with `arpack clean-branch`.

## 2. Requirements

- Git
- Python 3.11 or newer
- Any launcher or management tool capable of exporting a Modrinth-format `.mrpack`
- GitHub CLI `gh` only for local `yank` and `restore` commands

## 3. Install or upgrade `arpack`

Run in the repository root on `Main`:

```bash
python -m pip install --upgrade .
```

Verify:

```bash
arpack --help
```

## 4. Upgrade from v3

On `Main`:

```bash
git switch Main
git pull --ff-only origin Main
```

Copy the v5 files, then remove active release source that must not remain on `Main`:

```bash
git rm -r pack release
git add -A
git commit -m "Upgrade Arcomua workflow to v5"
git push origin Main
python -m pip install --upgrade .
```

Clean an existing version branch such as `cloth/26.2`:

```bash
git switch cloth/26.2
git pull --ff-only origin cloth/26.2
arpack clean-branch --commit --push
```

Type `CLEAN` when prompted. The command keeps `pack/` and `release/` while removing duplicated tooling, documentation, and product directories inherited from `Main`.

## 5. Import and publish a modpack

Export a `.mrpack` using any compatible tool, then run:

```bash
arpack import "/path/to/export.mrpack" \
  --channel release \
  --notes-file "/path/to/notes.md"
```

The command automatically:

1. Detects Fabric or NeoForge;
2. Reads the Minecraft version;
3. Creates or switches to `cloth/<version>` or `anvil/<version>`;
4. Uses an orphan branch for a new version line;
5. Extracts the `.mrpack` into `pack/` on the version branch;
6. Writes `release/release.toml`;
7. Generates the changelog and final `.mrpack` locally;
8. Verifies reproducible output.

## 6. Version number and Modrinth version name

The base version number is generated automatically:

```text
<Minecraft version>-<lowercase loader>-<YYMMDD>
```

Examples:

```text
26.2-fabric-260801
26.2-neoforge-260801
```

The Modrinth version name uses the same components with spaces and the official loader name:

```text
26.2 Fabric 260801
26.2 NeoForge 260801
```

## 7. Multiple releases on the same date

Use the optional `--release-id`:

```bash
arpack import export.mrpack \
  --channel beta \
  --release-id fix1
```

This produces:

```text
Version number: 26.2-fabric-260801-fix1
Modrinth version name: 26.2 Fabric 260801 fix1
Git tag: cloth-26.2-260801-fix1
File: Arcomua-Cloth-26.2-Fabric-260801-fix1.mrpack
```

A release ID may contain 1–32 letters, numbers, dots, underscores, or hyphens. Without it, the original date-only format is retained.

## 8. Channel

Select one explicitly:

```text
--channel release
--channel beta
--channel alpha
```

The selected value is sent directly to Modrinth.

## 9. Local validation

```bash
arpack check
```

Output is written to:

```text
dist/
├── *.mrpack
├── *.mrpack.sha256
├── CHANGELOG.md
└── release-metadata.json
```

Import `dist/*.mrpack` into a clean test instance and verify downloads, startup, world loading, configurations, and resource packs.

## 10. Commit and publish

```bash
git add -A
git commit -m "Release Arcomua Cloth 26.2"
git push -u origin cloth/26.2
```

Or commit and push during import:

```bash
arpack import export.mrpack \
  --channel release \
  --release-id fix1 \
  --commit \
  --push
```

A push to a version branch publishes to Modrinth and creates a GitHub Release.

## 11. Yank and restore

From the target version branch:

```bash
arpack yank --reason "Crash on startup"
```

For a release with an ID:

```bash
arpack yank \
  --line cloth \
  --minecraft 26.2 \
  --date 260801 \
  --release-id fix1 \
  --reason "Crash on startup"
```

Restore it with:

```bash
arpack restore --line cloth --minecraft 26.2 --date 260801 --release-id fix1
```

## 12. Embedded mods in automatic changelogs

Some launchers place mods that cannot be represented as Modrinth download entries directly in:

```text
pack/overrides/mods/
```

v5 reads metadata from these JARs and compares them together with remote mods listed in `modrinth.index.json`.

Recognized metadata formats:

```text
Fabric: fabric.mod.json
NeoForge: META-INF/neoforge.mods.toml
Forge: META-INF/mods.toml
Quilt: quilt.mod.json
Legacy Forge: mcmod.info
```

For Forge and NeoForge `${file.jarVersion}` values, the workflow attempts to read `Implementation-Version` from `META-INF/MANIFEST.MF`.

Replacing a JAR with the same mod ID and a newer version produces an entry such as:

```markdown
### Updated
- Example Mod: `1.0.0` → `1.1.0`
```

A JAR with unrecognized metadata still participates in added, removed, and changed detection through its path and hash.

## 13. Automatic options.txt sanitization

Every import and build checks:

```text
pack/overrides/options.txt
pack/overrides/config/yosbr/options.txt
```

When present, the following values are normalized:

```text
lastServer:example.com  → lastServer: 
lang:zh_cn             → lang:en_us
```

Missing files and missing keys are skipped. All other settings remain unchanged.
