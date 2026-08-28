"""Tests for mTLS certificate provisioning workflow.

Tests cover:
- gen-device-cert.sh: Outputs ca.crt, device.crt, device.key
- device.key permissions: 600
- device.crt CN: {site_id}-{serial_number} format
- device.crt subject O=ReVx-Energy, OU=EMS
- ca.crt in output matches provided CA cert
- CA private key NOT in output directory
- No .csr file remains after generation
- device.crt is verifiable against ca.crt
- Script rejects missing args
- ems-certs Yocto recipe exists and installs to /etc/ems/certs/
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (pyproject.toml with [tool.uv.workspace])."""
    candidate: Path = Path(__file__).resolve().parent
    while candidate != candidate.parent:
        marker: Path = candidate / "pyproject.toml"
        if marker.exists() and "[tool.uv.workspace]" in marker.read_text():
            return candidate
        candidate = candidate.parent
    raise RuntimeError("Cannot locate repository root (pyproject.toml with [tool.uv.workspace])")


REPO_ROOT: Path = _find_repo_root()
GEN_DEVICE_CERT: Path = REPO_ROOT / "tools" / "gen-device-cert.sh"
GEN_CA: Path = REPO_ROOT / "tools" / "gen-ca.sh"
CERTS_RECIPE: Path = (
    REPO_ROOT
    / "yocto"
    / "meta-ems"
    / "recipes-ems"
    / "ems-certs"
    / "ems-certs_0.1.0.bb"
)

OPENSSL_AVAILABLE: bool = shutil.which("openssl") is not None

skip_no_openssl = pytest.mark.skipif(
    not OPENSSL_AVAILABLE, reason="openssl CLI not available"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ca_dir(tmp_path: Path) -> Path:
    """Generate a test CA (ca.key + ca.crt) using openssl."""
    ca_path: Path = tmp_path / "ca"
    ca_path.mkdir()
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(ca_path / "ca.key"),
            "-out",
            str(ca_path / "ca.crt"),
            "-sha256",
            "-days",
            "3650",
            "-nodes",
            "-subj",
            "/CN=ReVx-EMS-CA/O=ReVx-Energy",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"CA generation failed: {result.stderr}"
    return ca_path


@pytest.fixture
def cert_output_dir(tmp_path: Path) -> Path:
    """Empty directory for gen-device-cert.sh output."""
    out: Path = tmp_path / "out"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@skip_no_openssl
def test_gen_device_cert_outputs(ca_dir: Path, cert_output_dir: Path) -> None:
    """Running gen-device-cert.sh produces ca.crt, device.crt, device.key in output dir."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"gen-device-cert.sh failed: {result.stderr}\n{result.stdout}"
    assert (cert_output_dir / "ca.crt").exists(), "ca.crt not found in output"
    assert (cert_output_dir / "device.crt").exists(), "device.crt not found in output"
    assert (cert_output_dir / "device.key").exists(), "device.key not found in output"


@skip_no_openssl
def test_device_key_permissions(ca_dir: Path, cert_output_dir: Path) -> None:
    """device.key has file permissions 600 (owner read/write only)."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    device_key: Path = cert_output_dir / "device.key"
    file_mode: int = os.stat(device_key).st_mode & 0o777
    assert file_mode == 0o600, f"device.key permissions are {oct(file_mode)}, expected 0o600"


@skip_no_openssl
def test_device_cert_cn(ca_dir: Path, cert_output_dir: Path) -> None:
    """device.crt subject CN matches '{site_id}-{serial_number}' format."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(cert_output_dir / "device.crt"),
            "-noout",
            "-subject",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"openssl x509 subject failed: {result.stderr}"
    subject: str = result.stdout.strip()
    # openssl >= 3.x formats as "CN = value"; older versions use "CN=value"
    assert "site001-ECU1170-0042" in subject and "CN" in subject, (
        f"Expected CN=site001-ECU1170-0042 in subject, got: {subject}"
    )


@skip_no_openssl
def test_device_cert_org(ca_dir: Path, cert_output_dir: Path) -> None:
    """device.crt subject O=ReVx-Energy, OU=EMS."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(cert_output_dir / "device.crt"),
            "-noout",
            "-subject",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"openssl x509 subject failed: {result.stderr}"
    subject: str = result.stdout.strip()
    # openssl >= 3.x formats as "O = value"; older versions use "O=value"
    assert "ReVx-Energy" in subject and "O" in subject, (
        f"Expected O=ReVx-Energy in subject, got: {subject}"
    )
    assert "EMS" in subject and "OU" in subject, (
        f"Expected OU=EMS in subject, got: {subject}"
    )


@skip_no_openssl
def test_ca_cert_copied(ca_dir: Path, cert_output_dir: Path) -> None:
    """ca.crt in output matches the provided CA cert."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    original_ca: str = (ca_dir / "ca.crt").read_text()
    output_ca: str = (cert_output_dir / "ca.crt").read_text()
    assert original_ca == output_ca, "ca.crt in output does not match original CA cert"


@skip_no_openssl
def test_no_ca_key_in_output(ca_dir: Path, cert_output_dir: Path) -> None:
    """CA private key file is NOT present in the output directory."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    output_files: list[str] = [f.name for f in cert_output_dir.iterdir()]
    for filename in output_files:
        assert "ca.key" not in filename, (
            f"CA private key file found in output: {filename}"
        )


@skip_no_openssl
def test_csr_cleaned_up(ca_dir: Path, cert_output_dir: Path) -> None:
    """No .csr file remains in output directory after generation."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    csr_files: list[Path] = list(cert_output_dir.glob("*.csr"))
    assert len(csr_files) == 0, f"CSR file(s) found in output: {csr_files}"


@skip_no_openssl
def test_cert_signed_by_ca(ca_dir: Path, cert_output_dir: Path) -> None:
    """device.crt is verifiable against ca.crt (openssl verify)."""
    subprocess.run(
        [
            str(GEN_DEVICE_CERT),
            "site001",
            "ECU1170-0042",
            str(ca_dir / "ca.key"),
            str(ca_dir / "ca.crt"),
            str(cert_output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(cert_output_dir / "ca.crt"),
            str(cert_output_dir / "device.crt"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"openssl verify failed — device.crt not signed by CA: {result.stderr}"
    )
    assert "OK" in result.stdout, f"Unexpected verify output: {result.stdout}"


def test_script_rejects_missing_args() -> None:
    """Script exits non-zero with usage message when args are missing."""
    if not GEN_DEVICE_CERT.exists():
        pytest.skip("gen-device-cert.sh not yet created")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [str(GEN_DEVICE_CERT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, "Script should exit non-zero when called with no args"
    assert "Usage" in result.stdout or "Usage" in result.stderr, (
        "Script should print usage message when args are missing"
    )


def test_certs_recipe_exists() -> None:
    """ems-certs recipe file exists and installs to /etc/ems/certs/."""
    assert CERTS_RECIPE.exists(), f"ems-certs recipe not found at {CERTS_RECIPE}"
    recipe_text: str = CERTS_RECIPE.read_text()
    assert "/etc/ems/certs" in recipe_text, (
        "ems-certs recipe does not reference /etc/ems/certs/"
    )
