from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .core import (
    BRANCH_RE,
    RepositoryConfig,
    WorkflowError,
    current_release_date,
    detect_product,
    extract_mrpack,
    find_repo_root,
    load_config,
    normalize_manifest_for_release,
    parse_config_text,
    read_release_toml,
    release_tag,
    require_clean_repo,
    run_git,
    validate_manifest,
    validate_release_date,
    validate_release_id,
    write_json,
    write_release_toml,
)
from .release import run_release

VERSION_WORKFLOW_PATH = Path(".github/workflows/publish-version.yml")
VERSION_BRANCH_ALLOWED_PREFIXES = (
    ".github/workflows/publish-version.yml",
    ".gitignore",
    "pack/",
    "release/",
)
VERSION_BRANCH_GITIGNORE = """# Local build output
dist/
*.mrpack

# Python
__pycache__/
*.py[cod]
*.egg-info/
build/
.venv/
venv/
"""


def remote_branch_exists(repo: Path, branch: str) -> bool:
    result = run_git(
        repo,
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        branch,
        check=False,
    )
    return result.returncode == 0


def local_branch_exists(repo: Path, branch: str) -> bool:
    return (
        run_git(
            repo,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 0
    )


def config_from_repository(
    repo: Path,
    relative_path: str,
    default_branch: str = "Main",
) -> RepositoryConfig:
    current_branch = run_git(repo, "branch", "--show-current").stdout.strip()
    if not BRANCH_RE.fullmatch(current_branch):
        local_path = repo / relative_path
        if local_path.exists():
            return load_config(local_path)

    result = run_git(
        repo,
        "show",
        f"{default_branch}:{relative_path}",
        check=False,
    )
    if result.returncode != 0:
        raise WorkflowError(
            f"Unable to load {relative_path} from the working tree or {default_branch}"
        )
    return parse_config_text(result.stdout)


def main_branch_file(repo: Path, default_branch: str, relative_path: Path) -> str:
    result = run_git(
        repo,
        "show",
        f"{default_branch}:{relative_path.as_posix()}",
        check=False,
    )
    if result.returncode != 0:
        raise WorkflowError(
            f"Missing {relative_path.as_posix()} on {default_branch}. "
            "Upgrade and commit the workflow on Main first."
        )
    return result.stdout


def is_allowed_version_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in VERSION_BRANCH_ALLOWED_PREFIXES
    )


