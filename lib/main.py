"""Primary script entrypoint where arguments are processed and flights are set up."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

import requests

from lib import log

from .config import IS_DOCKER, GlobalConfig, ReservationConfig
from .config_ui import DEFAULT_CONFIG_UI_PORT
from .reservation_monitor import AccountMonitor, ReservationMonitor
from .standalone_fare_tracker import StandaloneFareMonitor

IP_TIMEZONE_URL = "https://ipinfo.io/timezone"
LOG_FILE = "logs/auto-southwest-check-in.log"
CONFIG_UI_FLAG = "--config-ui"
CONFIG_UI_PORT_FLAG = "--config-ui-port"
FARE_TRACKERS_ONCE_FLAG = "--fare-trackers-once"
FARE_TRACKERS_SUMMARY_FILE_FLAG = "--fare-trackers-summary-file"

logger = log.get_logger(__name__)


def get_timezone() -> str:
    """Fetches the local timezone based on the system's IP address"""
    try:
        logger.debug("Fetching local timezone")
        response = requests.get(IP_TIMEZONE_URL, timeout=5)
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException:
        logger.debug("Timezone request failed, reverting to UTC")
        return "UTC"


def test_notifications(config: GlobalConfig) -> None:
    """
    Send a test notification to all configured sources. The notification configs for every account
    and reservation are merged to ensure only one test notification is sent to each source, even
    if a URL is specified for multiple accounts or reservations.
    """
    for account in config.accounts:
        config.merge_notification_config(account)

    for reservation in config.reservations:
        config.merge_notification_config(reservation)

    for fare_tracker in config.fare_trackers:
        config.merge_notification_config(fare_tracker)

    new_config = ReservationConfig()
    new_config.notifications = config.notifications
    reservation_monitor = ReservationMonitor(new_config)

    logger.info("Sending test notifications to %d sources", len(new_config.notifications))
    reservation_monitor.notification_handler.send_notification("This is a test message")


def pluralize(word: str, count: int) -> str:
    """Pluralize a word to improve grammar for printed messages"""
    return word if count == 1 else word + "s"


def set_up_accounts(config: GlobalConfig, lock: multiprocessing.Lock) -> None:
    for account in config.accounts:
        account_monitor = AccountMonitor(account, lock)
        account_monitor.start()


def set_up_reservations(config: GlobalConfig, lock: multiprocessing.Lock) -> None:
    for reservation in config.reservations:
        reservation_monitor = ReservationMonitor(reservation, lock)
        reservation_monitor.start()


def set_up_fare_trackers(config: GlobalConfig, lock: multiprocessing.Lock) -> None:
    for fare_tracker in config.fare_trackers:
        fare_monitor = StandaloneFareMonitor(fare_tracker, lock)
        fare_monitor.start()


def check_fare_trackers_once(config: GlobalConfig, summary_file_path: Path | None = None) -> None:
    drops = []
    for fare_tracker in config.fare_trackers:
        config.merge_notification_config(fare_tracker)
        fare_monitor = StandaloneFareMonitor(fare_tracker)
        drops.extend(fare_monitor._check(send_notifications=False))

    if summary_file_path is not None:
        write_fare_tracker_summary(summary_file_path, drops)

    if drops:
        new_config = ReservationConfig()
        new_config.notifications = config.notifications
        reservation_monitor = ReservationMonitor(new_config)
        reservation_monitor.notification_handler.standalone_fare_drop_summary(drops)


def write_fare_tracker_summary(summary_file_path: Path, drops: list) -> None:
    if not drops:
        try:
            summary_file_path.unlink()
        except FileNotFoundError:
            pass
        return

    lines = ["# Southwest Fare Drops", ""]
    for drop in drops:
        config = drop.config
        flight_info = ""
        if config.flight_number:
            flight_info = f" flight {config.flight_number}"

        previous_price = NotificationPriceFormatter.format(drop.previous_fare)
        current_price = NotificationPriceFormatter.format(drop.current_fare)
        lines.append(
            f"- {config.origin_airport} to {config.destination_airport}{flight_info} on "
            f"{config.departure_date}: {drop.current_fare.currency_code} dropped from "
            f"{previous_price} to {current_price}"
        )

    summary_file_path.write_text("\n".join(lines) + "\n")


