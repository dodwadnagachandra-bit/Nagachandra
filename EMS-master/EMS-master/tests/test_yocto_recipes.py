"""Validation tests for Yocto meta-ems layer recipe files.

All tests are pure file-content checks — no bitbake execution needed.
Uses the same _find_repo_root pattern as diagnostics tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------


def _find_repo_root(start: str) -> str:
    """Walk up until a directory containing [tool.uv.workspace] pyproject.toml is found."""
    current: str = os.path.abspath(start)
    for _ in range(10):
        candidate: str = os.path.join(current, "pyproject.toml")
        if os.path.isfile(candidate):
            with open(candidate) as fh:
                contents: str = fh.read()
            if "[tool.uv.workspace]" in contents:
                return current
        parent: str = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise RuntimeError(f"Could not find repo root from {start!r}")


REPO_ROOT: Path = Path(_find_repo_root(str(Path(__file__).parent)))
META_EMS: Path = REPO_ROOT / "yocto" / "meta-ems"
RECIPES_EMS: Path = META_EMS / "recipes-ems"
RECIPES_CORE: Path = META_EMS / "recipes-core"
RECIPES_SUPPORT: Path = META_EMS / "recipes-support"
DEPLOY_TMPFILES: Path = REPO_ROOT / "deploy" / "tmpfiles"
CMAKE_TOOLCHAINS: Path = REPO_ROOT / "cmake" / "toolchains"


# ---------------------------------------------------------------------------
# Layer conf tests
# ---------------------------------------------------------------------------


class TestLayerConf:
    """Validate the meta-ems layer.conf file."""

    def test_layer_conf_exists(self) -> None:
        """layer.conf must exist at yocto/meta-ems/conf/layer.conf."""
        conf: Path = META_EMS / "conf" / "layer.conf"
        assert conf.exists(), f"Missing: {conf}"

    def test_layer_conf_scarthgap(self) -> None:
        """layer.conf must declare Scarthgap compatibility."""
        conf: Path = META_EMS / "conf" / "layer.conf"
        contents: str = conf.read_text()
        assert "scarthgap" in contents, "LAYERSERIES_COMPAT must include 'scarthgap'"
        assert "LAYERSERIES_COMPAT" in contents, "Missing LAYERSERIES_COMPAT assignment"

    def test_layer_conf_dependencies(self) -> None:
        """layer.conf must declare dependencies on meta-ti-bsp, meta-oe, meta-python."""
        conf: Path = META_EMS / "conf" / "layer.conf"
        contents: str = conf.read_text()
        assert "LAYERDEPENDS" in contents, "Missing LAYERDEPENDS"
        assert "meta-ti-bsp" in contents, "Missing meta-ti-bsp in LAYERDEPENDS"
        assert "meta-oe" in contents, "Missing meta-oe in LAYERDEPENDS"
        assert "meta-python" in contents, "Missing meta-python in LAYERDEPENDS"


# ---------------------------------------------------------------------------
# C recipe tests
# ---------------------------------------------------------------------------


class TestCRecipes:
    """Validate that all 5 C binary .bb files exist in recipes-ems/."""

    C_RECIPES: list[tuple[str, str]] = [
        ("ems-common-c", "ems-common-c_0.1.0.bb"),
        ("safety-manager", "safety-manager_0.1.0.bb"),
        ("comm-manager-c", "comm-manager-c_0.1.0.bb"),
        ("data-manager-c", "data-manager-c_0.1.0.bb"),
        ("control-manager-c", "control-manager-c_0.1.0.bb"),
    ]

    def test_all_c_recipes_exist(self) -> None:
        """All 5 C binary .bb files must exist in recipes-ems/."""
        missing: list[str] = []
        for package_dir, filename in self.C_RECIPES:
            recipe: Path = RECIPES_EMS / package_dir / filename
            if not recipe.exists():
                missing.append(str(recipe.relative_to(REPO_ROOT)))
        assert not missing, f"Missing C recipe files:\n" + "\n".join(missing)

    @pytest.mark.parametrize("package_dir,filename", C_RECIPES)
    def test_c_recipe_cmake_externalsrc(self, package_dir: str, filename: str) -> None:
        """Each C recipe must inherit cmake externalsrc."""
        recipe: Path = RECIPES_EMS / package_dir / filename
        if not recipe.exists():
            pytest.skip(f"{recipe.name} does not exist")
        contents: str = recipe.read_text()
        assert "inherit cmake externalsrc" in contents, (
            f"{filename}: missing 'inherit cmake externalsrc'"
        )


# ---------------------------------------------------------------------------
# Image recipe tests
# ---------------------------------------------------------------------------


class TestImageRecipe:
    """Validate the core-image-ems.bb production image recipe."""

    IMAGE_RECIPE: Path = RECIPES_CORE / "images" / "core-image-ems.bb"

    def test_image_recipe_readonly(self) -> None:
        """core-image-ems.bb must enable read-only-rootfs in IMAGE_FEATURES."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "read-only-rootfs" in contents, (
            "IMAGE_FEATURES must include 'read-only-rootfs'"
        )

    def test_image_recipe_python_deps(self) -> None:
        """core-image-ems.bb must install python3, python3-modules, python3-venv."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "python3" in contents, "Missing python3 in IMAGE_INSTALL"
        assert "python3-modules" in contents, "Missing python3-modules in IMAGE_INSTALL"
        assert "python3-venv" in contents, "Missing python3-venv in IMAGE_INSTALL"

    def test_image_recipe_venv_postprocess(self) -> None:
        """core-image-ems.bb must define ROOTFS_POSTPROCESS_COMMAND with venv creation."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "ROOTFS_POSTPROCESS_COMMAND" in contents, (
            "Missing ROOTFS_POSTPROCESS_COMMAND"
        )
        assert "create_ems_venv" in contents, (
            "ROOTFS_POSTPROCESS_COMMAND must reference create_ems_venv"
        )

    def test_image_recipe_boot_script(self) -> None:
        """core-image-ems.bb must include ems-boot-script in IMAGE_INSTALL."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "ems-boot-script" in contents, (
            "IMAGE_INSTALL must include 'ems-boot-script'"
        )

    def test_image_recipe_certs(self) -> None:
        """core-image-ems.bb must include ems-certs in IMAGE_INSTALL."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "ems-certs" in contents, "IMAGE_INSTALL must include 'ems-certs'"

    def test_image_recipe_boot_files(self) -> None:
        """core-image-ems.bb must include boot.scr in IMAGE_BOOT_FILES."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "IMAGE_BOOT_FILES" in contents, "Missing IMAGE_BOOT_FILES"
        assert "boot.scr" in contents, "IMAGE_BOOT_FILES must include 'boot.scr'"

    def test_image_recipe_safety_manager(self) -> None:
        """core-image-ems.bb must install safety-manager package."""
        contents: str = self.IMAGE_RECIPE.read_text()
        assert "safety-manager" in contents, (
            "IMAGE_INSTALL must include 'safety-manager'"
        )


# ---------------------------------------------------------------------------
# fstab / base-files bbappend tests
# ---------------------------------------------------------------------------


class TestFstabBbappend:
    """Validate the base-files bbappend adds the required fstab entries."""

    BBAPPEND: Path = RECIPES_CORE / "base-files" / "base-files_%.bbappend"

    def test_fstab_tmpfs_tmp(self) -> None:
        """base-files bbappend must add tmpfs entry for /tmp (50m)."""
        contents: str = self.BBAPPEND.read_text()
        assert "/tmp" in contents, "Missing /tmp tmpfs entry"
        assert "50m" in contents or "size=50m" in contents, (
            "Missing 50m size for /tmp tmpfs"
        )

    def test_fstab_tmpfs_var_run(self) -> None:
        """base-files bbappend must add tmpfs entry for /var/run (10m)."""
        contents: str = self.BBAPPEND.read_text()
        assert "/var/run" in contents, "Missing /var/run tmpfs entry"
        assert "10m" in contents or "size=10m" in contents, (
            "Missing 10m size for /var/run tmpfs"
        )

    def test_fstab_tmpfs_run_ems(self) -> None:
        """base-files bbappend must add tmpfs entry for /run/ems (5m)."""
        contents: str = self.BBAPPEND.read_text()
        assert "/run/ems" in contents, "Missing /run/ems tmpfs entry"
        assert "5m" in contents or "size=5m" in contents, (
            "Missing 5m size for /run/ems tmpfs"
        )

    def test_fstab_tmpfs_journal(self) -> None:
        """base-files bbappend must add tmpfs entry for /var/log/journal (50m)."""
        contents: str = self.BBAPPEND.read_text()
        assert "/var/log/journal" in contents, "Missing /var/log/journal tmpfs entry"
        # 50m is shared with /tmp entry; journal-specific check
        assert "journal" in contents, "Missing journal mount point"

    def test_fstab_ssd_data(self) -> None:
        """base-files bbappend must add /dev/nvme0n1p1 mounted at /data as ext4 rw."""
        contents: str = self.BBAPPEND.read_text()
        assert "/dev/nvme0n1p1" in contents, "Missing /dev/nvme0n1p1 SSD device"
        assert "/data" in contents, "Missing /data mount point"
        assert "ext4" in contents, "Missing ext4 filesystem type"


# ---------------------------------------------------------------------------
# Supporting recipe tests
# ---------------------------------------------------------------------------


class TestSupportingRecipes:
    """Validate that supporting recipe files exist."""

    def test_systemd_units_recipe_exists(self) -> None:
        """ems-systemd-units recipe must exist in recipes-ems/."""
        recipe: Path = RECIPES_EMS / "ems-systemd-units" / "ems-systemd-units_0.1.0.bb"
        assert recipe.exists(), f"Missing: {recipe.relative_to(REPO_ROOT)}"

    def test_frontend_recipe_exists(self) -> None:
        """ems-frontend recipe must install to /opt/ems/frontend/dist/."""
        recipe: Path = RECIPES_EMS / "ems-frontend" / "ems-frontend_0.1.0.bb"
        assert recipe.exists(), f"Missing: {recipe.relative_to(REPO_ROOT)}"
        contents: str = recipe.read_text()
        assert "/opt/ems/frontend/dist" in contents, (
            "ems-frontend recipe must install to /opt/ems/frontend/dist/"
        )


# ---------------------------------------------------------------------------
# CMake toolchain test
# ---------------------------------------------------------------------------


class TestCMakeToolchain:
    """Validate cmake/toolchains/aarch64-linux.cmake retains the commented sysroot."""

    def test_cmake_sysroot_comment(self) -> None:
        """aarch64-linux.cmake must have CMAKE_SYSROOT commented out (for Yocto SDK)."""
        toolchain: Path = CMAKE_TOOLCHAINS / "aarch64-linux.cmake"
        assert toolchain.exists(), f"Missing: {toolchain.relative_to(REPO_ROOT)}"
        contents: str = toolchain.read_text()
        # Must be present as a comment (not active) — Yocto SDK uncomments this
        assert "CMAKE_SYSROOT" in contents, "Missing CMAKE_SYSROOT reference"
        # The line with CMAKE_SYSROOT must be commented
        for line in contents.splitlines():
            if "CMAKE_SYSROOT" in line and "set(" in line:
                assert line.strip().startswith("#"), (
                    "CMAKE_SYSROOT set() line must be commented out for Yocto SDK"
                )
