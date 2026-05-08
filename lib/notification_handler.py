from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

import apprise
import requests

from .log import get_logger
from .utils import LoginError, NotificationLevel, RequestError

if TYPE_CHECKING:
    from .flight import Flight
    from .reservation_monitor import AccountMonitor, ReservationMonitor
    from .config import TrackedFareConfig
    from .standalone_fare_tracker import StandaloneFareDrop, TrackedFare

MANUAL_CHECKIN_URL = "https://mobile.southwest.com/check-in"
MANAGE_RESERVATION_URL = "https://www.southwest.com/air/manage-reservation/"

FLIGHT_TIME_PLACEHOLDER = "FLIGHT_TIME"

logger = get_logger(__name__)


class NotificationHandler:
    """Handles all notifications that will be sent to the user either via Apprise or the console"""

    def __init__(self, reservation_monitor: AccountMonitor | ReservationMonitor) -> None:
        self.reservation_monitor = reservation_monitor
        self.notifications = reservation_monitor.config.notifications

    def send_notification(
        self,
        body: str,
        level: NotificationLevel = None,
        flights: list[Flight] | None = None,
        body_format: apprise.NotifyFormat = apprise.NotifyFormat.TEXT,
        console_body: str | None = None,
    ) -> None:
        """
        Send a notification to all configured services. The notification will only be sent if the
        level of the notification is greater than or equal to the level of the notification service.

        The flights parameter is necessary so the flight time format of each service is respected.
        """
        flights = flights or []

        # Print console messages with a 12-hour time format
        printed_body = self._format_flight_times(console_body or body, flights, False)
        print(printed_body)  # This isn't logged as it contains sensitive information

        title = "Auto Southwest Check-in Script"
        flights = flights or []

        for notification in self.notifications:
            # Only send the notification to levels that are greater than or equal to the level
            # of the notification. If level is none, it means the message will always be printed.
            # For example, this is used when the user tests notifications.
            if level and level < notification.level:
                continue

            # Replace any flight time placeholder with the actual flight times, according to the
            # notification's time format
            formatted_body = self._format_flight_times(
                body, flights, notification.twenty_four_hour_time
            )

            # Send each notification separately, as each message may contain different formatted
            # flight times
            apobj = apprise.Apprise(notification.url)
            notification_sent = apobj.notify(
                title=title, body=formatted_body, body_format=body_format
            )
            if not notification_sent:
                logger.error("A notification service reported delivery failure")

    def _format_flight_times(
        self, body: str, flights: list[Flight], twenty_four_hr_time: bool
    ) -> str:
        """
        Replace the flight time placeholder with the actual flight times, converting them to 24-hour
        time if necessary.
        """
        formatted_body = body
        for flight in flights:
            formatted_body = formatted_body.replace(
                FLIGHT_TIME_PLACEHOLDER, flight.get_display_time(twenty_four_hr_time), 1
            )

        return formatted_body

    def new_flights(self, flights: list[Flight]) -> None:
        # Don't send notifications if no new flights are scheduled
        if len(flights) == 0:
            return

        is_international = False
        flight_schedule_message = (
            "Successfully scheduled the following flights to check in for "
            f"{self._get_account_name()}:\n"
        )
        for flight in flights:
            flight_schedule_message += (
                f"Flight from {flight.departure_airport} to {flight.destination_airport} on "
                f"{FLIGHT_TIME_PLACEHOLDER}\n"
            )
            if flight.is_international:
                is_international = True

        if is_international:
            # Add an extra message for international flights to make sure people fill out their
            # passport information.
            flight_schedule_message += (
                "\nInternational flights were scheduled. Make sure to fill out your passport "
                "information before the check-in date\n"
            )

        logger.debug("Sending new flights notification")
        self.send_notification(flight_schedule_message, NotificationLevel.INFO, flights)

    def reaccommodated_flights(self, flights: list[Flight]) -> None:
        # Don't send notifications if no flights can be reaccommodated
        if len(flights) == 0:
            return

        flight_reaccommodation_message = (
            "The following flights are eligible to be changed at no cost for "
            f"{self._get_account_name()}!\nManage your reservations here: "
            f"{MANAGE_RESERVATION_URL}\n"
        )
        for flight in flights:
            flight_reaccommodation_message += (
                f"Flight from {flight.departure_airport} to {flight.destination_airport} on "
                f"{FLIGHT_TIME_PLACEHOLDER}\n"
            )

        logger.debug("Sending reaccommodated flights notification")
        self.send_notification(flight_reaccommodation_message, NotificationLevel.INFO, flights)

    def failed_reservation_retrieval(self, error: RequestError, confirmation_number: str) -> None:
        error_message = (
            f"Error: Failed to retrieve reservation for {self._get_account_name()} "
            f"with confirmation number {confirmation_number}. Reason: {error}.\n"
            "Make sure the reservation information is correct and try again.\n"
        )
        logger.debug("Sending failed reservation retrieval notification...")
        self.send_notification(error_message, NotificationLevel.ERROR)

    def timeout_during_retrieval(self, monitor_type: str) -> None:
        message = (
            f"Notice: Webdriver time out during {monitor_type} retrieval for "
            f"{self._get_account_name()}. Skipping reservation retrieval until next interval\n"
        )
        self.send_notification(message, NotificationLevel.NOTICE)

    def too_many_requests_during_login(self) -> None:
        message = (
            "Notice: Encountered a Too Many Requests error while logging in for "
            f"{self._get_account_name()}. Skipping reservation retrieval until next interval\n"
        )
        self.send_notification(message, NotificationLevel.NOTICE)

    def failed_login(self, error: LoginError) -> None:
        error_message = (
            "Error: Failed to log in to account with username "
            f"{self.reservation_monitor.username}. {error}.\n"
        )
        logger.debug("Sending failed login notification...")
        self.send_notification(error_message, NotificationLevel.ERROR)

    def successful_checkin(self, boarding_pass: dict[str, Any], flight: Flight) -> None:
        success_message = (
            f"Successfully checked in to flight from '{flight.departure_airport}' to "
            f"'{flight.destination_airport}' for {self._get_account_name()}!\n"
        )

        for flight_info in boarding_pass["flights"]:
            for passenger in flight_info["passengers"]:
                if passenger["boardingGroup"] is not None:
                    success_message += (
                        f"{passenger['name']} got "
                        f"{passenger['boardingGroup']}{passenger['boardingPosition']}!\n"
                    )

        logger.debug("Sending successful check-in notification...")
        self.send_notification(success_message, NotificationLevel.CHECKIN)

    def failed_checkin(self, error: RequestError, flight: Flight) -> None:
        error_message = (
            f"Error: Failed to check in to flight {flight.confirmation_number} for "
            f"{self._get_account_name()}. Reason: {error}.\nCheck in at this url: "
            f"{MANUAL_CHECKIN_URL}\n"
        )
        logger.debug("Sending failed check-in notification...")
        self.send_notification(error_message, NotificationLevel.ERROR)

    def airport_checkin_required(self, flight: Flight) -> None:
        error_message = (
            f"Error: Airport check-in is required for flight {flight.confirmation_number} for "
            f"{self._get_account_name()}.\n"
        )
        logger.debug("Sending airport check-in required notification...")
        self.send_notification(error_message, NotificationLevel.ERROR)

    def timeout_before_checkin(self, flight: Flight) -> None:
        error_message = (
            "Error: Timed out waiting for headers before check-in. Check-in to flight "
            f"{flight.confirmation_number} for {self._get_account_name()} at "
            f"{FLIGHT_TIME_PLACEHOLDER} may fail.\n"
        )
        logger.debug("Sending timeout before check-in notification...")
        self.send_notification(error_message, NotificationLevel.ERROR, [flight])

    def lower_fare(self, flight: Flight, price_info: str) -> None:
        message = (
            f"Found lower fare of {price_info} for flight {flight.confirmation_number} "
            f"from '{flight.departure_airport}' to '{flight.destination_airport}' on "
            f"{FLIGHT_TIME_PLACEHOLDER} for {self._get_account_name()}!\nManage your reservation "
            f"here: {MANAGE_RESERVATION_URL}\n"
        )
        logger.debug("Sending lower fare notification...")
        self.send_notification(message, NotificationLevel.INFO, [flight])

    def standalone_fare_drop(self, previous_fare: TrackedFare, current_fare: TrackedFare) -> None:
        config = self.reservation_monitor.config
        message = "Southwest fare drop found\n\n"
        message += self._format_standalone_fare_drop_details(config, previous_fare, current_fare)
        html_message = self._format_standalone_fare_drop_email(
            [(config, previous_fare, current_fare)]
        )

        logger.debug("Sending standalone lower fare notification...")
        self.send_notification(
            html_message,
            NotificationLevel.INFO,
            body_format=apprise.NotifyFormat.HTML,
            console_body=message,
        )

    def standalone_fare_drop_summary(self, drops: list[StandaloneFareDrop]) -> None:
        if not drops:
            return

        message = f"Southwest fare drops found: {len(drops)}\n"
        html_drops = []
        for index, drop in enumerate(drops, start=1):
            drop_details = self._format_standalone_fare_drop_details(
                drop.config, drop.previous_fare, drop.current_fare
            )
            message += f"\nDrop {index}\n{drop_details}"
            html_drops.append((drop.config, drop.previous_fare, drop.current_fare))

        html_message = self._format_standalone_fare_drop_email(html_drops)

        logger.debug("Sending standalone lower fare summary notification...")
        self.send_notification(
            html_message,
            NotificationLevel.INFO,
            body_format=apprise.NotifyFormat.HTML,
            console_body=message,
        )

    def _format_standalone_fare_drop_details(
        self,
        config: TrackedFareConfig,
        previous_fare: TrackedFare,
        current_fare: TrackedFare,
    ) -> str:
        configured_flight = config.flight_number or "Any"
        matched_flights = current_fare.flight_numbers.replace("\u200b", "")
        departure_time = current_fare.departure_time or "Unknown"

        return (
            f"Route: {config.origin_airport} -> {config.destination_airport}\n"
            f"Date: {config.departure_date}\n"
            f"Configured flight: {configured_flight}\n"
            f"Matched flight(s): {matched_flights}\n"
            f"Departure: {departure_time}\n"
            f"Price type: {current_fare.currency_code}\n"
            f"Previous: {self._format_standalone_price(previous_fare)}\n"
            f"Current: {self._format_standalone_price(current_fare)}\n"
            f"Savings: {self._format_standalone_savings(previous_fare, current_fare)}\n"
        )

    def _format_standalone_fare_drop_email(
        self, drops: list[tuple[TrackedFareConfig, TrackedFare, TrackedFare]]
    ) -> str:
        drop_count = len(drops)
        heading = "Southwest Fare Drop" if drop_count == 1 else "Southwest Fare Drops"
        subheading = (
            "A tracked fare is lower than your saved price."
            if drop_count == 1
            else f"{drop_count} tracked fares are lower than your saved prices."
        )
        cards = "\n".join(
            self._format_standalone_fare_drop_html_card(
                index, config, previous_fare, current_fare
            )
            for index, (config, previous_fare, current_fare) in enumerate(drops, start=1)
        )

        return f"""\
<div style="margin:0;padding:24px;background:#f4f6fb;color:#1f2937;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #d9e2f0;border-radius:12px;overflow:hidden;">
    <div style="background:#304cb2;color:#ffffff;padding:22px 24px;border-bottom:5px solid #ffbf27;">
      <div style="font-size:13px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#ffec99;">Auto Southwest</div>
      <h1 style="margin:8px 0 0;font-size:26px;line-height:1.2;font-weight:800;">{heading}</h1>
      <p style="margin:8px 0 0;font-size:15px;line-height:1.5;color:#eaf0ff;">{escape(subheading)}</p>
    </div>
    <div style="padding:20px 24px 24px;">
      {cards}
    </div>
  </div>
</div>
"""

    def _format_standalone_fare_drop_html_card(
        self,
        index: int,
        config: TrackedFareConfig,
        previous_fare: TrackedFare,
        current_fare: TrackedFare,
    ) -> str:
        configured_flight = config.flight_number or "Any"
        matched_flights = current_fare.flight_numbers.replace("\u200b", "")
        departure_time = current_fare.departure_time or "Unknown"
        previous_price = self._format_standalone_price(previous_fare)
        current_price = self._format_standalone_price(current_fare)
        savings = self._format_standalone_savings(previous_fare, current_fare)

        return f"""\
      <div style="border:1px solid #d9e2f0;border-radius:10px;margin:0 0 16px;overflow:hidden;background:#ffffff;">
        <div style="padding:14px 16px;background:#f8fafc;border-bottom:1px solid #e5e7eb;">
          <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#64748b;">Drop {index}</div>
          <div style="font-size:22px;font-weight:800;color:#111827;margin-top:4px;">{escape(config.origin_airport)} &rarr; {escape(config.destination_airport)}</div>
          <div style="font-size:14px;color:#4b5563;margin-top:4px;">{escape(str(config.departure_date))}</div>
        </div>
        <div style="padding:16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
            <tr>
              <td style="padding:6px 0;color:#64748b;font-size:13px;">Configured flight</td>
              <td style="padding:6px 0;text-align:right;color:#111827;font-size:14px;font-weight:700;">{escape(configured_flight)}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#64748b;font-size:13px;">Matched flight(s)</td>
              <td style="padding:6px 0;text-align:right;color:#111827;font-size:14px;font-weight:700;">{escape(matched_flights)}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#64748b;font-size:13px;">Departure</td>
              <td style="padding:6px 0;text-align:right;color:#111827;font-size:14px;font-weight:700;">{escape(departure_time)}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#64748b;font-size:13px;">Price type</td>
              <td style="padding:6px 0;text-align:right;color:#111827;font-size:14px;font-weight:700;">{escape(current_fare.currency_code)}</td>
            </tr>
          </table>
          <div style="margin-top:14px;padding:14px;border-radius:10px;background:#ecfdf5;border:1px solid #bbf7d0;">
            <div style="font-size:13px;color:#047857;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;">New lower price</div>
            <div style="margin-top:6px;font-size:30px;line-height:1.1;font-weight:900;color:#065f46;">{escape(current_price)}</div>
            <div style="margin-top:6px;font-size:14px;color:#475569;">Previous: <span style="text-decoration:line-through;">{escape(previous_price)}</span></div>
            <div style="display:inline-block;margin-top:10px;padding:6px 10px;border-radius:999px;background:#fffbeb;color:#92400e;font-size:13px;font-weight:800;">Saved {escape(savings)}</div>
          </div>
        </div>
      </div>
"""

    def _format_standalone_price(self, fare: TrackedFare) -> str:
        if fare.currency_code == "USD":
            return f"${fare.amount / 100:.2f}"

        return f"{fare.amount:,} {fare.currency_code}"

    def _format_standalone_savings(
        self, previous_fare: TrackedFare, current_fare: TrackedFare
    ) -> str:
        savings = previous_fare.amount - current_fare.amount
        if current_fare.currency_code == "USD":
            return f"${savings / 100:.2f}"

        return f"{savings:,} {current_fare.currency_code}"

    def healthchecks_success(self, data: str) -> None:
        if self.reservation_monitor.config.healthchecks_url is not None:
            requests.post(self.reservation_monitor.config.healthchecks_url, data=data)

    def healthchecks_fail(self, data: str) -> None:
        if self.reservation_monitor.config.healthchecks_url is not None:
            requests.post(self.reservation_monitor.config.healthchecks_url + "/fail", data=data)

    def _get_account_name(self) -> str:
        return self.reservation_monitor.get_account_name()
