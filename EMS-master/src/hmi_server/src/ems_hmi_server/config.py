"""HMI config loader -- reads hmi_config.yaml and returns typed dict."""

from __future__ import annotations

import yaml

DEFAULT_CONFIG_PATH: str = "config/hmi_config.yaml"


def load_hmi_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load HMI configuration from a YAML file.

    Args:
        path: Path to the hmi_config.yaml file.

    Returns:
        Parsed configuration dictionary.
    """
    with open(path, "r") as f:
        config: dict = yaml.safe_load(f)
    return config
