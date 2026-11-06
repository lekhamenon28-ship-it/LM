"""Load cross-tool credentials without including them in inventory output."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent / "credentials.json"
FIELDS = {
    "dynatrace": {"environment_url": "DT_ENV_URL", "api_token": "DT_API_TOKEN", "platform_url": "DT_PLATFORM_URL", "platform_token": "DT_PLATFORM_TOKEN"},
    "datadog": {"site": "DD_SITE", "api_key": "DD_API_KEY", "application_key": "DD_APP_KEY", "environment": "DD_ENV"},
    "zabbix": {"url": "ZBX_URL", "api_token": "ZBX_TOKEN"},
    "splunk": {"url": "SPLUNK_URL", "api_token": "SPLUNK_TOKEN"},
    "solarwinds": {"url": "SW_URL", "username": "SW_USER", "password": "SW_PASSWORD"},
}


def read_credentials(path: Path = DEFAULT_FILE, tool: str | None = None) -> dict:
    if not path.exists():
        raise RuntimeError(f"Credentials file not found: {path}. Copy credentials.example.json to credentials.json and set permissions to 600.")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise RuntimeError(f"Credentials file is readable by other users: {path}. Run chmod 600 on it.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read credentials file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Credentials file must contain a JSON object")
    if tool is not None and tool not in FIELDS:
        raise ValueError('Unknown credential tool: '+tool)
    selected = FIELDS.items() if tool is None else [(tool,FIELDS[tool])]
    for name, fields in selected:
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise RuntimeError(f"Credentials section {name} must be a JSON object")
        for field, env_name in fields.items():
            for key in (field, env_name):
                value = section.get(key, "")
                if not isinstance(value, str):
                    raise RuntimeError(f"Credentials field {name}.{key} must be a string")
    return data


def load_credentials(path: Path = DEFAULT_FILE, tool: str | None = None) -> None:
    data = read_credentials(path, tool)
    selected = FIELDS.items() if tool is None else [(tool,FIELDS[tool])]
    for tool, fields in selected:
        for field, env_name in fields.items():
            section = data.get(tool, {})
            value = section.get(field, "").strip() or section.get(env_name, "")
            if value.strip() and not os.environ.get(env_name):
                os.environ[env_name] = value.strip()


def write_credentials(data: dict, path: Path = DEFAULT_FILE, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        json.dump(data, handle, indent=2)
        handle.write("\n")


def initialize_credentials(path: Path = DEFAULT_FILE) -> None:
    template = json.loads((Path(__file__).resolve().parent / "credentials.example.json").read_text(encoding="utf-8"))
    write_credentials(template, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one credentials file for all collectors and tools.")
    parser.add_argument("--init", action="store_true", required=True, help="Create a template without overwriting an existing file")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()
    try:
        initialize_credentials(args.credentials)
    except FileExistsError:
        parser.error(f"Configuration already exists: {args.credentials}; edit it to update tool credentials.")
    print(f"Created {args.credentials.resolve()}. Edit this file to enter tool URLs and credentials; leave unused tools blank.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
