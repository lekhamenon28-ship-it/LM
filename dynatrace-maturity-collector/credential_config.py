"""Load cross-tool credentials without including them in inventory output."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent / "credentials.json"
FIELDS = {
    "dynatrace": {"environment_url": "DT_ENV_URL", "api_token": "DT_API_TOKEN", "platform_url": "DT_PLATFORM_URL", "platform_token": "DT_PLATFORM_TOKEN"},
    "datadog": {"site": "DD_SITE", "api_key": "DD_API_KEY", "application_key": "DD_APP_KEY"},
    "zabbix": {"url": "ZBX_URL", "api_token": "ZBX_TOKEN"},
    "splunk": {"url": "SPLUNK_URL", "api_token": "SPLUNK_TOKEN"},
    "solarwinds": {"url": "SW_URL", "username": "SW_USER", "password": "SW_PASSWORD"},
}


def load_credentials(path: Path = DEFAULT_FILE) -> None:
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
    for tool, fields in FIELDS.items():
        section = data.get(tool, {})
        if not isinstance(section, dict):
            raise RuntimeError(f"Credentials section {tool} must be a JSON object")
        for field, env_name in fields.items():
            value = section.get(field, "")
            if not isinstance(value, str):
                raise RuntimeError(f"Credentials field {tool}.{field} must be a string")
            if value.strip() and not os.environ.get(env_name):
                os.environ[env_name] = value.strip()
