"""Static contracts for the Windows-to-Linux release exporter."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EXPORT_SCRIPT = (
    REPOSITORY_ROOT / "deploy" / "scripts" / "export-windows-release.ps1"
)


def _script() -> str:
    return EXPORT_SCRIPT.read_text(encoding="utf-8")


def test_exporter_requires_linux_amd64_immutable_images() -> None:
    content = _script()

    assert "linux/amd64" in content
    assert "bizorch-api:$ReleaseId" in content
    assert "bizorch-enterprise:$ReleaseId" in content
    assert "bizorch-enterprise-mcp:$ReleaseId" in content
    assert "docker save" not in content
    assert "@('save', '--output', $imageArchive)" in content


def test_exporter_packages_only_explicit_deploy_inputs() -> None:
    content = _script()

    assert "'deploy\\compose.yaml'" in content
    assert "'deploy\\production.env.example'" in content
    assert "'deploy\\nginx'" in content
    assert "'deploy\\scripts'" in content
    assert "compose.windows-e2e.yaml" not in content
    assert "Runtime .env is intentionally excluded" in content
    assert "$_.Name -eq '.env'" in content


def test_exporter_separates_runtime_data_and_creates_sha256_manifest() -> None:
    content = _script()

    assert "[switch]$ConfirmDataWritersStopped" in content
    assert "Stop the local BizOrch API and knowledge-index worker" in content
    assert "'data\\chroma'" in content
    assert "'data\\uploads'" in content
    assert "'data\\checkpoints'" in content
    assert "bizorch-data-$ReleaseId.tar.gz" in content
    assert "Get-FileHash -Algorithm SHA256" in content
    assert "'SHA256SUMS'" in content
    assert "[System.IO.File]::WriteAllText" in content
    assert '($checksumLines -join "`n") + "`n"' in content
