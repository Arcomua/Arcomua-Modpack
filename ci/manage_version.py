from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpack.core import (  # noqa: E402
    WorkflowError,
    load_config,
    modify_modrinth_version_status,
    validate_release_date,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archive or restore a published Arcomua Modrinth version."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--action", choices=["yank", "restore"], required=True)
    parser.add_argument("--line", choices=["cloth", "anvil"], required=True)
    parser.add_argument("--minecraft", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--api-base", default="https://api.modrinth.com/v2")
    args = parser.parse_args()

    token = os.getenv("MODRINTH_TOKEN", "").strip()
    if not token:
        raise WorkflowError("MODRINTH_TOKEN is not configured")

    config = load_config(args.config.resolve())
    matches = [
        product
        for product in config.products.values()
        if product.branch_line == args.line
    ]
    if len(matches) != 1:
        raise WorkflowError(f"Unknown product line: {args.line}")

    release_date = validate_release_date(args.date)
    target_status = "archived" if args.action == "yank" else "listed"
    result = modify_modrinth_version_status(
        token=token,
        project=matches[0],
        minecraft=args.minecraft,
        release_date=release_date,
        status_value=target_status,
        api_base=args.api_base,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
