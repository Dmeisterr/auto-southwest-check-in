from pathlib import Path


def test_fare_tracker_timer_runs_every_8_hours() -> None:
    timer = Path("deploy/systemd/auto-southwest-fare-trackers.timer").read_text()

    assert "OnUnitActiveSec=8h" in timer
    assert "Persistent=true" in timer
    assert "Unit=auto-southwest-fare-trackers.service" in timer


def test_fare_tracker_service_runs_once_with_environment_file() -> None:
    service = Path("deploy/systemd/auto-southwest-fare-trackers.service").read_text()

    assert "Type=oneshot" in service
    assert "WorkingDirectory=%h/auto-southwest-check-in" in service
    assert "EnvironmentFile=-%h/.config/auto-southwest-check-in/fare-trackers.env" in service
    assert "southwest.py --fare-trackers-once --verbose" in service


def test_fare_tracker_environment_example_uses_gmail_notification_url() -> None:
    env_file = Path("deploy/systemd/fare-trackers.env.example").read_text()

    assert "AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_URL" in env_file
    assert "mailtos://gmail.com" in env_file
    assert "user=your.email@gmail.com" in env_file
