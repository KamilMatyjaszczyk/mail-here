"""RPM sources are curated separately from the user's working directory."""

import configparser
import hashlib
import runpy
import secrets
import shutil
import tarfile
from pathlib import Path

import pytest
import tomllib

PROJECT = Path(__file__).resolve().parents[1]
BUILDER = runpy.run_path(str(PROJECT / "packaging/rpm/build.py"))


@pytest.fixture
def source_tree(tmp_path):
    project = tmp_path / "project"
    for source in BUILDER["source_files"](PROJECT):
        target = project / BUILDER["source_relative_path"](PROJECT, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return project


def test_rpm_and_python_versions_agree():
    version = BUILDER["project_version"](PROJECT)
    assert BUILDER["project_version"](PROJECT, f"v{version}") == version
    with pytest.raises(ValueError, match="Release tag"):
        BUILDER["project_version"](PROJECT, "v999.0.0")


def test_repository_readme_is_packaged_and_can_be_rebuilt(source_tree, tmp_path):
    readme = source_tree / "README.md"
    content = readme.read_bytes()
    readme.rename(source_tree.parent / "README.md")
    version = BUILDER["project_version"](source_tree)
    archive = BUILDER["prepare_sources"](source_tree, tmp_path / "first", version)
    extracted = tmp_path / "extracted"
    with tarfile.open(archive) as source:
        assert source.extractfile(f"mcpmail-{version}/README.md").read() == content
        assert all(".." not in Path(name).parts for name in source.getnames())
        source.extractall(extracted, filter="data")
    rebuilt = BUILDER["prepare_sources"](
        extracted / f"mcpmail-{version}", tmp_path / "second", version
    )
    with tarfile.open(rebuilt) as source:
        assert source.extractfile(f"mcpmail-{version}/README.md").read() == content


def test_repository_readme_symlink_is_rejected(source_tree, tmp_path):
    (source_tree / "README.md").unlink()
    private = tmp_path / "private.md"
    private.write_text("not for distribution")
    (source_tree.parent / "README.md").symlink_to(private)
    with pytest.raises(ValueError, match="Symlinks"):
        BUILDER["source_files"](source_tree)


def test_rpm_version_mismatch_is_rejected(source_tree):
    spec = source_tree / "packaging/rpm/mcpmail.spec"
    version = BUILDER["project_version"](source_tree)
    spec.write_text(
        spec.read_text().replace(f"Version:        {version}", "Version:        0.0.0")
    )
    with pytest.raises(ValueError, match="match pyproject.toml"):
        BUILDER["project_version"](source_tree)


def test_source_archive_excludes_local_data_and_credentials(source_tree, tmp_path):
    excluded = (
        ".env",
        ".venv/lib/private.py",
        ".git/config",
        "dist/old.rpm",
        "client_secret.json",
        "credentials.json",
        "token.json",
        "private.key",
        "mailklient.sqlite3",
        "attachments/message.eml",
        "build/private.py",
        "src/mailklient/oauth-token.json",
        "tests/real-mail.eml",
    )
    marker = secrets.token_bytes(32)
    for name in excluded:
        path = source_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker)
    version = BUILDER["project_version"](source_tree)
    archive = BUILDER["prepare_sources"](source_tree, tmp_path / "rpmbuild", version)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        names = {member.name.removeprefix(f"mcpmail-{version}/") for member in members}
        assert not names.intersection(excluded)
        assert {
            "src/mailklient/database/schema.sql",
            "src/mailklient/main.py",
            "pyproject.toml",
            "tests/test_rpm_packaging.py",
            "packaging/rpm/mcpmail.spec",
            "docs/oauth.md",
        } <= names
        for member in members:
            assert member.isfile() and member.uid == member.gid == 0
            assert marker not in source.extractfile(member).read()


@pytest.mark.parametrize("name", ["src/mailklient/leak.py", "packaging/rpm/build.py"])
def test_source_symlinks_are_rejected(source_tree, tmp_path, name):
    private = tmp_path / "outside.py"
    private.write_text("not for distribution")
    link = source_tree / name
    link.unlink(missing_ok=True)
    link.symlink_to(private)
    with pytest.raises(ValueError, match="Symlinks"):
        BUILDER["source_files"](source_tree)


def test_rpm_launcher_uses_installed_command():
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(PROJECT / "packaging/rpm/mailklient.desktop")
    entry = desktop["Desktop Entry"]
    assert entry["Name"] == "mcpMail"
    assert entry["Exec"] == entry["TryExec"] == "mcpMail"
    assert entry["Icon"] == "mail-message-new"
    assert entry["Terminal"] == "false"
    assert "Email;" in entry["Categories"]


@pytest.mark.parametrize("engine", ["podman", "docker"])
def test_container_build_mounts_only_staging_and_checks_artifacts(
    tmp_path, monkeypatch, engine
):
    calls = []
    version = BUILDER["project_version"](PROJECT)

    def fake_run(*args):
        calls.append(args)
        if args[1] == "build":
            context = Path(args[-1])
            assert args[args.index("--file") + 1] == str(context / "Containerfile")
            assert {p.name for p in context.iterdir()} == {"Containerfile"}
        elif "rpmbuild" in args:
            assert "--network=none" in args
            assert (
                "--userns=keep-id" in args if engine == "podman" else "--user" in args
            )
            volume = args[args.index("--volume") + 1]
            topdir = Path(volume.removesuffix(":/build:Z"))
            assert not topdir.is_relative_to(PROJECT)
            for kind, arch in (("RPMS/noarch", "noarch"), ("SRPMS", "src")):
                path = topdir / kind / f"mcpmail-{version}-1.fc44.{arch}.rpm"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"synthetic-rpm")
        else:
            assert args[-1] == "/checks/check-install.sh"

    monkeypatch.setitem(BUILDER["build"].__globals__, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda _: "/test/container-engine")
    output = tmp_path / "output"
    artifacts = BUILDER["build"](PROJECT, output, engine, f"v{version}", True)
    assert len(calls) == 3
    assert len(artifacts) == 2
    assert (output / "SHA256SUMS").read_text() == "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in artifacts
    )


def test_renamed_package_preserves_imports_and_old_commands():
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    assert config["project"]["name"] == "mcpMail"
    scripts = config["project"]["scripts"]
    assert scripts["mcpMail"] == scripts["mailklient"] == "mailklient.main:main"
    assert scripts["mcpMail-launcher"] == scripts["mailklient-launcher"]
    spec = (PROJECT / "packaging/rpm/mcpmail.spec").read_text()
    assert "Name:           mcpmail" in spec
    assert "Obsoletes:      mailklient < 0.1.0-2" in spec
