from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .core import (
    BRANCH_RE,
    WorkflowError,
    current_release_date,
    detect_product,
    extract_mrpack,
    find_repo_root,
    load_config,
    normalize_manifest_for_release,
    read_release_toml,
    require_clean_repo,
    run_git,
    validate_manifest,
    validate_release_date,
    write_json,
    write_release_toml,
)


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


def switch_version_branch(repo: Path, default_branch: str, target_branch: str) -> bool:
    run_git(repo, "fetch", "origin", default_branch, "--tags")

    if local_branch_exists(repo, target_branch):
        run_git(repo, "switch", target_branch)
        if remote_branch_exists(repo, target_branch):
            run_git(repo, "pull", "--ff-only", "origin", target_branch)
        return False

    if remote_branch_exists(repo, target_branch):
        run_git(repo, "switch", "--track", "-c", target_branch, f"origin/{target_branch}")
        return False

    run_git(repo, "switch", default_branch)
    run_git(repo, "pull", "--ff-only", "origin", default_branch)
    run_git(repo, "switch", "-c", target_branch)
    return True


def run_local_check(
    repo: Path,
    config_relative: str,
    *,
    offline: bool = False,
    verify_reproducible: bool = True,
) -> None:
    command = [
        sys.executable,
        str(repo / "ci/release.py"),
        "--source",
        str(repo),
        "--config",
        str(repo / config_relative),
    ]
    if offline:
        command.append("--offline")
    if verify_reproducible:
        command.append("--verify-reproducible")

    print("\nRunning the same build and changelog logic used by GitHub Actions...\n")
    result = subprocess.run(command, cwd=repo)
    if result.returncode != 0:
        raise WorkflowError("Local release check failed. Nothing was committed or pushed.")

    print("\nLocal release check passed.")
    print(f"Test artifact: {repo / 'dist'}")


def import_command(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path.cwd())
    require_clean_repo(repo)
    config = load_config(repo / args.config)
    release_date = (
        validate_release_date(args.date)
        if args.date
        else current_release_date(config)
    )

    source = args.mrpack.expanduser().resolve()
    with zipfile.ZipFile(source, "r") as archive:
        try:
            manifest = json.loads(archive.read("modrinth.index.json").decode("utf-8"))
        except KeyError as exc:
            raise WorkflowError("The mrpack does not contain modrinth.index.json") from exc

    minecraft, loaders = validate_manifest(manifest)
    product = detect_product(config, loaders)
    branch = config.branch_pattern.format(line=product.branch_line, minecraft=minecraft)
    new_branch = switch_version_branch(repo, config.default_branch, branch)

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
    )
    write_json(repo / "pack/modrinth.index.json", manifest)
    write_release_toml(
        repo / "release/release.toml",
        release_date=release_date,
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
    print(f"Target branch: {branch}" + (" (created)" if new_branch else ""))
    print(f"Release channel: {args.channel}")
    print(f"Release date: {release_date}")
    print(f"Modrinth version number: {full_version}")

    if not args.skip_check:
        run_local_check(
            repo,
            args.config,
            offline=args.offline,
            verify_reproducible=True,
        )
    else:
        print("\nWarning: local release check was skipped.")

    print("\nReview `git diff` and import dist/*.mrpack into a clean Prism instance.")

    if args.commit or args.push:
        run_git(repo, "add", "pack", "release")
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
    run_local_check(
        repo,
        args.config,
        offline=args.offline,
        verify_reproducible=not args.skip_reproducible,
    )
    return 0


def product_for_line(config, line: str):
    matches = [product for product in config.products.values() if product.branch_line == line]
    if len(matches) != 1:
        raise WorkflowError(f"Unknown product line: {line}")
    return matches[0]


def resolve_manage_target(
    repo: Path,
    config,
    args: argparse.Namespace,
) -> tuple[str, str, str]:
    line = args.line
    minecraft = args.minecraft
    release_date = args.date

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

    if not line or not minecraft or not release_date:
        raise WorkflowError(
            "Unable to determine the target. Run this on a version branch, or provide "
            "--line, --minecraft, and --date."
        )

    product = product_for_line(config, line)
    validate_release_date(release_date)
    return product.branch_line, minecraft, release_date


def dispatch_manage_workflow(
    *,
    repo: Path,
    default_branch: str,
    action: str,
    line: str,
    minecraft: str,
    release_date: str,
    reason: str,
    yes: bool,
    dry_run: bool,
) -> None:
    tag = f"{line}-{minecraft}-{release_date}"
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
        answer = input(
            f"{confirmation} {tag}? Type {confirmation} to continue: "
        ).strip()
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
    config = load_config(repo / args.config)
    line, minecraft, release_date = resolve_manage_target(repo, config, args)
    dispatch_manage_workflow(
        repo=repo,
        default_branch=config.default_branch,
        action=action,
        line=line,
        minecraft=minecraft,
        release_date=release_date,
        reason=args.reason or "",
        yes=args.yes,
        dry_run=args.dry_run,
    )
    return 0


def add_manage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--line", choices=["cloth", "anvil"])
    parser.add_argument("--minecraft", help="Minecraft version, for example 26.2")
    parser.add_argument("--date", help="Published version date in YYMMDD format")
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
        help="Path relative to repository root",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    imp = subparsers.add_parser("import", help="Import a Prism-exported .mrpack")
    imp.add_argument("mrpack", type=Path)
    imp.add_argument("--channel", required=True, choices=["release", "beta", "alpha"])
    imp.add_argument(
        "--date",
        help="Override automatic YYMMDD date; intended only for migration/backfill",
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
        if args.command == "yank":
            return manage_command(args, "yank")
        if args.command == "restore":
            return manage_command(args, "restore")
        raise WorkflowError(f"Unsupported command: {args.command}")
    except (WorkflowError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
