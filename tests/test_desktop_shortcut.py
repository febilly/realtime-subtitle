import os
from pathlib import Path
from unittest.mock import call, patch

import pytest

import desktop_shortcut


def hosted_executable(version: str, directory: str = r"C:\Apps") -> Path:
    return Path(directory) / f"RealtimeSubtitle-hosted-v{version}.exe"


def test_source_mode_is_unavailable_and_does_not_touch_native_shortcuts():
    with patch.object(desktop_shortcut, "current_executable_path", return_value=None), \
            patch.object(desktop_shortcut, "_matching_shortcuts") as inspect, \
            patch.object(desktop_shortcut, "_write_shortcut") as write:
        assert desktop_shortcut.get_shortcut_status() == {
            "available": False,
            "exists": False,
            "matched": 0,
        }
        assert desktop_shortcut.create_desktop_shortcut()["created"] is False
        assert desktop_shortcut.repair_existing_shortcuts()["updated"] == 0
        inspect.assert_not_called()
        write.assert_not_called()


def test_non_windows_packaged_process_skips_the_entire_feature():
    with patch.object(desktop_shortcut.os, "name", "posix"), \
            patch.object(desktop_shortcut.sys, "frozen", True, create=True), \
            patch.object(
                desktop_shortcut.sys,
                "executable",
                "/tmp/RealtimeSubtitle-hosted-v4.1.3.exe",
            ):
        assert desktop_shortcut.current_executable_path() is None
        assert desktop_shortcut.repair_existing_shortcuts() == {
            "available": False,
            "exists": False,
            "matched": 0,
            "updated": 0,
        }


@pytest.mark.parametrize(("left", "right", "expected"), [
    ("4.1.3", "4.1.2", 1),
    ("4.1.3", "4.1.3", 0),
    ("4.1.3", "4.1.3-rc.10", 1),
    ("4.1.3-rc.2", "4.1.3-rc.10", -1),
    ("4.1.3rc2", "4.1.3rc1", 1),
    ("4.1.3-preview.2", "4.1.3-beta.9", 1),
    ("4.1.3-beta.2+build.7", "4.1.3-beta.2+build.1", 0),
    ("4.1", "4.1.0", 0),
])
def test_compare_versions_handles_numeric_and_prerelease_parts(left, right, expected):
    assert desktop_shortcut.compare_versions(left, right) == expected


def test_compare_versions_is_conservative_for_unparseable_values():
    assert desktop_shortcut.compare_versions("nightly", "4.1.3") is None
    assert desktop_shortcut.compare_versions("4.1.3", "unknown") is None


def test_target_replacement_never_downgrades_and_replaces_equal_version_paths():
    current = hosted_executable("4.1.3", r"C:\Downloads\current")
    assert desktop_shortcut.should_replace_target(
        current, hosted_executable("4.1.2", r"C:\Downloads\old")
    ) is True
    assert desktop_shortcut.should_replace_target(
        current, hosted_executable("4.1.3", r"D:\Other")
    ) is True
    assert desktop_shortcut.should_replace_target(
        current, hosted_executable("4.1.4", r"C:\Downloads\newer")
    ) is False
    assert desktop_shortcut.should_replace_target(
        current, Path(r"C:\Downloads\current\RealtimeSubtitle-hosted-v4.1.3.exe")
    ) is False


def test_status_reports_existing_application_shortcut():
    executable = hosted_executable("4.1.3")
    with patch.object(desktop_shortcut, "current_executable_path", return_value=executable), \
            patch.object(desktop_shortcut, "_matching_shortcuts", return_value=[
                {"path": r"C:\Desktop\One.lnk", "target": str(executable)},
                {"path": r"C:\Desktop\Two.lnk", "target": str(executable)},
            ]) as inspect:
        assert desktop_shortcut.get_shortcut_status() == {
            "available": True,
            "exists": True,
            "matched": 2,
        }
        inspect.assert_called_once_with()


