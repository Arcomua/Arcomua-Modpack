# English Maintainer Guide

## 1. Repository model

`Main` is the control branch. It stores shared tooling, GitHub Actions, product configuration, and maintainer documentation. Actual pack sources live in long-lived product/version branches:

```text
cloth/26.1.2
cloth/26.2
anvil/26.1.2
anvil/26.2
```

Mapping:

```text
Fabric   → Arcomua Cloth → cloth/<Minecraft version>
NeoForge → Arcomua Anvil → anvil/<Minecraft version>
```

Use external tool to remains the graphical authoring and testing environment. The repository tooling handles import, validation, changelog generation, build, publication, and version withdrawal.

## 2. Initial setup

### 2.1 Requirements

- Git
- Python 3.11+
- Game launcher allow you export modpack as Modrinth format
- GitHub CLI `gh` for the one-command yank/restore feature

### 2.2 Install `arpack`

Install:

```bash
python -m pip install --upgrade .
```

Verify:

```bash
arpack --help
```

Fallback:

```bash
python -m arpack --help
```

### 2.3 GitHub configuration

Create the repository Actions secret:

```text
MODRINTH_TOKEN
```

Enable:

```text
Settings → Actions → General → Workflow permissions
Read and write permissions
```

For local yank/restore commands, authenticate GitHub CLI:

```bash
gh auth login
```

## 3. Normal release procedure

### Step 1: Maintain the pack in game launcher

Use a development instance and a separate clean-test instance.

### Step 2: Export a Modrinth `.mrpack`

The temporary version entered in launcher does not matter. `arpack` replaces it.

### Step 3: Optionally write manual notes

Example `notes.md`:

```markdown
- Fixed the default graphics preset.
- Improved resource-pack compatibility.
```

### Step 4: Import and validate

```bash
arpack import "/path/to/exported.mrpack" \
  --channel beta \
  --notes-file "/path/to/notes.md"
```

The tool automatically detects the product and Minecraft version, switches or creates the correct branch, uses the current UTC+8 date, rewrites the version number, generates the changelog, and performs a reproducible local build.

Version format:

```text
<Minecraft version>-<loader>-<YYMMDD>
```

Example:

```text
26.2-fabric-260731
```

The channel is always an explicit maintainer choice:

```text
release
beta
alpha
```

Use `--date YYMMDD` only for migration or backfilling an older release.

### Step 5: Inspect the output

```bash
arpack check
```

Output:

```text
dist/*.mrpack
dist/*.mrpack.sha256
dist/CHANGELOG.md
dist/release-metadata.json
```

### Step 6: Clean-test in game launcher

Import the generated `dist/*.mrpack` into a new instance and verify download, startup, world creation, configuration, and resource packs.

### Step 7: Commit and push

```bash
git add pack release
git commit -m "Release Arcomua Cloth 26.2 Fabric 260731"
git push -u origin cloth/26.2
```

Or:

```bash
arpack import pack.mrpack \
  --channel release \
  --notes-file notes.md \
  --commit \
  --push
```

GitHub Actions then validates, builds, publishes to Modrinth, and creates the GitHub Release.

## 4. Changelog generation

The first release for a product/Minecraft branch contains only:

```text
Initial version
```

Later releases automatically list added, updated, and removed mods, followed by the text from `release/manual.md`.

## 5. Yank a buggy version

A yank is reversible:

- Modrinth status becomes `archived`;
- the GitHub Release becomes a draft;
- the Git tag and audit history remain.

From the target version branch:

```bash
arpack yank --reason "Crash on startup"
```

Or specify the target explicitly:

```bash
arpack yank \
  --line cloth \
  --minecraft 26.2 \
  --date 260731 \
  --reason "Crash on startup"
```

Use `--dry-run` to inspect the target without dispatching anything.

The same operation is available in the GitHub UI:

```text
Actions → Manage published version → Run workflow
```

## 6. Restore a yanked version

```bash
arpack restore
```

Or:

```bash
arpack restore --line cloth --minecraft 26.2 --date 260731
```

This returns the Modrinth version to `listed` and republishes the GitHub Release.

## 7. Versioning limitation

Because the version number contains only the date, each product/loader/Minecraft line can publish only one distinct build per calendar day. Yank a broken same-day release and publish the corrected build under a new date.