def remove_empty_directories(repo: Path) -> None:
    for directory in sorted(
        (path for path in repo.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if ".git" in directory.parts:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass


def ensure_version_branch_layout(repo: Path, default_branch: str) -> list[str]:
    removed: list[str] = []
    tracked = run_git(repo, "ls-files", "-z").stdout.split("\0")
    for relative in tracked:
        if not relative or is_allowed_version_file(relative):
            continue
        target = repo / relative
        if target.is_file() or target.is_symlink():
            target.unlink()
        removed.append(relative)

    remove_empty_directories(repo)

    workflow_text = main_branch_file(repo, default_branch, VERSION_WORKFLOW_PATH)
    workflow_path = repo / VERSION_WORKFLOW_PATH
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(workflow_text, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(
        VERSION_BRANCH_GITIGNORE,
        encoding="utf-8",
        newline="\n",
    )
    return removed


def switch_version_branch(
    repo: Path,
    default_branch: str,
    target_branch: str,
) -> tuple[bool, list[str]]:
    run_git(repo, "fetch", "origin", default_branch, "--tags")

    if local_branch_exists(repo, target_branch):
        run_git(repo, "switch", target_branch)
        if remote_branch_exists(repo, target_branch):
            run_git(repo, "pull", "--ff-only", "origin", target_branch)
        return False, ensure_version_branch_layout(repo, default_branch)

    if remote_branch_exists(repo, target_branch):
        run_git(repo, "switch", "--track", "-c", target_branch, f"origin/{target_branch}")
        return False, ensure_version_branch_layout(repo, default_branch)

    run_git(repo, "switch", default_branch)
    run_git(repo, "pull", "--ff-only", "origin", default_branch)
    run_git(repo, "switch", "--orphan", target_branch)
    return True, ensure_version_branch_layout(repo, default_branch)


def run_local_check(
    repo: Path,
    config: RepositoryConfig,
    *,
    offline: bool = False,
    verify_reproducible: bool = True,
) -> None:
    print("\nRunning the same build and changelog logic used by GitHub Actions...\n")
    run_release(
        source=repo,
        config=config,
        publish=False,
        offline=offline,
        verify_reproducible=verify_reproducible,
    )
    print("\nLocal release check passed.")
    print(f"Test artifact: {repo / 'dist'}")


def import_command(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path.cwd())
    require_clean_repo(repo)
    config = config_from_repository(repo, args.config)
    release_date = (
        validate_release_date(args.date)
        if args.date
        else current_release_date(config)
    )
    release_id = validate_release_id(args.release_id)

    source = args.mrpack.expanduser().resolve()
    with zipfile.ZipFile(source, "r") as archive:
        try:
            manifest = json.loads(archive.read("modrinth.index.json").decode("utf-8"))
        except KeyError as exc:
            raise WorkflowError("The mrpack does not contain modrinth.index.json") from exc

    minecraft, loaders = validate_manifest(manifest)
    product = detect_product(config, loaders)
    branch = config.branch_pattern.format(line=product.branch_line, minecraft=minecraft)
    new_branch, removed = switch_version_branch(repo, config.default_branch, branch)

    manifest = extract_mrpack(source, repo / "pack")
    minecraft_after, loaders_after = validate_manifest(manifest)
    product_after = detect_product(config, loaders_after)
    if minecraft_after != minecraft or product_after.key != product.key:
        raise WorkflowError("The imported pack changed identity during extraction")

    full_version = normalize_manifest_for_release(
        manifest,
        product=product,
        minecraft=minecraft,
        release_date=release_date,
        release_id=release_id,
    )
    write_json(repo / "pack/modrinth.index.json", manifest)
    write_release_toml(
        repo / "release/release.toml",
        release_date=release_date,
        release_id=release_id,
        channel=args.channel,
        product=product,
        minecraft=minecraft,
    )

    manual_path = repo / "release/manual.md"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    if args.notes_file:
        manual_path.write_text(
            args.notes_file.expanduser().read_text(encoding="utf-8").strip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
    elif args.note:
        manual_path.write_text(args.note.strip() + "\n", encoding="utf-8", newline="\n")
    else:
        manual_path.write_text("", encoding="utf-8", newline="\n")

    print(f"Product: {product.display_name}")
    print(f"Minecraft: {minecraft}")
    print(f"Target branch: {branch}" + (" (created as orphan)" if new_branch else ""))
    print(f"Release channel: {args.channel}")
    print(f"Release date: {release_date}")
    print(f"Release ID: {release_id or '(none)'}")
    print(f"Modrinth version number: {full_version}")
    if removed:
        print(f"Removed {len(removed)} duplicated tracked file(s) from the version branch.")

    if not args.skip_check:
        run_local_check(
            repo,
            config,
            offline=args.offline,
            verify_reproducible=True,
        )
    else:
        print("\nWarning: local release check was skipped.")

    print("\nReview `git diff` and import dist/*.mrpack into a clean test instance.")

    if args.commit or args.push:
        run_git(repo, "add", "-A")
        message = args.message or f"Release {product.display_name} {full_version}"
        run_git(repo, "commit", "-m", message)
        print(f"Created commit: {message}")

    if args.push:
        if not args.yes:
            answer = input(
                f"Push {branch} and trigger automatic publication? Type RELEASE: "
            ).strip()
            if answer != "RELEASE":
                raise WorkflowError("Publication cancelled")
        run_git(repo, "push", "-u", "origin", branch)
        print("Pushed. GitHub Actions will publish the locally validated source.")

    return 0


def check_command(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path.cwd())
    config = config_from_repository(repo, args.config)
    run_local_check(
        repo,
        config,
        offline=args.offline,
        verify_reproducible=not args.skip_reproducible,
    )
    return 0


def clean_branch_command(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path.cwd())
    require_clean_repo(repo)
    config = config_from_repository(repo, args.config)
    branch = run_git(repo, "branch", "--show-current").stdout.strip()
    if not BRANCH_RE.fullmatch(branch):
        raise WorkflowError("clean-branch must be run on cloth/<minecraft> or anvil/<minecraft>")

    removed = ensure_version_branch_layout(repo, config.default_branch)
    run_local_check(
        repo,
        config,
        offline=args.offline,
        verify_reproducible=True,
    )
    print(f"Version branch layout cleaned. Removed {len(removed)} duplicated tracked file(s).")

    if args.commit or args.push:
        run_git(repo, "add", "-A")
        message = args.message or f"Clean version branch layout for {branch}"
        run_git(repo, "commit", "-m", message)
        print(f"Created commit: {message}")

    if args.push:
        if not args.yes:
            answer = input(f"Push the cleaned {branch} branch? Type CLEAN: ").strip()
            if answer != "CLEAN":
                raise WorkflowError("Operation cancelled")
        run_git(repo, "push", "origin", branch)
        print("Cleaned version branch pushed.")
    return 0


def product_for_line(config: RepositoryConfig, line: str):
    matches = [product for product in config.products.values() if product.branch_line == line]
    if len(matches) != 1:
        raise WorkflowError(f"Unknown product line: {line}")
    return matches[0]


def resolve_manage_target(
    repo: Path,
    config: RepositoryConfig,
    args: argparse.Namespace,
) -> tuple[str, str, str, str]:
    line = args.line
    minecraft = args.minecraft
    release_date = args.date
    release_id = args.release_id

    branch = run_git(repo, "branch", "--show-current").stdout.strip()
    branch_match = BRANCH_RE.fullmatch(branch)
    if branch_match:
        line = line or branch_match.group(1)
        minecraft = minecraft or branch_match.group(2)

    release_file = repo / "release/release.toml"
    if release_file.exists():
        release = read_release_toml(release_file)
        line = line or str(release.get("product", ""))
        minecraft = minecraft or str(release.get("minecraft", ""))
        release_date = release_date or release["date"]
        release_id = release_id if release_id is not None else release.get("release_id", "")

    if not line or not minecraft or not release_date:
        raise WorkflowError(
            "Unable to determine the target. Run this on a version branch, or provide "
            "--line, --minecraft, and --date."
        )

    product = product_for_line(config, line)
    validate_release_date(release_date)
    return product.branch_line, minecraft, release_date, validate_release_id(release_id)


def dispatch_manage_workflow(
    *,
    repo: Path,
    default_branch: str,
    action: str,
    line: str,
    minecraft: str,
    release_date: str,
    release_id: str,
    reason: str,
    yes: bool,
    dry_run: bool,
) -> None:
    tag = release_tag(line, minecraft, release_date, release_id)
    if dry_run:
        print("Dry run only. No GitHub workflow was dispatched.")
        print(f"Action: {action}")
        print(f"Tag: {tag}")
        print(f"Reason: {reason or '(none)'}")
        return

    if not shutil.which("gh"):
        raise WorkflowError(
            "GitHub CLI (`gh`) is required for arpack yank/restore. "
            "Install it or run the Manage published version workflow in GitHub Actions."
        )

    auth = subprocess.run(["gh", "auth", "status"], cwd=repo)
    if auth.returncode != 0:
        raise WorkflowError("GitHub CLI is not authenticated. Run `gh auth login` first.")

    confirmation = "YANK" if action == "yank" else "RESTORE"
    if not yes:
        answer = input(f"{confirmation} {tag}? Type {confirmation} to continue: ").strip()
        if answer != confirmation:
            raise WorkflowError("Operation cancelled")

    command = [
        "gh",
        "workflow",
        "run",
        "manage-version.yml",
        "--ref",
        default_branch,
        "-f",
        f"action={action}",
        "-f",
        f"line={line}",
        "-f",
        f"minecraft={minecraft}",
        "-f",
        f"date={release_date}",
        "-f",
        f"release_id={release_id}",
        "-f",
        f"reason={reason}",
    ]
    result = subprocess.run(command, cwd=repo)
    if result.returncode != 0:
        raise WorkflowError("Unable to dispatch the GitHub version-management workflow")

    verb = "Yank" if action == "yank" else "Restore"
    print(f"{verb} request submitted for {tag}.")
    print("Check the repository Actions page for completion.")


def manage_command(args: argparse.Namespace, action: str) -> int:
    repo = find_repo_root(Path.cwd())
    config = config_from_repository(repo, args.config)
    line, minecraft, release_date, release_id = resolve_manage_target(repo, config, args)
    dispatch_manage_workflow(
        repo=repo,
        default_branch=config.default_branch,
        action=action,
        line=line,
        minecraft=minecraft,
        release_date=release_date,
        release_id=release_id,
        reason=args.reason or "",
        yes=args.yes,
        dry_run=args.dry_run,
    )
    return 0


def add_manage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--line", choices=["cloth", "anvil"])
    parser.add_argument("--minecraft", help="Minecraft version, for example 26.2")
    parser.add_argument("--date", help="Published version date in YYMMDD format")
    parser.add_argument("--release-id", help="Optional release ID, for example fix1")
    parser.add_argument("--reason", help="Optional internal reason recorded in the workflow run")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Print the target only")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arpack",
        description="Import, validate, publish, yank, and restore Arcomua mrpacks.",
    )
    parser.add_argument(
        "--config",
        default="config/products.toml",
        help="Path on Main containing product configuration",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    imp = subparsers.add_parser(
        "import",
        help="Import an .mrpack exported by any Modrinth-format-capable launcher",
    )
    imp.add_argument("mrpack", type=Path)
    imp.add_argument("--channel", required=True, choices=["release", "beta", "alpha"])
    imp.add_argument(
        "--date",
        help="Override automatic YYMMDD date; intended only for migration/backfill",
    )
    imp.add_argument(
        "--release-id",
        help="Optional suffix for multiple releases on one date, for example fix1",
    )
    imp.add_argument("--notes-file", type=Path)
    imp.add_argument("--note")
    imp.add_argument("--offline", action="store_true", help="Do not resolve Mod names online")
    imp.add_argument("--skip-check", action="store_true", help="Skip the local CI-equivalent check")
    imp.add_argument("--commit", action="store_true")
    imp.add_argument("--push", action="store_true")
    imp.add_argument("--yes", action="store_true", help="Skip RELEASE confirmation")
    imp.add_argument("--message", help="Custom Git commit message")

    check = subparsers.add_parser(
        "check",
        help="Run the GitHub Actions build/changelog stage locally without publishing",
    )
    check.add_argument("--offline", action="store_true", help="Do not resolve Mod names online")
    check.add_argument(
        "--skip-reproducible",
        action="store_true",
        help="Build only once instead of verifying identical output",
    )

    clean = subparsers.add_parser(
        "clean-branch",
        help="Remove duplicated Main files from the current version branch",
    )
    clean.add_argument("--offline", action="store_true")
    clean.add_argument("--commit", action="store_true")
    clean.add_argument("--push", action="store_true")
    clean.add_argument("--yes", action="store_true")
    clean.add_argument("--message")

    yank = subparsers.add_parser(
        "yank",
        help="Archive a buggy Modrinth version and hide its GitHub Release",
    )
    add_manage_arguments(yank)

    restore = subparsers.add_parser(
        "restore",
        help="Restore a previously yanked Modrinth/GitHub version",
    )
    add_manage_arguments(restore)

    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            return import_command(args)
        if args.command == "check":
            return check_command(args)
        if args.command == "clean-branch":
            return clean_branch_command(args)
        if args.command == "yank":
            return manage_command(args, "yank")
        if args.command == "restore":
            return manage_command(args, "restore")
        raise WorkflowError(f"Unsupported command: {args.command}")
    except (WorkflowError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