def test_startup_repair_only_updates_targets_that_are_not_newer():
    executable = hosted_executable("4.1.3", r"C:\Downloads\current")
    old_path = r"C:\Desktop\Old.lnk"
    same_path = r"C:\Desktop\Same.lnk"
    newer_path = r"C:\Desktop\Newer.lnk"
    inspected = [
        {"path": old_path, "target": str(hosted_executable("4.1.2", r"C:\Old"))},
        {"path": same_path, "target": str(hosted_executable("4.1.3", r"D:\Same"))},
        {"path": newer_path, "target": str(hosted_executable("4.1.4", r"C:\Newer"))},
    ]
    with patch.object(desktop_shortcut, "current_executable_path", return_value=executable), \
            patch.object(desktop_shortcut, "_matching_shortcuts", return_value=inspected), \
            patch.object(desktop_shortcut, "_write_shortcut") as write:
        assert desktop_shortcut.repair_existing_shortcuts() == {
            "available": True,
            "exists": True,
            "matched": 3,
            "updated": 2,
        }
        assert write.call_args_list == [
            call(Path(old_path), executable, load_existing=True),
            call(Path(same_path), executable, load_existing=True),
        ]


def test_create_uses_the_current_packaged_executable():
    executable = hosted_executable("4.1.3")
    desktop = Path(r"C:\Users\Test\Desktop")
    with patch.object(desktop_shortcut, "current_executable_path", return_value=executable), \
            patch.object(desktop_shortcut, "_desktop_path", return_value=desktop), \
            patch.object(desktop_shortcut.Path, "exists", return_value=False), \
            patch.object(desktop_shortcut, "_write_shortcut") as write:
        assert desktop_shortcut.create_desktop_shortcut() == {
            "available": True,
            "created": True,
            "exists": True,
        }
        write.assert_called_once_with(
            desktop / desktop_shortcut.SHORTCUT_FILENAME,
            executable,
            load_existing=False,
        )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Shell shortcuts")
def test_real_shortcut_repair_updates_same_version_when_directory_changes(tmp_path):
    desktop = tmp_path / "Desktop"
    old_dir = tmp_path / "old download"
    new_dir = tmp_path / "new download"
    desktop.mkdir()
    old_dir.mkdir()
    new_dir.mkdir()
    filename = "RealtimeSubtitle-hosted-v4.1.3.exe"
    old_executable = old_dir / filename
    new_executable = new_dir / filename
    old_executable.touch()
    new_executable.touch()

    with patch.object(desktop_shortcut, "_desktop_path", return_value=desktop):
        desktop_shortcut._write_shortcut(
            desktop / desktop_shortcut.SHORTCUT_FILENAME,
            old_executable,
            load_existing=False,
        )
        with patch.object(desktop_shortcut, "current_executable_path", return_value=new_executable):
            first_repair = desktop_shortcut.repair_existing_shortcuts()
            second_repair = desktop_shortcut.repair_existing_shortcuts()

    assert first_repair["updated"] == 1
    assert second_repair["updated"] == 0


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Shell shortcuts")
def test_real_shortcut_repair_recognizes_renamed_versioned_shortcut(tmp_path):
    desktop = tmp_path / "Desktop"
    old_dir = tmp_path / "v4.1.2"
    new_dir = tmp_path / "v4.1.3"
    desktop.mkdir()
    old_dir.mkdir()
    new_dir.mkdir()
    old_executable = old_dir / "RealtimeSubtitle-hosted-v4.1.2.exe"
    new_executable = new_dir / "RealtimeSubtitle-hosted-v4.1.3.exe"
    old_executable.touch()
    new_executable.touch()

    with patch.object(desktop_shortcut, "_desktop_path", return_value=desktop):
        desktop_shortcut._write_shortcut(
            desktop / desktop_shortcut.SHORTCUT_FILENAME,
            old_executable,
            load_existing=False,
        )
        (desktop / desktop_shortcut.SHORTCUT_FILENAME).rename(desktop / "My Subtitles.lnk")
        with patch.object(desktop_shortcut, "current_executable_path", return_value=new_executable):
            repair = desktop_shortcut.repair_existing_shortcuts()
            repeat = desktop_shortcut.repair_existing_shortcuts()

    assert repair["updated"] == 1
    assert repeat["updated"] == 0
