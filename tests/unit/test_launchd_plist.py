import plistlib
from pathlib import Path


def test_launchd_fare_tracker_plist_runs_one_shot_command_every_8_hours() -> None:
    plist_path = Path("deploy/launchd/com.auto-southwest.fare-trackers.plist")
    plist = plistlib.loads(plist_path.read_bytes())

    assert plist["Label"] == "com.auto-southwest.fare-trackers"
    assert plist["StartInterval"] == 8 * 60 * 60
    assert plist["RunAtLoad"] is True
    assert plist["WorkingDirectory"].endswith("auto-southwest-check-in")
    assert plist["ProgramArguments"][-2:] == ["--fare-trackers-once", "--verbose"]
    assert plist["ProgramArguments"][0].endswith("venv/bin/python")
    assert plist["ProgramArguments"][1].endswith("southwest.py")
    assert plist["StandardOutPath"].endswith("logs/launchd-fare-trackers.out.log")
    assert plist["StandardErrorPath"].endswith("logs/launchd-fare-trackers.err.log")
