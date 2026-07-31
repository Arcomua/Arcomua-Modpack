from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# The reusable workflow checks out Main tooling separately.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpack.core import (  # noqa: E402
    BRANCH_RE,
    WorkflowError,
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
    run_git,
    validate_manifest,
    write_github_output,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify-reproducible", action="store_true")
    parser.add_argument("--api-base", default="https://api.modrinth.com/v2")
    args = parser.parse_args()

    source = args.source.resolve()
    config = load_config(args.config.resolve())

    branch = os.getenv("GITHUB_REF_NAME") or run_git(
        source, "branch", "--show-current"
    ).stdout.strip()
    match = BRANCH_RE.fullmatch(branch)
    if not match:
        raise WorkflowError(
            f"Version publication is only valid on cloth/<minecraft> or anvil/<minecraft>, got {branch!r}"
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

    full_version = normalize_manifest_for_release(
        manifest,
        product=product,
        minecraft=minecraft,
        release_date=release["date"],
    )
    write_json(manifest_path, manifest)

    tag = config.tag_pattern.format(
        line=line,
        minecraft=minecraft,
        release_date=release["date"],
    )
    prefix = config.tag_pattern.format(
        line=line,
        minecraft=minecraft,
        release_date="",
    )
    previous_tag = previous_release_tag(source, prefix=prefix, current_tag=tag)
    manual_notes = (source / "release/manual.md").read_text(
        encoding="utf-8"
    ) if (source / "release/manual.md").exists() else ""
    changelog = generate_changelog(
        source,
        current_manifest=manifest,
        previous_tag=previous_tag,
        manual_notes=manual_notes,
        lookup_metadata=not args.offline,
    )

    dist = source / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    artifact_name = (
        f"{product.filename_name}-{minecraft}-"
        f"{product.modrinth_loader.title()}-{release['date']}.mrpack"
    )
    artifact = dist / artifact_name
    build_mrpack(source / "pack", artifact)

    if args.verify_reproducible:
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
    (dist / f"{artifact_name}.sha256").write_text(
        f"{sha256}  {artifact_name}\n",
        encoding="utf-8",
        newline="\n",
    )
    changelog_path = dist / "CHANGELOG.md"
    changelog_path.write_text(changelog, encoding="utf-8", newline="\n")

    title = (
        f"{product.display_name} {minecraft} "
        f"{product.modrinth_loader.title()} {release['date']}"
    )
    metadata = {
        "branch": branch,
        "tag": tag,
        "title": title,
        "channel": release["channel"],
        "version_number": full_version,
        "artifact": str(artifact),
        "artifact_name": artifact_name,
        "sha256_file": str(dist / f"{artifact_name}.sha256"),
        "changelog_file": str(changelog_path),
        "sha512": sha512,
        "previous_tag": previous_tag,
        "project_id": product.modrinth_project_id,
    }
    write_json(dist / "release-metadata.json", metadata)
    write_github_output(
        {
            "tag": tag,
            "title": title,
            "channel": release["channel"],
            "prerelease": release["channel"] != "release",
            "artifact": str(artifact),
            "artifact_name": artifact_name,
            "sha256_file": str(dist / f"{artifact_name}.sha256"),
            "changelog_file": str(changelog_path),
            "version_number": full_version,
        }
    )

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("\nGenerated changelog:\n")
    print(changelog)

    if args.publish:
        token = os.getenv("MODRINTH_TOKEN", "").strip()
        if not token:
            raise WorkflowError("MODRINTH_TOKEN is not configured")
        publish_modrinth(
            token=token,
            project=product,
            minecraft=minecraft,
            full_version=full_version,
            channel=release["channel"],
            changelog=changelog,
            artifact=artifact,
            sha512=sha512,
            api_base=args.api_base,
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