class NotificationPriceFormatter:
    @staticmethod
    def format(fare) -> str:
        if fare.currency_code == "USD":
            return f"${fare.amount / 100:.2f}"

        return f"{fare.amount:,} {fare.currency_code}"


def get_config_ui_port(arguments: list[str]) -> int:
    if CONFIG_UI_PORT_FLAG not in arguments:
        return DEFAULT_CONFIG_UI_PORT

    port_arg_idx = arguments.index(CONFIG_UI_PORT_FLAG) + 1
    try:
        port = int(arguments[port_arg_idx])
    except (IndexError, ValueError):
        logger.error("Invalid config UI port. For more information, try '--help'")
        sys.exit(2)

    if port <= 0:
        logger.error("Invalid config UI port. For more information, try '--help'")
        sys.exit(2)

    return port


def get_fare_trackers_summary_file(arguments: list[str]) -> Path | None:
    if FARE_TRACKERS_SUMMARY_FILE_FLAG not in arguments:
        return None

    path_arg_idx = arguments.index(FARE_TRACKERS_SUMMARY_FILE_FLAG) + 1
    try:
        return Path(arguments[path_arg_idx])
    except IndexError:
        logger.error("Invalid fare tracker summary file. For more information, try '--help'")
        sys.exit(2)


def set_up_check_in(arguments: list[str]) -> None:
    """
    Initialize reservation and account monitoring based on the configuration
    and arguments passed in
    """
    logger.debug("Called with %d arguments", len(arguments))

    config = GlobalConfig()
    config.initialize()

    if FARE_TRACKERS_ONCE_FLAG in arguments:
        check_fare_trackers_once(config, get_fare_trackers_summary_file(arguments))
        sys.exit()
    elif "--test-notifications" in arguments:
        test_notifications(config)
        sys.exit()
    elif len(arguments) == 2:
        logger.debug("Adding account through CLI arguments")
        account = {"username": arguments[0], "password": arguments[1]}
        config.create_account_config([account])
    elif len(arguments) == 3:
        logger.debug("Adding reservation through CLI arguments")
        reservation = {
            "confirmationNumber": arguments[0],
            "firstName": arguments[1],
            "lastName": arguments[2],
        }
        config.create_reservation_config([reservation])
    elif len(arguments) > 3:
        logger.error("Invalid arguments. For more information, try '--help'")
        sys.exit(2)

    num_accounts = len(config.accounts)
    num_reservations = len(config.reservations)
    num_fare_trackers = len(config.fare_trackers)
    logger.info(
        "Monitoring %s %s, %s %s, and %s standalone fare %s\n",
        num_accounts,
        pluralize("account", num_accounts),
        num_reservations,
        pluralize("reservation", num_reservations),
        num_fare_trackers,
        pluralize("tracker", num_fare_trackers),
    )

    lock = multiprocessing.Lock()
    set_up_accounts(config, lock)
    set_up_reservations(config, lock)
    set_up_fare_trackers(config, lock)

    # Keep the main process alive until all processes are done so it can handle
    # keyboard interrupts
    for process in multiprocessing.active_children():
        process.join()


def main(arguments: list[str], version: str) -> None:
    log.init_main_logging()
    logger.debug("Auto-Southwest Check-In %s", version)

    if IS_DOCKER:
        # Setting timezone to avoid Southwest fingerprinting (based on browser timezone)
        timezone = get_timezone()
        os.environ["TZ"] = timezone

    if CONFIG_UI_FLAG in arguments:
        from .config_ui import run_config_ui  # noqa: PLC0415

        run_config_ui(get_config_ui_port(arguments))
        return

    # Remove flags now that they are not needed (and will mess up parsing)
    flags_to_remove = ["--debug-screenshots", "-v", "--verbose"]
    arguments = [x for x in arguments if x not in flags_to_remove]

    try:
        set_up_check_in(arguments)
    except KeyboardInterrupt:
        logger.info("\nCtrl+C pressed. Stopping all check-ins")
        sys.exit(130)
