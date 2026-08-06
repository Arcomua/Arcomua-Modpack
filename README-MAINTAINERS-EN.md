# Arcomua Modpack Maintainer Workflow

## 1. Install `arpack`

Requirements: Git, Python 3.11+, and a launcher or management tool that can export a Modrinth `.mrpack`.

From the repository root:

```bash
python -m pip install --upgrade .
arpack --help
```

GitHub CLI is only required for local yank/restore commands:

```bash
gh auth login
```

## 2. Build and export the pack

Update mods and configuration in your external tool, test the game, and export a Modrinth-format `.mrpack`.

Optional manual notes can be stored outside the repository in `notes.md`:

```markdown
- Fixed the default graphics preset.
- Improved resource-pack compatibility.
```

## 3. Import the release source

Normal release:

```bash
arpack import "/path/to/exported.mrpack" \
  --channel release \
  --notes-file "/path/to/notes.md"
```

For multiple releases on the same date:

```bash
arpack import "/path/to/exported.mrpack" \
  --channel beta \
  --release-id fix1 \
  --notes-file "/path/to/notes.md"
```

Available channels:

```text
release
beta
alpha
```

`arpack` automatically detects the loader and Minecraft version, switches to the compact version branch, generates the version number, imports the pack source, detects remote and embedded-JAR mod changes, normalizes `options.txt`, generates the changelog, and performs a reproducible local build check.

## 4. Inspect and clean-test

Run again when needed:

```bash
arpack check
```

Inspect:

```text
dist/*.mrpack
dist/*.mrpack.sha256
dist/CHANGELOG.md
dist/release-metadata.json
```

Import `dist/*.mrpack` into a new instance and verify download, startup, configuration, and world creation.

## 5. Commit and publish

```bash
git add -A
git commit -m "Release Arcomua Cloth 26.2"
git push -u origin cloth/26.2
```

Or let the import command commit and push:

```bash
arpack import pack.mrpack \
  --channel release \
  --notes-file notes.md \
  --commit \
  --push
```

The version-branch push triggers GitHub Actions, which builds the `.mrpack`, publishes it to Modrinth, and creates the GitHub Release.

## 6. Yank or restore a version

Yank the version recorded by the current branch:

```bash
arpack yank --reason "Crash on startup"
```

Restore it:

```bash
arpack restore
```

The same operation is available from:

```text
Actions → Manage published version → Run workflow
```
