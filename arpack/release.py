from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .core import (
    BRANCH_RE,
    RepositoryConfig,
    WorkflowError,
    artifact_filename,
    build_mrpack,
    detect_product,
    file_digest,
    generate_changelog,
    load_config,
    normalize_manifest_for_release,
    previous_release_tag,
    publish_modrinth,
    read_json,
    read_release_toml,
    release_tag,
    release_tag_prefix,
    run_git,
    validate_manifest,
    version_display_name,
    write_github_output,
    write_json,
)


def run_release(
    *,
    source: Path,
    config: RepositoryConfig,
    publish: bool = False,
    offline: bool = False,
    verify_reproducible: bool = False,
    api_base: str = "https://api.modrinth.com/v2",
) -> dict[str, object]:
    source = source.resolve()
    branch = os.getenv("GITHUB_REF_NAME") or run_git(
        source, "branch", "--show-current"
    ).stdout.strip()
    match = BRANCH_RE.fullmatch(branch)
    if not match:
        raise WorkflowError(
            "Version publication is only valid on cloth/<minecraft> or "
            f"anvil/<minecraft>, got {branch!r}"
        )

    line, branch_minecraft = match.groups()
    release = read_release_toml(source / "release/release.toml")
    manifest_path = source / "pack/modrinth.index.json"
    manifest = read_json(manifest_path)
    minecraft, loaders = validate_manifest(manifest)
    product = detect_product(config, loaders)

    if product.branch_line != line:
        raise WorkflowError(
            f"Branch line {line!r} does not match loader product {product.key!r}"
        )
    if minecraft != branch_minecraft:
        raise WorkflowError(
            f"Branch Minecraft {branch_minecraft!r} does not match manifest {minecraft!r}"
        )
    if release.get("product") != product.key:
        raise WorkflowError("release.product does not match the detected product")
    if release.get("minecraft") != minecraft:
        raise WorkflowError("release.minecraft does not match the pack manifest")

    release_id = release.get("release_id", "")
    full_version = normalize_manifest_for_release(
        manifest,
        product=product,
        minecraft=minecraft,
        release_date=release["date"],
        release_id=release_id,
    )
    write_json(manifest_path, manifest)

    tag = release_tag(line, minecraft, release["date"], release_id)
    prefix = release_tag_prefix(line, minecraft)
    previous_tag = previous_release_tag(source, prefix=prefix, current_tag=tag)
    manual_notes = (
        (source / "release/manual.md").read_text(encoding="utf-8")
        if (source / "release/manual.md").exists()
        else ""
    )
    changelog = generate_changelog(
        source,
        current_manifest=manifest,
        previous_tag=previous_tag,
        manual_notes=manual_notes,
        lookup_metadata=not offline,
    )

    dist = source / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    artifact_name = artifact_filename(
        product,
        minecraft,
        release["date"],
        release_id,
    )
    artifact = dist / artifact_name
    build_mrpack(source / "pack", artifact)

    if verify_reproducible:
        with tempfile.TemporaryDirectory(prefix="arcomua-repro-") as temp_dir:
            second = Path(temp_dir) / artifact_name
            build_mrpack(source / "pack", second)
            first_hash = file_digest(artifact, "sha256")
            second_hash = file_digest(second, "sha256")
            if first_hash != second_hash:
                raise WorkflowError(
                    "The same source produced different mrpack hashes. "
                    "The build is not reproducible."
                )

    sha256 = file_digest(artifact, "sha256")
    sha512 = file_digest(artifact, "sha512")
    sha256_file = dist / f"{artifact_name}.sha256"
    sha256_file.write_text(
        f"{sha256}  {artifact_name}\n",
        encoding="utf-8",
        newline="\n",
    )
    changelog_path = dist / "CHANGELOG.md"
    changelog_path.write_text(changelog, encoding="utf-8", newline="\n")

    subtitle = version_display_name(
        product,
        minecraft,
        release["date"],
        release_id,
    )
    github_title = f"{product.display_name} {subtitle}"
    metadata: dict[str, object] = {
        "branch": branch,
        "tag": tag,
        "title": github_title,
        "modrinth_version_name": subtitle,
        "channel": release["channel"],
        "version_number": full_version,
        "release_id": release_id,
        "artifact": str(artifact),
        "artifact_name": artifact_name,
        "sha256_file": str(sha256_file),
        "changelog_file": str(changelog_path),
        "sha512": sha512,
        "previous_tag": previous_tag,
        "project_id": product.modrinth_project_id,
    }
    write_json(dist / "release-metadata.json", metadata)
    write_github_output(
        {
            "tag": tag,
            "title": github_title,
            "channel": release["channel"],
            "prerelease": release["channel"] != "release",
            "artifact": str(artifact),
            "artifact_name": artifact_name,
            "sha256_file": str(sha256_file),
            "changelog_file": str(changelog_path),
            "version_number": full_version,
        }
    )

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("\nGenerated changelog:\n")
    print(changelog)

    if publish:
        token = os.getenv("MODRINTH_TOKEN", "").strip()
        if not token:
            raise WorkflowError("MODRINTH_TOKEN is not configured")
        publish_modrinth(
            token=token,
            project=product,
            minecraft=minecraft,
            full_version=full_version,
            release_date=release["date"],
            release_id=release_id,
            channel=release["channel"],
            changelog=changelog,
            artifact=artifact,
            sha512=sha512,
            api_base=api_base,
        )

    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify-reproducible", action="store_true")
    parser.add_argument("--api-base", default="https://api.modrinth.com/v2")
    args = parser.parse_args(argv)

    try:
        run_release(
            source=args.source,
            config=load_config(args.config.resolve()),
            publish=args.publish,
            offline=args.offline,
            verify_reproducible=args.verify_reproducible,
            api_base=args.api_base,
        )
        return 0
    except WorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
