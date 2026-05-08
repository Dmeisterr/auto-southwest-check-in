from __future__ import annotations

import json
import multiprocessing
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .log import LOGS_DIRECTORY, get_logger
from .notification_handler import NotificationHandler
from .utils import DriverTimeoutError, RequestError, make_request
from .webdriver import WebDriver

if TYPE_CHECKING:
    from .config import TrackedFareConfig

JSON = dict[str, Any]

BOOKING_SHOPPING_URL = "mobile-air-booking/v1/mobile-air-booking/page/air/booking/shopping"
CURRENCIES = ("USD", "PTS")
STATE_FILE_PATH = Path(LOGS_DIRECTORY) / "fare-tracker-state.json"

logger = get_logger(__name__)


class FareTrackingError(Exception):
    """A custom exception when standalone fare tracking cannot find a usable fare"""


@dataclass
class TrackedFare:
    currency_code: str
    amount: int
    flight_numbers: str
    departure_time: str
    fare_product_id: str | None = None
    fare_label: str | None = None


class HeaderSession:
    """Small adapter that lets the existing WebDriver header flow serve fare trackers."""

    def __init__(self, monitor: StandaloneFareMonitor) -> None:
        self.reservation_monitor = monitor
        self.headers = {}

    def refresh_headers(self) -> None:
        logger.debug("Refreshing headers for standalone fare tracker")
        webdriver = WebDriver(self)
        webdriver.set_headers()


class FareTrackerState:
    def __init__(self, state_file_path: Path = STATE_FILE_PATH) -> None:
        self.state_file_path = state_file_path

    def get_previous_prices(self, tracker_key: str) -> dict[str, TrackedFare]:
        state = self._read_state()
        return {
            currency: TrackedFare(**fare)
            for currency, fare in state.get(tracker_key, {}).items()
            if fare is not None
        }

    def save_prices(self, tracker_key: str, fares: dict[str, TrackedFare]) -> None:
        state = self._read_state()
        state[tracker_key] = {currency: asdict(fare) for currency, fare in fares.items()}

        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_file_path.write_text(json.dumps(state, indent=4, sort_keys=True) + "\n")

    def _read_state(self) -> JSON:
        try:
            return json.loads(self.state_file_path.read_text())
        except FileNotFoundError:
            return {}
        except json.decoder.JSONDecodeError:
            logger.warning("Standalone fare tracker state file is invalid. Starting fresh")
            return {}


class StandaloneFareClient:
    def __init__(self, config: TrackedFareConfig, header_session: HeaderSession) -> None:
        self.config = config
        self.header_session = header_session

    def get_current_fares(self) -> dict[str, TrackedFare]:
        fares = {}
        for currency in CURRENCIES:
            try:
                fare = self._get_current_fare(currency)
            except FareTrackingError as err:
                logger.debug(
                    "%s fare unavailable for %s: %s", currency, self.get_tracker_key(), err
                )
                continue

            fares[currency] = fare

        if not fares:
            raise FareTrackingError("No fares were found for standalone fare tracker")

        return fares

    def get_tracker_key(self) -> str:
        flight_number = self.config.flight_number or "ANY"
        return (
            f"{self.config.origin_airport}-{self.config.destination_airport}-"
            f"{self.config.departure_date}-{flight_number}"
        )

    def _get_current_fare(self, currency: str) -> TrackedFare:
        query = self._get_search_query(currency)
        response = make_request("POST", BOOKING_SHOPPING_URL, self.header_session.headers, query)
        fares = self._normalize_response(response, currency)
        return self._select_fare(fares)

    def _get_search_query(self, currency: str) -> JSON:
        return {
            "adultPassengersCount": "1",
            "departureDate": self.config.departure_date,
            "departureTimeOfDay": "ALL_DAY",
            "destinationAirportCode": self.config.destination_airport,
            "originationAirportCode": self.config.origin_airport,
            "promoCode": "",
            "returnTimeOfDay": "ALL_DAY",
            "tripType": "oneway",
            "currencyType": currency,
        }

    def _normalize_response(self, response: JSON, currency: str) -> list[TrackedFare]:
        fares = []
        for card in self._iter_flight_cards(response):
            flight_numbers = self._parse_flight_numbers(card)
            departure_time = self._parse_departure_time(card)
            fare = self._get_lowest_fare(card.get("fares"), currency)

            if fare is None:
                continue

            fares.append(
                TrackedFare(
                    currency_code=currency,
                    amount=fare["amount"],
                    flight_numbers=flight_numbers,
                    departure_time=departure_time,
                    fare_product_id=fare.get("fare_product_id"),
                    fare_label=fare.get("fare_label"),
                )
            )

        return fares

    def _iter_flight_cards(self, data: Any) -> list[JSON]:
        if isinstance(data, list):
            cards = []
            for item in data:
                cards.extend(self._iter_flight_cards(item))
            return cards

        if not isinstance(data, dict):
            return []

        if "fares" in data and ("flightNumbers" in data or "flights" in data):
            return [data]

        cards = []
        for value in data.values():
            cards.extend(self._iter_flight_cards(value))

        return cards

    def _parse_flight_numbers(self, card: JSON) -> str:
        flight_numbers = card.get("flightNumbers")
        if flight_numbers:
            return str(flight_numbers)

        flights = card.get("flights") or []
        numbers = []
        for flight in flights:
            number = str(flight.get("number", "")).removeprefix("WN")
            if number:
                numbers.append(number)

        return "\u200b/\u200b".join(numbers)

    def _parse_departure_time(self, card: JSON) -> str:
        return str(card.get("departureTime") or card.get("departureTimeFormatted") or "")

    def _get_lowest_fare(self, fares: list[JSON] | None, currency: str) -> JSON | None:
        lowest_fare = None
        for fare in fares or []:
            amount = self._find_amount(fare, currency)
            if amount is None:
                continue

            normalized_fare = {
                "amount": amount,
                "fare_product_id": fare.get("_meta", {}).get("fareProductId")
                or fare.get("fareProductId"),
                "fare_label": self._find_fare_label(fare),
            }

            if lowest_fare is None or normalized_fare["amount"] < lowest_fare["amount"]:
                lowest_fare = normalized_fare

        return lowest_fare

    def _find_amount(self, value: Any, currency: str) -> int | None:
        if isinstance(value, list):
            for item in value:
                amount = self._find_amount(item, currency)
                if amount is not None:
                    return amount

            return None

        if not isinstance(value, dict):
            return None

        currency_code = str(value.get("currencyCode", "")).upper()
        if currency_code == currency:
            amount = self._parse_amount(value, currency)
            if amount is not None:
                return amount

        for child in value.values():
            amount = self._find_amount(child, currency)
            if amount is not None:
                return amount

        return None

    def _parse_amount(self, price_info: JSON, currency: str) -> int | None:
        amount = price_info.get("amount") or price_info.get("value")
        if amount is None:
            return None

        amount_string = str(amount).replace(",", "").replace("$", "")
        sign = price_info.get("sign", "")

        try:
            parsed_amount = Decimal(sign + amount_string)
        except InvalidOperation:
            return None

        if currency == "USD":
            return int(parsed_amount * 100)

        return int(parsed_amount)

    def _find_fare_label(self, fare: JSON) -> str | None:
        for key in ["fareProductName", "fareProductLabel", "label", "title"]:
            if fare.get(key):
                return str(fare[key])

        return None

    def _select_fare(self, fares: list[TrackedFare]) -> TrackedFare:
        matching_fares = fares
        if self.config.flight_number:
            configured_flight_number = self._normalize_flight_number(self.config.flight_number)
            matching_fares = [
                fare
                for fare in fares
                if self._normalize_flight_number(fare.flight_numbers) == configured_flight_number
            ]

        if not matching_fares:
            raise FareTrackingError("No matching flight fares were found")

        return min(matching_fares, key=lambda fare: fare.amount)

    def _normalize_flight_number(self, flight_number: str) -> str:
        return flight_number.replace("\u200b", "").replace("WN", "")


