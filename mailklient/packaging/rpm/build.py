"""Build a Fedora RPM in a container, without mounting the working tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import tomllib

PACKAGING_FILES = (
    "Containerfile",
    "LICENSE-NOTICE",
    "build.py",
    "mailklient.desktop",
    "mcpmail.spec",
    "check-install.sh",
    "smoke_test.py",
)
IMAGE = "localhost/mcpmail-rpm-builder:44"


def project_version(project: Path, tag: str | None = None) -> str:
    with (project / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("RPM builds require a numeric X.Y.Z project version.")
    spec = (project / "packaging/rpm/mcpmail.spec").read_text()
    match = re.search(r"^Version:\s*(\S+)\s*$", spec, re.MULTILINE)
    if not match or match[1] != version:
        raise ValueError("Update Version in mcpmail.spec to match pyproject.toml.")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Release tag must be v{version}.")
    return version


def source_files(project: Path) -> list[Path]:
    paths = [project / name for name in ("pyproject.toml", "README.md", "MANIFEST.in")]
    paths.extend(project / "packaging/rpm" / name for name in PACKAGING_FILES)
    # Include code, docs and synthetic tests only, never the entire checkout.
    for directory, patterns in (
        ("src/mailklient", ("*.py", "*.sql")),
        ("docs", ("*.md",)),
        ("tests", ("test_*.py",)),
    ):
        root = project / directory
        for pattern in patterns:
            paths.extend(root.rglob(pattern))
    for path in paths:
        if not path.is_file():
            raise ValueError(
                f"Missing regular source file: {path.relative_to(project)}"
            )
        for part in (path, *path.relative_to(project).parents):
            candidate = part if part.is_absolute() else project / part
            if candidate.is_symlink():
                raise ValueError(
                    f"Symlinks are not packaged: {path.relative_to(project)}"
                )
        if any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(project).parts
        ):
            raise ValueError(f"Hidden source path: {path.relative_to(project)}")
    return sorted(set(paths))


def prepare_sources(project: Path, topdir: Path, version: str) -> Path:
    sources = topdir / "SOURCES"
    specs = topdir / "SPECS"
    sources.mkdir(parents=True)
    specs.mkdir()
    (topdir / "home").mkdir()
    archive = sources / f"mcpmail-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for path in source_files(project):
            name = f"mcpmail-{version}/{path.relative_to(project).as_posix()}"
            info = output.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o644
            info.mtime = 0
            with path.open("rb") as stream:
                output.addfile(info, stream)
    packaging = project / "packaging/rpm"
    shutil.copyfile(packaging / "mcpmail.spec", specs / "mcpmail.spec")
    for name in ("mailklient.desktop", "LICENSE-NOTICE"):
        shutil.copyfile(packaging / name, sources / name)
    return archive


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def build(
    project: Path,
    output: Path,
    engine: str,
    tag: str | None,
    check_install: bool = False,
) -> list[Path]:
    version = project_version(project, tag)
    if shutil.which(engine) is None:
        raise ValueError(f"Install {engine} before building the RPM.")
    with tempfile.TemporaryDirectory(prefix="mcpmail-rpm-") as temporary:
        stage = Path(temporary)
        topdir = stage / "rpmbuild"
        prepare_sources(project, topdir, version)
        context = stage / "image"
        context.mkdir()
        shutil.copyfile(
            project / "packaging/rpm/Containerfile", context / "Containerfile"
        )
        run(
            engine,
            "build",
            "--file",
            str(context / "Containerfile"),
            "--tag",
            IMAGE,
            str(context),
        )
        user_args = (
            ["--userns=keep-id"]
            if engine == "podman"
            else ["--user", f"{os.getuid()}:{os.getgid()}"]
        )
        run(
            engine,
            "run",
            "--rm",
            "--network=none",
            *user_args,
            "--volume",
            f"{topdir}:/build:Z",
            "--env",
            "HOME=/build/home",
            IMAGE,
            "rpmbuild",
            "-ba",
            "--define",
            "_topdir /build",
            "/build/SPECS/mcpmail.spec",
        )
        packages = sorted((topdir / "RPMS").rglob("*.rpm"))
        packages.extend(sorted((topdir / "SRPMS").glob("*.src.rpm")))
        if not any(path.name.endswith(".noarch.rpm") for path in packages):
            raise RuntimeError("rpmbuild produced no installable RPM.")
        if check_install:
            checks = stage / "checks"
            checks.mkdir()
            for name in ("check-install.sh", "smoke_test.py"):
                shutil.copyfile(project / "packaging/rpm" / name, checks / name)
            run(
                engine,
                "run",
                "--rm",
                "--volume",
                f"{topdir / 'RPMS/noarch'}:/artifacts:ro,Z",
                "--volume",
                f"{checks}:/checks:ro,Z",
                "registry.fedoraproject.org/fedora:44",
                "bash",
                "/checks/check-install.sh",
            )
        output.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for package in packages:
            target = output / package.name
            if target.is_symlink():
                raise ValueError(f"Refusing to overwrite symlink: {target}")
            shutil.copyfile(package, target)
            artifacts.append(target)
        sums = output / "SHA256SUMS"
        if sums.is_symlink():
            raise ValueError(f"Refusing to overwrite symlink: {sums}")
        sums.write_text(
            "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in artifacts
            )
        )
        return artifacts


def main() -> int:
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("podman", "docker"), default="podman")
    parser.add_argument("--output-dir", type=Path, default=project / "dist/rpm")
    parser.add_argument("--tag", help="Require a matching vX.Y.Z release tag")
    parser.add_argument(
        "--check-install",
        action="store_true",
        help="Also install, start and uninstall in a fresh Fedora container",
    )
    args = parser.parse_args()
    try:
        artifacts = build(
            project,
            args.output_dir.resolve(),
            args.engine,
            args.tag,
            args.check_install,
        )
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"RPM build failed: {exc}\n")
    for artifact in artifacts:
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
