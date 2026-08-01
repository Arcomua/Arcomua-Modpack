from __future__ import annotations

from pathlib import Path, PurePath, PurePosixPath

OPTIONS_PATHS = {
    "overrides/options.txt",
    "overrides/config/yosbr/options.txt",
}
_UTF8_BOM = b"\xef\xbb\xbf"


def sanitize_options_bytes(raw: bytes) -> tuple[bytes, bool]:
    has_bom = raw.startswith(_UTF8_BOM)
    payload = raw[len(_UTF8_BOM):] if has_bom else raw
    text = payload.decode("utf-8", errors="surrogateescape")

    changed = False
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            content, ending = line[:-2], "\r\n"
        elif line.endswith("\n") or line.endswith("\r"):
            content, ending = line[:-1], line[-1]
        else:
            content, ending = line, ""

        replacement = content
        if content.startswith("lastServer:"):
            replacement = "lastServer: "
        elif content.startswith("lang:"):
            replacement = "lang:en_us"

        if replacement != content:
            changed = True
        output.append(replacement + ending)

    encoded = "".join(output).encode("utf-8", errors="surrogateescape")
    if has_bom:
        encoded = _UTF8_BOM + encoded
    return encoded, changed


def sanitize_pack_options(pack_dir: Path) -> list[Path]:
    changed_paths: list[Path] = []
    for relative_text in sorted(OPTIONS_PATHS):
        relative = PurePosixPath(relative_text)
        path = pack_dir.joinpath(*relative.parts)
        if not path.is_file():
            continue
        sanitized, changed = sanitize_options_bytes(path.read_bytes())
        if changed:
            path.write_bytes(sanitized)
            changed_paths.append(path)
    return changed_paths


def pack_file_bytes(path: Path, relative: PurePath) -> bytes:
    raw = path.read_bytes()
    relative_text = "/".join(relative.parts)
    if relative_text in OPTIONS_PATHS:
        return sanitize_options_bytes(raw)[0]
    return raw
