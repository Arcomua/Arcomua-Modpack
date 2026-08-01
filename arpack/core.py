from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

API_BASE = "https://api.modrinth.com/v2"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
CHANNELS = {"release", "beta", "alpha"}
ALLOWED_PACK_ROOTS = {
    "modrinth.index.json",
    "overrides",
    "client-overrides",
    "server-overrides",
}
RELEASE_DATE_RE = re.compile(r"^\d{6}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
BRANCH_RE = re.compile(r"^(cloth|anvil)/(.+)$")
MODRINTH_CDN_RE = re.compile(
    r"^https://cdn\.modrinth\.com/data/(?P<project>[^/]+)/versions/(?P<version>[^/]+)/"
)


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    key: str
    display_name: str
    filename_name: str
    branch_line: str
    modrinth_project_id: str
    allowed_loader_dependencies: tuple[str, ...]
    modrinth_loader: str
    loader_display_name: str
    environment: str


@dataclass(frozen=True)
class RepositoryConfig:
    default_branch: str
    branch_pattern: str
    release_timezone_offset_hours: int
    products: dict[str, Product]


@dataclass(frozen=True)
class ModEntry:
    identity: str
    project_id: str | None
    version_id: str | None
    path: str
    filename: str
    sha512: str


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_config_text(text: str) -> RepositoryConfig:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WorkflowError(f"Invalid products.toml: {exc}") from exc

    repo = raw.get("repository", {})
    products_raw = raw.get("products", {})
    if not isinstance(repo, dict) or not isinstance(products_raw, dict):
        raise WorkflowError("products.toml is missing repository/products tables")

    products: dict[str, Product] = {}
    for key, item in products_raw.items():
        if not isinstance(item, dict):
            raise WorkflowError(f"products.{key} must be a TOML table")
        products[key] = Product(
            key=key,
            display_name=str(item["display_name"]),
            filename_name=str(item["filename_name"]),
            branch_line=str(item["branch_line"]),
            modrinth_project_id=str(item["modrinth_project_id"]),
            allowed_loader_dependencies=tuple(item["allowed_loader_dependencies"]),
            modrinth_loader=str(item["modrinth_loader"]),
            loader_display_name=str(item["loader_display_name"]),
            environment=str(item["environment"]),
        )

    return RepositoryConfig(
        default_branch=str(repo.get("default_branch", "Main")),
        branch_pattern=str(repo.get("branch_pattern", "{line}/{minecraft}")),
        release_timezone_offset_hours=int(repo.get("release_timezone_offset_hours", 8)),
        products=products,
    )


def load_config(path: Path) -> RepositoryConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowError(f"Missing configuration: {path}") from exc
    return parse_config_text(text)

def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        command = "git " + " ".join(args)
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorkflowError(f"{command} failed: {detail}")
    return result


def find_repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise WorkflowError("Run this command inside the Arcomua-Modpack Git repository")
    return Path(result.stdout.strip()).resolve()


def require_clean_repo(repo: Path) -> None:
    status = run_git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise WorkflowError(
            "The Git working tree is not clean. Commit, stash, or discard existing changes first."
        )


def safe_archive_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/"):
        raise WorkflowError(f"Unsafe archive path: {name!r}")
    if ":" in path.parts[0] or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkflowError(f"Unsafe archive path: {name!r}")
    return path


def validate_manifest(manifest: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    required = {
        "formatVersion": int,
        "game": str,
        "versionId": str,
        "name": str,
        "files": list,
        "dependencies": dict,
    }
    for key, expected in required.items():
        if key not in manifest or not isinstance(manifest[key], expected):
            raise WorkflowError(f"modrinth.index.json field {key!r} is missing or invalid")

    if manifest["formatVersion"] != 1:
        raise WorkflowError("Only Modrinth mrpack formatVersion 1 is supported")
    if manifest["game"] != "minecraft":
        raise WorkflowError("The pack game must be minecraft")

    dependencies = manifest["dependencies"]
    minecraft = dependencies.get("minecraft")
    if not isinstance(minecraft, str) or not minecraft.strip():
        raise WorkflowError("dependencies.minecraft is missing")

    seen: set[str] = set()
    for index, item in enumerate(manifest["files"]):
        if not isinstance(item, dict):
            raise WorkflowError(f"files[{index}] must be an object")
        path = item.get("path")
        if not isinstance(path, str):
            raise WorkflowError(f"files[{index}].path is invalid")
        safe_archive_path(path)
        if path in seen:
            raise WorkflowError(f"Duplicate pack file path: {path}")
        seen.add(path)

        hashes = item.get("hashes")
        if not isinstance(hashes, dict):
            raise WorkflowError(f"{path}: hashes are missing")
        if not isinstance(hashes.get("sha1"), str) or len(hashes["sha1"]) != 40:
            raise WorkflowError(f"{path}: invalid SHA-1")
        if not isinstance(hashes.get("sha512"), str) or len(hashes["sha512"]) != 128:
            raise WorkflowError(f"{path}: invalid SHA-512")

        downloads = item.get("downloads")
        if not isinstance(downloads, list) or not downloads:
            raise WorkflowError(f"{path}: downloads must be a non-empty list")
        for url in downloads:
            parsed = urllib.parse.urlparse(str(url))
            if parsed.scheme != "https" or not parsed.netloc:
                raise WorkflowError(f"{path}: invalid HTTPS download URL: {url}")

    manifest["files"].sort(key=lambda item: item["path"].casefold())
    loaders = tuple(
        key for key in dependencies
        if key in {"fabric-loader", "quilt-loader", "forge", "neoforge"}
    )
    return minecraft, loaders


def detect_product(config: RepositoryConfig, loader_dependencies: Iterable[str]) -> Product:
    loader_set = set(loader_dependencies)
    matches = [
        product
        for product in config.products.values()
        if loader_set.intersection(product.allowed_loader_dependencies)
    ]
    if len(matches) != 1:
        raise WorkflowError(
            "Unable to map loader dependencies to exactly one product. "
            f"Detected: {sorted(loader_set)}"
        )
    return matches[0]


def read_release_toml(path: Path) -> dict[str, str]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Missing release metadata: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise WorkflowError(f"Invalid release TOML: {exc}") from exc
    release = value.get("release")
    if not isinstance(release, dict):
        raise WorkflowError("release/release.toml must contain a [release] table")
    result = {key: str(val) for key, val in release.items()}
    channel = result.get("channel")
    release_date = result.get("date") or result.get("id")
    if channel not in CHANNELS:
        raise WorkflowError("release.channel must be release, beta, or alpha")
    if not release_date or not RELEASE_DATE_RE.fullmatch(release_date):
        raise WorkflowError("release.date must use the YYMMDD format")
    result["date"] = release_date
    result["release_id"] = validate_release_id(result.get("release_id", ""))
    return result

def current_release_date(config: RepositoryConfig) -> str:
    offset = timezone(timedelta(hours=config.release_timezone_offset_hours))
    return datetime.now(offset).strftime("%y%m%d")


def validate_release_date(value: str) -> str:
    if not RELEASE_DATE_RE.fullmatch(value):
        raise WorkflowError("Release date must use the YYMMDD format")
    try:
        datetime.strptime(value, "%y%m%d")
    except ValueError as exc:
        raise WorkflowError(f"Invalid release date: {value}") from exc
    return value


def validate_release_id(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    if not RELEASE_ID_RE.fullmatch(normalized):
        raise WorkflowError(
            "Release ID must be 1-32 characters and use only letters, numbers, dot, underscore, or hyphen"
        )
    return normalized


def write_release_toml(
    path: Path,
    *,
    release_date: str,
    release_id: str,
    channel: str,
    product: Product,
    minecraft: str,
) -> None:
    validate_release_date(release_date)
    release_id = validate_release_id(release_id)
    content = (
        "[release]\n"
        f'date = "{release_date}"\n'
        f'release_id = "{release_id}"\n'
        f'channel = "{channel}"\n'
        f'product = "{product.key}"\n'
        f'minecraft = "{minecraft}"\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def version_number(
    product: Product,
    minecraft: str,
    release_date: str,
    release_id: str = "",
) -> str:
    validate_release_date(release_date)
    release_id = validate_release_id(release_id)
    base = f"{minecraft}-{product.modrinth_loader}-{release_date}"
    return f"{base}-{release_id}" if release_id else base


def version_display_name(
    product: Product,
    minecraft: str,
    release_date: str,
    release_id: str = "",
) -> str:
    validate_release_date(release_date)
    release_id = validate_release_id(release_id)
    parts = [minecraft, product.loader_display_name, release_date]
    if release_id:
        parts.extend(release_id.split("-"))
    return " ".join(parts)


def release_tag(
    line: str,
    minecraft: str,
    release_date: str,
    release_id: str = "",
) -> str:
    validate_release_date(release_date)
    release_id = validate_release_id(release_id)
    base = f"{line}-{minecraft}-{release_date}"
    return f"{base}-{release_id}" if release_id else base


def release_tag_prefix(line: str, minecraft: str) -> str:
    return f"{line}-{minecraft}-"


def artifact_filename(
    product: Product,
    minecraft: str,
    release_date: str,
    release_id: str = "",
) -> str:
    suffix = f"-{validate_release_id(release_id)}" if release_id else ""
    return (
        f"{product.filename_name}-{minecraft}-{product.loader_display_name}-"
        f"{release_date}{suffix}.mrpack"
    )


def normalize_manifest_for_release(
    manifest: dict[str, Any],
    *,
    product: Product,
    minecraft: str,
    release_date: str,
    release_id: str = "",
) -> str:
    full_version = version_number(product, minecraft, release_date, release_id)
    manifest["name"] = product.display_name
    manifest["versionId"] = full_version
    return full_version

def extract_mrpack(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file() or source.suffix.casefold() != ".mrpack":
        raise WorkflowError(f"Expected a .mrpack file: {source}")

    with zipfile.ZipFile(source, "r") as archive:
        members = archive.infolist()
        normalized_members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        roots: set[str] = set()

        for member in members:
            path = safe_archive_path(member.filename)
            roots.add(path.parts[0])
            normalized_members.append((member, path))

        unexpected = roots - ALLOWED_PACK_ROOTS
        if unexpected:
            raise WorkflowError(
                "The exported pack contains unexpected root items: "
                + ", ".join(sorted(unexpected))
            )
        if "modrinth.index.json" not in roots:
            raise WorkflowError("The mrpack root does not contain modrinth.index.json")

        temp = destination.parent / f".{destination.name}.import-{uuid.uuid4().hex}"
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)

        try:
            for member, rel in normalized_members:
                target = temp.joinpath(*rel.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

            manifest = read_json(temp / "modrinth.index.json")
            validate_manifest(manifest)
            write_json(temp / "modrinth.index.json", manifest)

            if destination.exists():
                shutil.rmtree(destination)
            temp.replace(destination)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    return read_json(destination / "modrinth.index.json")


def iter_pack_files(pack_dir: Path):
    for path in sorted(pack_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise WorkflowError(f"Symbolic links are not allowed in pack/: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(pack_dir)
        if rel.as_posix() == ".gitkeep":
            continue
        safe_archive_path(rel.as_posix())
        if rel.parts[0] not in ALLOWED_PACK_ROOTS:
            raise WorkflowError(f"Unexpected root item in pack/: {rel}")
        yield path, rel


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mrpack(pack_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for source, rel in iter_pack_files(pack_dir):
            info = zipfile.ZipInfo(rel.as_posix(), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, source.read_bytes())


def manifest_mods(manifest: dict[str, Any]) -> dict[str, ModEntry]:
    result: dict[str, ModEntry] = {}
    for item in manifest.get("files", []):
        path = str(item.get("path", ""))
        if not path.startswith("mods/"):
            continue

        project_id = None
        version_id = None
        for url in item.get("downloads", []):
            match = MODRINTH_CDN_RE.match(str(url))
            if match:
                project_id = match.group("project")
                version_id = match.group("version")
                break

        sha512 = str(item.get("hashes", {}).get("sha512", ""))
        filename = PurePosixPath(path).name
        identity = project_id or f"external:{path.casefold()}"
        result[identity] = ModEntry(
            identity=identity,
            project_id=project_id,
            version_id=version_id,
            path=path,
            filename=filename,
            sha512=sha512,
        )
    return result


def api_get_json(url: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Arcomua-Modpack-Workflow/0.1 (GitHub Actions)",
    }
    if token:
        headers["Authorization"] = token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WorkflowError(f"Modrinth API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowError(f"Modrinth API request failed: {exc}") from exc


def batched(values: list[str], size: int = 80):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def get_modrinth_metadata(
    project_ids: set[str],
    version_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    projects: dict[str, dict[str, Any]] = {}
    versions: dict[str, dict[str, Any]] = {}

    for group in batched(sorted(project_ids)):
        query = urllib.parse.urlencode({"ids": json.dumps(group)})
        payload = api_get_json(f"{API_BASE}/projects?{query}")
        for item in payload:
            projects[str(item["id"])] = item

    for group in batched(sorted(version_ids)):
        query = urllib.parse.urlencode({"ids": json.dumps(group)})
        payload = api_get_json(f"{API_BASE}/versions?{query}")
        for item in payload:
            versions[str(item["id"])] = item

    return projects, versions


def previous_release_tag(
    repo: Path,
    *,
    prefix: str,
    current_tag: str,
) -> str | None:
    if run_git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        return None
    result = run_git(
        repo,
        "for-each-ref",
        "--merged=HEAD",
        "--sort=-creatordate",
        "--format=%(refname:short)",
        f"refs/tags/{prefix}*",
    )
    for tag in result.stdout.splitlines():
        tag = tag.strip()
        if tag and tag != current_tag:
            return tag
    return None


def manifest_from_git(repo: Path, tag: str) -> dict[str, Any]:
    result = run_git(repo, "show", f"{tag}:pack/modrinth.index.json")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Previous manifest at {tag} is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"Previous manifest at {tag} is not an object")
    return value


def render_mod(entry: ModEntry, projects: dict[str, dict[str, Any]], versions: dict[str, dict[str, Any]]) -> tuple[str, str]:
    if entry.project_id and entry.project_id in projects:
        project = projects[entry.project_id]
        name = str(project.get("title") or project.get("slug") or entry.filename)
        slug = str(project.get("slug") or entry.project_id)
        label = f"[{name}](https://modrinth.com/mod/{slug})"
    else:
        label = f"`{entry.filename}`"

    version = entry.filename
    if entry.version_id and entry.version_id in versions:
        version = str(versions[entry.version_id].get("version_number") or entry.filename)
    return label, version


def generate_changelog(
    repo: Path,
    *,
    current_manifest: dict[str, Any],
    previous_tag: str | None,
    manual_notes: str,
    lookup_metadata: bool = True,
) -> str:
    if previous_tag is None:
        return "Initial version\n"

    old_manifest = manifest_from_git(repo, previous_tag)
    old = manifest_mods(old_manifest)
    new = manifest_mods(current_manifest)

    added_keys = sorted(new.keys() - old.keys())
    removed_keys = sorted(old.keys() - new.keys())
    shared_keys = sorted(new.keys() & old.keys())
    updated_keys = [
        key
        for key in shared_keys
        if old[key].sha512 != new[key].sha512 or old[key].version_id != new[key].version_id
    ]

    project_ids = {
        entry.project_id
        for entry in [*old.values(), *new.values()]
        if entry.project_id
    }
    version_ids = {
        entry.version_id
        for entry in [*old.values(), *new.values()]
        if entry.version_id
    }

    if lookup_metadata:
        try:
            projects, versions = get_modrinth_metadata(project_ids, version_ids)
        except WorkflowError:
            # The release can still proceed if Modrinth metadata lookup is temporarily unavailable.
            projects, versions = {}, {}
    else:
        projects, versions = {}, {}

    sections: list[str] = []

    if added_keys or updated_keys or removed_keys:
        sections.append("## Mod changes")

    if added_keys:
        lines = ["### Added"]
        for key in added_keys:
            label, version = render_mod(new[key], projects, versions)
            lines.append(f"- {label} `{version}`")
        sections.append("\n".join(lines))

    if updated_keys:
        lines = ["### Updated"]
        for key in updated_keys:
            label, old_version = render_mod(old[key], projects, versions)
            _, new_version = render_mod(new[key], projects, versions)
            lines.append(f"- {label}: `{old_version}` → `{new_version}`")
        sections.append("\n".join(lines))

    if removed_keys:
        lines = ["### Removed"]
        for key in removed_keys:
            label, version = render_mod(old[key], projects, versions)
            lines.append(f"- {label} `{version}`")
        sections.append("\n".join(lines))

    manual_notes = manual_notes.strip()
    if manual_notes:
        sections.append("## Maintainer notes\n\n" + manual_notes)

    if not sections:
        return "No mod changes detected.\n"

    return "\n\n".join(sections).rstrip() + "\n"


def encode_multipart(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> tuple[bytes, str]:
    boundary = f"----ArcomuaBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n'.encode())
        chunks.append(b"Content-Type: application/json; charset=utf-8\r\n\r\n")
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
        ).encode()
    )
    chunks.append(b"Content-Type: application/x-modrinth-modpack+zip\r\n\r\n")
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def api_request(
    url: str,
    *,
    token: str,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, Any]:
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "Arcomua-Modpack-Workflow/0.1 (GitHub Actions)",
    }
    if content_type:
        headers["Content-Type"] = content_type

    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8")) if raw else None
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise WorkflowError(f"Modrinth API request failed: {exc}") from exc


def publish_modrinth(
    *,
    token: str,
    project: Product,
    minecraft: str,
    full_version: str,
    release_date: str,
    release_id: str,
    channel: str,
    changelog: str,
    artifact: Path,
    sha512: str,
    api_base: str = API_BASE,
) -> None:
    project_id = urllib.parse.quote(project.modrinth_project_id, safe="")
    version_number = urllib.parse.quote(full_version, safe="")
    status, existing = api_request(
        f"{api_base.rstrip('/')}/project/{project_id}/version/{version_number}",
        token=token,
    )

    if status == 200:
        remote_hashes = {
            str(file.get("hashes", {}).get("sha512", ""))
            for file in existing.get("files", [])
            if isinstance(file, dict)
        }
        if sha512 in remote_hashes:
            print("Modrinth already contains the identical version; upload skipped.")
            return
        raise WorkflowError(
            "Modrinth already contains this version number with a different file. "
            "This version number already exists with different content. Use a different --release-id instead of overwriting a published version."
        )
    if status != 404:
        raise WorkflowError(f"Unable to query Modrinth version (HTTP {status}): {existing}")

    data = {
        "name": version_display_name(project, minecraft, release_date, release_id),
        "version_number": full_version,
        "changelog": changelog,
        "dependencies": [],
        "game_versions": [minecraft],
        "version_type": channel,
        "loaders": [project.modrinth_loader],
        "featured": channel == "release",
        "status": "listed",
        "project_id": project.modrinth_project_id,
        "file_parts": ["pack"],
        "primary_file": "pack",
        "environment": project.environment,
    }
    body, content_type = encode_multipart(
        {"data": json.dumps(data, ensure_ascii=False, separators=(",", ":"))},
        "pack",
        artifact,
    )
    status, payload = api_request(
        f"{api_base.rstrip('/')}/version",
        token=token,
        method="POST",
        body=body,
        content_type=content_type,
    )
    if status != 200:
        raise WorkflowError(f"Modrinth publication failed (HTTP {status}): {payload}")
    print(f"Published to Modrinth: version_id={payload.get('id')}")


def modify_modrinth_version_status(
    *,
    token: str,
    project: Product,
    minecraft: str,
    release_date: str,
    release_id: str,
    status_value: str,
    api_base: str = API_BASE,
) -> dict[str, Any]:
    if status_value not in {"archived", "listed"}:
        raise WorkflowError(f"Unsupported Modrinth status transition: {status_value}")

    full_version = version_number(project, minecraft, release_date, release_id)
    project_id = urllib.parse.quote(project.modrinth_project_id, safe="")
    version_number_encoded = urllib.parse.quote(full_version, safe="")
    status, existing = api_request(
        f"{api_base.rstrip('/')}/project/{project_id}/version/{version_number_encoded}",
        token=token,
    )
    if status == 404:
        raise WorkflowError(f"Modrinth version not found: {full_version}")
    if status != 200 or not isinstance(existing, dict):
        raise WorkflowError(
            f"Unable to query Modrinth version (HTTP {status}): {existing}"
        )

    version_id = str(existing.get("id", "")).strip()
    if not version_id:
        raise WorkflowError("Modrinth returned a version without an ID")

    payload: dict[str, Any] = {"status": status_value}
    if status_value == "archived":
        payload["featured"] = False
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    status, response = api_request(
        f"{api_base.rstrip('/')}/version/{urllib.parse.quote(version_id, safe='')}",
        token=token,
        method="PATCH",
        body=body,
        content_type="application/json",
    )
    if status != 204:
        raise WorkflowError(
            f"Unable to change Modrinth version status (HTTP {status}): {response}"
        )

    return {
        "version_id": version_id,
        "version_number": full_version,
        "status": status_value,
        "project_id": project.modrinth_project_id,
    }


def write_github_output(values: dict[str, str | bool]) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            handle.write(f"{key}={value}\n")