class StandaloneFareMonitor:
    def __init__(
        self,
        config: TrackedFareConfig,
        lock: multiprocessing.Lock | None = None,
        state: FareTrackerState | None = None,
    ) -> None:
        self.config = config
        self.lock = lock
        self.notification_handler = NotificationHandler(self)
        self.header_session = HeaderSession(self)
        self.client = StandaloneFareClient(config, self.header_session)
        self.state = state or FareTrackerState()

    def start(self) -> None:
        process = multiprocessing.Process(target=self.monitor)
        process.start()

    def monitor(self) -> None:
        try:
            self._monitor()
        except KeyboardInterrupt:
            time.sleep(0.05)
            with self._get_lock():
                self._stop_monitoring()

    def _monitor(self) -> None:
        while True:
            time_before = time.monotonic()

            logger.debug("Acquiring lock...")
            with self._get_lock():
                logger.debug("Lock acquired")
                self._check()

                if self.config.retrieval_interval <= 0:
                    logger.debug("Monitoring is disabled as retrieval interval is 0")
                    break

            logger.debug("Lock released")
            self._smart_sleep(time_before)

    def _check(self) -> None:
        tracker_key = self.client.get_tracker_key()
        try:
            self.header_session.refresh_headers()
            current_fares = self.client.get_current_fares()
            previous_fares = self.state.get_previous_prices(tracker_key)
            self._notify_on_drops(previous_fares, current_fares)
            self.state.save_prices(tracker_key, current_fares)
            self.notification_handler.healthchecks_success(
                f"Successful standalone fare check,\ntracker = {tracker_key}"
            )
        except DriverTimeoutError:
            logger.warning("Timeout while refreshing headers. Skipping standalone fare check")
            self.notification_handler.timeout_during_retrieval("standalone fare")
        except (RequestError, FareTrackingError) as err:
            logger.error("Error during standalone fare check. %s. Skipping...", err)
            self.notification_handler.healthchecks_fail(
                f"Failed standalone fare check,\ntracker = {tracker_key}"
            )
        except Exception as err:
            logger.exception("Unexpected error during standalone fare check: %s", repr(err))
            self.notification_handler.healthchecks_fail(
                f"Failed standalone fare check,\ntracker = {tracker_key}"
            )

    def _notify_on_drops(
        self, previous_fares: dict[str, TrackedFare], current_fares: dict[str, TrackedFare]
    ) -> None:
        for currency, current_fare in current_fares.items():
            previous_fare = previous_fares.get(currency)
            if previous_fare is not None and current_fare.amount < previous_fare.amount:
                self.notification_handler.standalone_fare_drop(previous_fare, current_fare)

    def _smart_sleep(self, previous_time: float) -> None:
        time_taken = time.monotonic() - previous_time
        sleep_time = max(self.config.retrieval_interval - time_taken, 0)
        logger.debug("Sleeping for %d seconds", sleep_time)
        time.sleep(sleep_time)

    def _get_lock(self) -> multiprocessing.Lock | nullcontext:
        return self.lock if self.lock is not None else nullcontext()

    def get_account_name(self) -> str:
        return self.client.get_tracker_key()

    def _stop_monitoring(self) -> None:
        print(f"\nStopping standalone fare tracking for {self.get_account_name()}")
