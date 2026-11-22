import sys

import pytest

from stampcut.core import platforms


@pytest.mark.parametrize(
    "sys_platform, expected",
    [("win32", "windows"), ("cygwin", "windows"), ("darwin", "macos"), ("linux", "linux"), ("freebsd14", "linux")],
)
def test_current_maps_sys_platform(monkeypatch, sys_platform, expected):
    monkeypatch.setattr(sys, "platform", sys_platform)
    assert platforms.current() == expected


@pytest.mark.parametrize(
    "sys_platform, expected",
    [("win32", "~/Videos/StampCut"), ("darwin", "~/Movies/StampCut"), ("linux", "~/Videos/StampCut")],
)
def test_default_output_dir(monkeypatch, sys_platform, expected):
    monkeypatch.setattr(sys, "platform", sys_platform)
    assert platforms.default_output_dir() == expected
