"""EMS monorepo scaffold smoke tests.

Verifies that:
- All 12 module directories exist under src/
- All 12 Python packages are importable
- All packages have the expected version string
"""

import importlib
from pathlib import Path


# Root of the repository (two levels up from tests/)
REPO_ROOT = Path(__file__).parent.parent


def test_all_module_dirs_exist() -> None:
    """Assert all 12 module directories exist under src/."""
    module_dirs = [
        "src/common",
        "src/safety_manager",
        "src/comm_manager",
        "src/data_manager",
        "src/logger",
        "src/config_manager",
        "src/control_manager",
        "src/alarm_manager",
        "src/scheduler",
        "src/diagnostics",
        "src/cloud_manager",
        "src/ota_manager",
        "src/hmi_server",
    ]
    for rel_path in module_dirs:
        full_path = REPO_ROOT / rel_path
        assert full_path.is_dir(), f"Expected module directory missing: {full_path}"


def test_common_python_importable() -> None:
    """Verify ems_common package is importable."""
    import ems_common  # noqa: F401


def test_python_modules_importable() -> None:
    """Verify all 12 Python packages can be imported."""
    packages = [
        "ems_common",
        "ems_config_manager",
        "ems_alarm_manager",
        "ems_scheduler",
        "ems_diagnostics",
        "ems_cloud_manager",
        "ems_ota_manager",
        "ems_hmi_server",
        "ems_comm_manager",
        "ems_data_manager",
        "ems_logger",
        "ems_control_manager",
    ]
    for pkg_name in packages:
        mod = importlib.import_module(pkg_name)
        assert mod is not None, f"Failed to import {pkg_name}"


def test_version_strings() -> None:
    """Verify all 12 Python packages export __version__ == '0.1.0'."""
    packages = [
        "ems_common",
        "ems_config_manager",
        "ems_alarm_manager",
        "ems_scheduler",
        "ems_diagnostics",
        "ems_cloud_manager",
        "ems_ota_manager",
        "ems_hmi_server",
        "ems_comm_manager",
        "ems_data_manager",
        "ems_logger",
        "ems_control_manager",
    ]
    for pkg_name in packages:
        mod = importlib.import_module(pkg_name)
        assert hasattr(mod, "__version__"), f"{pkg_name} missing __version__"
        assert mod.__version__ == "0.1.0", (
            f"{pkg_name}.__version__ expected '0.1.0', got '{mod.__version__}'"
        )
