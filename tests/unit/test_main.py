import logging
from pathlib import Path

import pytest
from pytest_mock import MockerFixture
from requests_mock.mocker import Mocker as RequestMocker

from lib import main
from lib.config import AccountConfig, GlobalConfig, ReservationConfig, TrackedFareConfig
from lib.notification_handler import NotificationHandler
from lib.reservation_monitor import AccountMonitor, ReservationMonitor
from lib.standalone_fare_tracker import StandaloneFareDrop, StandaloneFareMonitor, TrackedFare


@pytest.fixture(autouse=True)
def mock_config(mocker: MockerFixture) -> None:
    """The config file shouldn't actually be read for these tests"""
    mocker.patch("lib.config.GlobalConfig._read_config")


def test_get_timezone_fetches_timezone_from_request(requests_mock: RequestMocker) -> None:
    requests_mock.get(main.IP_TIMEZONE_URL, text="Asia/Tokyo")
    assert main.get_timezone() == "Asia/Tokyo"


def test_get_timezone_returns_utc_when_request_fails(requests_mock: RequestMocker) -> None:
    requests_mock.get(main.IP_TIMEZONE_URL, status_code=500)
    assert main.get_timezone() == "UTC"


def test_test_notifications_sends_to_every_url_in_config(mocker: MockerFixture) -> None:
    # Accessing protected methods is just used to not need to provide a full config object
    # to parse

    config = GlobalConfig()
    config.accounts = [AccountConfig()]
    config.reservations = [ReservationConfig()]
    config.fare_trackers = [TrackedFareConfig()]
    config._create_notification_config([{"url": "url1"}])

    config.accounts[0]._create_notification_config([{"url": "url1"}])
    config.accounts[0]._create_notification_config([{"url": "url2"}])

    config.reservations[0]._create_notification_config([{"url": "url3"}])
    config.reservations[0]._create_notification_config([{"url": "url1"}])

    config.fare_trackers[0]._create_notification_config([{"url": "url4"}])
    config.fare_trackers[0]._create_notification_config([{"url": "url2"}])

    mock_send_notification = mocker.patch.object(NotificationHandler, "send_notification")

    main.test_notifications(config)

    # Make sure the configs were merged correctly so all of the URLs are only sent one test
    # notification each
    assert len(config.notifications) == 4

    mock_send_notification.assert_called_once()


@pytest.mark.parametrize(("expected", "count"), [("tests", 0), ("test", 1), ("tests", 2)])
def test_pluralize_pluralizes_a_word_if_needed(expected: str, count: int) -> None:
    assert main.pluralize("test", count) == expected


def test_set_up_accounts_starts_all_accounts(mocker: MockerFixture) -> None:
    config = GlobalConfig()
    config.accounts = [AccountConfig(), AccountConfig()]

    mock_account_start = mocker.patch.object(AccountMonitor, "start")
    main.set_up_accounts(config, None)
    assert mock_account_start.call_count == len(config.accounts)


def test_set_up_reservations_starts_all_reservations(mocker: MockerFixture) -> None:
    config = GlobalConfig()
    config.reservations = [ReservationConfig(), ReservationConfig()]

    mock_reservation_start = mocker.patch.object(ReservationMonitor, "start")
    main.set_up_reservations(config, None)
    assert mock_reservation_start.call_count == len(config.reservations)


def test_set_up_fare_trackers_starts_all_fare_trackers(mocker: MockerFixture) -> None:
    config = GlobalConfig()
    config.fare_trackers = [TrackedFareConfig(), TrackedFareConfig()]

    mock_fare_tracker_start = mocker.patch.object(StandaloneFareMonitor, "start")
    main.set_up_fare_trackers(config, None)
    assert mock_fare_tracker_start.call_count == len(config.fare_trackers)


def test_set_up_check_in_sends_test_notifications_when_flag_passed(mocker: MockerFixture) -> None:
    mock_test_notifications = mocker.patch("lib.main.test_notifications")
    with pytest.raises(SystemExit):
        main.set_up_check_in(["--test-notifications"])
    mock_test_notifications.assert_called_once()


def test_set_up_check_in_checks_fare_trackers_once_when_flag_passed(
    mocker: MockerFixture,
) -> None:
    mock_check_fare_trackers_once = mocker.patch("lib.main.check_fare_trackers_once")

    with pytest.raises(SystemExit):
        main.set_up_check_in(["--fare-trackers-once"])

    mock_check_fare_trackers_once.assert_called_once()


def test_set_up_check_in_passes_fare_tracker_summary_file(
    mocker: MockerFixture,
) -> None:
    mock_check_fare_trackers_once = mocker.patch("lib.main.check_fare_trackers_once")

    with pytest.raises(SystemExit):
        main.set_up_check_in(["--fare-trackers-once", "--fare-trackers-summary-file", "drops.md"])

    assert mock_check_fare_trackers_once.call_args[0][1] == Path("drops.md")


def test_check_fare_trackers_once_sends_one_summary_notification(
    mocker: MockerFixture,
) -> None:
    config = GlobalConfig()
    config.fare_trackers = [TrackedFareConfig(), TrackedFareConfig()]
    drops = ["drop_one", "drop_two"]
    mock_check = mocker.patch.object(
        StandaloneFareMonitor, "_check", side_effect=[[drops[0]], [drops[1]]]
    )
    mock_summary = mocker.patch.object(NotificationHandler, "standalone_fare_drop_summary")

    main.check_fare_trackers_once(config)

    assert mock_check.call_count == 2
    mock_summary.assert_called_once_with(drops)


