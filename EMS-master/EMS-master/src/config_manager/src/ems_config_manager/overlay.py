"""Profile overlay loading for EMS configuration.

Uses full file replacement strategy: if a profile-specific config exists,
it replaces the default config entirely (no deep merge). This is deterministic
and matches the existing file structure where each profile has all 14 files.
"""

from pathlib import Path

import yaml


def load_with_profile(
    name: str, config_dir: Path, profile: str | None
) -> dict:
    """Load config YAML with optional profile overlay (full replacement).

    If profile is specified and a profile-specific file exists at
    config_dir/profiles/{profile}/{name}.yaml, that file is loaded instead
    of the default config_dir/{name}.yaml.

    Args:
        name: Config name without extension (e.g., "system_config").
        config_dir: Root config directory.
        profile: Deployment profile name, or None for default.

    Returns:
        Parsed YAML config as a dict.

    Raises:
        FileNotFoundError: If neither profile nor default file exists.
    """
    if profile:
        profile_path: Path = config_dir / "profiles" / profile / f"{name}.yaml"
        if profile_path.exists():
            return yaml.safe_load(profile_path.read_text())

    default_path: Path = config_dir / f"{name}.yaml"
    if not default_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {default_path} "
            f"(profile={profile!r} also not found)"
        )
    return yaml.safe_load(default_path.read_text())
