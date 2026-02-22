"""Tests for the PackageLayout utility."""

from pathlib import Path

from pycmdcheck.package_layout import PackageLayout


class TestSrcLayout:
    """Tests for src-layout projects."""

    def test_src_layout(self, temp_package: Path) -> None:
        """temp_package uses src/ layout and contains 'mypackage'."""
        layout = PackageLayout(temp_package)

        assert layout.is_src_layout is True
        names = layout.local_package_names()
        assert "mypackage" in names

    def test_primary_package(self, temp_package: Path) -> None:
        """Primary package should be 'mypackage' for temp_package."""
        layout = PackageLayout(temp_package)

        primary = layout.primary_package
        assert primary is not None
        assert primary.name == "mypackage"

    def test_primary_package_uses_name_hint(self, temp_package: Path) -> None:
        """When package_name kwarg is given, primary_package respects it."""
        # Create a second package in the src/ directory
        extra_pkg = temp_package / "src" / "extra"
        extra_pkg.mkdir()
        (extra_pkg / "__init__.py").write_text("")

        layout = PackageLayout(temp_package, package_name="extra")

        assert layout.primary_package is not None
        assert layout.primary_package.name == "extra"


class TestFlatLayout:
    """Tests for flat-layout projects."""

    def test_flat_layout(self, flat_layout_package: Path) -> None:
        """flat_layout_package should be detected as flat layout with 'myflatpkg'."""
        layout = PackageLayout(flat_layout_package)

        assert layout.is_src_layout is False
        names = layout.local_package_names()
        assert "myflatpkg" in names


class TestEmptyDir:
    """Tests for an empty project directory."""

    def test_empty_dir(self, empty_dir: Path) -> None:
        """An empty directory has no packages; primary_package is None."""
        layout = PackageLayout(empty_dir)

        assert layout.package_dirs == []
        assert layout.primary_package is None


class TestPythonFiles:
    """Tests for the python_files() method."""

    def test_python_files(self, temp_package: Path) -> None:
        """python_files() returns .py files and all have the .py suffix."""
        layout = PackageLayout(temp_package)
        files = layout.python_files()

        assert len(files) > 0
        assert all(f.suffix == ".py" for f in files)

    def test_python_files_excludes_venv(self, temp_package: Path) -> None:
        """Files under .venv should be excluded from python_files()."""
        # Create a .venv directory with a Python file inside the project
        venv_dir = temp_package / "src" / "mypackage" / ".venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / "sneaky.py").write_text("# should be excluded\n")

        layout = PackageLayout(temp_package)
        files = layout.python_files()

        file_names = {f.name for f in files}
        assert "sneaky.py" not in file_names


class TestLocalPackageNames:
    """Tests for the local_package_names() method."""

    def test_local_package_names(self, temp_package: Path) -> None:
        """local_package_names() returns a set containing the package name."""
        layout = PackageLayout(temp_package)
        names = layout.local_package_names()

        assert isinstance(names, set)
        assert "mypackage" in names