def test_check_fare_trackers_once_does_not_notify_when_no_drops(
    mocker: MockerFixture,
) -> None:
    config = GlobalConfig()
    config.fare_trackers = [TrackedFareConfig()]
    mocker.patch.object(StandaloneFareMonitor, "_check", return_value=[])
    mock_summary = mocker.patch.object(NotificationHandler, "standalone_fare_drop_summary")

    main.check_fare_trackers_once(config)

    mock_summary.assert_not_called()


def test_write_fare_tracker_summary_writes_markdown(tmp_path: Path) -> None:
    config = TrackedFareConfig()
    config.origin_airport = "PHX"
    config.destination_airport = "DEN"
    config.departure_date = "2026-08-15"
    config.flight_number = "1234"
    summary_file = tmp_path / "drops.md"
    drops = [
        StandaloneFareDrop(
            config,
            TrackedFare("USD", 12000, "1234", "10:00"),
            TrackedFare("USD", 9900, "1234", "10:00"),
        )
    ]

    main.write_fare_tracker_summary(summary_file, drops)

    summary = summary_file.read_text()
    assert "# Southwest Fare Drops" in summary
    assert "PHX to DEN flight 1234" in summary
    assert "$120.00 to $99.00" in summary


def test_write_fare_tracker_summary_removes_empty_summary(tmp_path: Path) -> None:
    summary_file = tmp_path / "drops.md"
    summary_file.write_text("old summary")

    main.write_fare_tracker_summary(summary_file, [])

    assert not summary_file.exists()


@pytest.mark.parametrize(
    ("arguments", "accounts_len", "reservations_len"),
    [
        ([], 0, 0),
        (["username", "password"], 1, 0),
        (["test", "John", "Doe"], 0, 1),
    ],
)
def test_set_up_check_in_sets_up_account_and_reservation_with_arguments(
    mocker: MockerFixture, arguments: list[str], accounts_len: int, reservations_len: int
) -> None:
    mock_process = mocker.patch("multiprocessing.Process")
    mock_processes = [mock_process] * (accounts_len + reservations_len)
    mocker.patch("multiprocessing.active_children", return_value=mock_processes)

    mock_set_up_accounts = mocker.patch("lib.main.set_up_accounts")
    mock_set_up_reservations = mocker.patch("lib.main.set_up_reservations")
    mock_set_up_fare_trackers = mocker.patch("lib.main.set_up_fare_trackers")

    main.set_up_check_in(arguments)

    assert len(mock_set_up_accounts.call_args[0][0].accounts) == accounts_len
    assert len(mock_set_up_reservations.call_args[0][0].reservations) == reservations_len
    mock_set_up_fare_trackers.assert_called_once()
    assert mock_process.join.call_count == len(mock_processes)


def test_set_up_check_in_sends_error_message_when_arguments_are_invalid(
    caplog: pytest.CaptureFixture[str],
) -> None:
    arguments = ["1", "2", "3", "4"]

    with pytest.raises(SystemExit):
        main.set_up_check_in(arguments)
    output = caplog.record_tuples[-1]

    assert output[1] == logging.ERROR
    assert "Invalid arguments" in output[2]
    assert "--help" in output[2]


def test_get_config_ui_port_returns_default_port() -> None:
    assert main.get_config_ui_port([]) == main.DEFAULT_CONFIG_UI_PORT


def test_get_config_ui_port_returns_configured_port() -> None:
    assert main.get_config_ui_port(["--config-ui-port", "9090"]) == 9090


@pytest.mark.parametrize("arguments", [["--config-ui-port"], ["--config-ui-port", "invalid"]])
def test_get_config_ui_port_exits_on_invalid_port(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        main.get_config_ui_port(arguments)


def test_main_starts_config_ui(mocker: MockerFixture) -> None:
    mocker.patch("lib.log.init_main_logging")
    mock_run_config_ui = mocker.patch("lib.config_ui.run_config_ui")
    mock_set_up_check_in = mocker.patch("lib.main.set_up_check_in")

    main.main(["--config-ui", "--config-ui-port", "9090"], "test_version")

    mock_run_config_ui.assert_called_once_with(9090)
    mock_set_up_check_in.assert_not_called()


def test_main_sets_up_the_script(mocker: MockerFixture) -> None:
    mock_init_main_logging = mocker.patch("lib.log.init_main_logging")
    mock_set_up_check_in = mocker.patch("lib.main.set_up_check_in")
    mock_get_timezone = mocker.patch("lib.main.get_timezone")

    arguments = ["test", "arguments", "--verbose", "-v"]
    main.main(arguments, "test_version")
    mock_init_main_logging.assert_called_once()

    # Ensure the '--verbose' and '-v' flags are removed
    mock_set_up_check_in.assert_called_once_with(arguments[:2])

    mock_get_timezone.assert_not_called()


def test_main_fetches_timezone_if_docker(mocker: MockerFixture) -> None:
    mocker.patch("lib.log.init_main_logging")
    mocker.patch("lib.main.set_up_check_in")

    mock_get_timezone = mocker.patch("lib.main.get_timezone", return_value="UTC")
    mocker.patch("lib.main.IS_DOCKER", return_value=True)

    main.main([], "test_version")
    mock_get_timezone.assert_called_once()


def test_main_exits_on_keyboard_interrupt(mocker: MockerFixture) -> None:
    mocker.patch("lib.log.init_main_logging")
    mocker.patch.object(main, "set_up_check_in", side_effect=KeyboardInterrupt)

    with pytest.raises(SystemExit):
        main.main([], "test_version")
