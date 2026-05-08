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
from .utils import DriverTimeoutError, RequestError, random_sleep_duration, time
from .webdriver import WebDriver

if TYPE_CHECKING:
    from .config import TrackedFareConfig

JSON = dict[str, Any]

BOOKING_SHOPPING_URL = (
    "https://www.southwest.com/api/air-booking/v1/air-booking/page/air/booking/shopping"
)
BOOKING_PAGE_URL = "https://www.southwest.com/air/booking/"
CURRENCIES = ("USD", "PTS")
REQUEST_FARE_TYPES = {"USD": "USD", "PTS": "POINTS"}
STATE_FILE_PATH = Path(LOGS_DIRECTORY) / "fare-tracker-state.json"
BROWSER_CONTROLLED_HEADER_PREFIXES = ("proxy-", "sec-")
BROWSER_CONTROLLED_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "cookie",
    "host",
    "origin",
    "referer",
    "user-agent",
}
BROWSER_FETCH_SCRIPT = """
const url = arguments[0];
const headers = arguments[1];
const payload = arguments[2];
const done = arguments[3];
fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
        ...headers,
        "Accept": "application/json",
        "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
}).then(async (response) => {
    done({
        status: response.status,
        statusText: response.statusText,
        body: await response.text()
    });
}).catch((error) => {
    done({error: String(error)});
});
"""

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


@dataclass
class StandaloneFareDrop:
    config: TrackedFareConfig
    previous_fare: TrackedFare
    current_fare: TrackedFare


class HeaderSession:
    """Small adapter that lets fare trackers use Southwest requests from a browser session."""

    def __init__(self, monitor: StandaloneFareMonitor) -> None:
        self.reservation_monitor = monitor
        self.headers = {}
        self.driver = None
        self.webdriver = None

    def refresh_headers(self) -> None:
        logger.debug("Refreshing headers for standalone fare tracker")
        self.close()
        self.webdriver = WebDriver(self)
        self.driver = self.webdriver.get_driver_with_headers()
        logger.debug("Loading Southwest booking page for browser-context shopping requests")
        self.driver.get(BOOKING_PAGE_URL)

    def make_shopping_request(self, query: JSON) -> JSON:
        if self.driver is None:
            raise RequestError("Browser session is not active")

        result = self.driver.execute_async_script(
            BROWSER_FETCH_SCRIPT,
            BOOKING_SHOPPING_URL,
            self._get_browser_fetch_headers(),
            query,
        )
        if not isinstance(result, dict):
            raise RequestError("Browser shopping request returned an invalid response")

        if result.get("error"):
            raise RequestError(str(result["error"]))

        return result

    def close(self) -> None:
        if self.driver is not None and self.webdriver is not None:
            self.webdriver._quit_driver(self.driver)

        self.driver = None
        self.webdriver = None

    def _get_browser_fetch_headers(self) -> JSON:
        headers = {}
        for key, value in self.headers.items():
            lower_key = key.lower()
            if lower_key in BROWSER_CONTROLLED_HEADERS:
                continue

            if lower_key.startswith(BROWSER_CONTROLLED_HEADER_PREFIXES):
                continue

            headers[key] = value

        return headers


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
        saved_fares = state.get(tracker_key, {})
        if not isinstance(saved_fares, dict):
            saved_fares = {}

        updated_fares = dict(saved_fares)
        for currency, fare in fares.items():
            saved_fare = self._tracked_fare_from_state(saved_fares.get(currency))
            if saved_fare is None or fare.amount < saved_fare.amount:
                updated_fares[currency] = asdict(fare)

        state[tracker_key] = updated_fares

        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_file_path.write_text(json.dumps(state, indent=4, sort_keys=True) + "\n")

    def _tracked_fare_from_state(self, fare: Any) -> TrackedFare | None:
        if not isinstance(fare, dict):
            return None

        try:
            return TrackedFare(**fare)
        except TypeError:
            return None

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
        response = self._make_shopping_request(query)
        fares = self._normalize_response(response, currency)
        return self._select_fare(fares)

    def _get_search_query(self, currency: str) -> JSON:
        return {
            "adultPassengersCount": "1",
            "application": "air-booking",
            "departureDate": self.config.departure_date,
            "departureTimeOfDay": "ALL_DAY",
            "destinationAirportCode": self.config.destination_airport,
            "fareType": REQUEST_FARE_TYPES[currency],
            "int": "HOMEQBOMAIR",
            "originationAirportCode": self.config.origin_airport,
            "passengerType": "ADULT",
            "promoCode": "",
            "reset": "true",
            "returnAirportCode": "",
            "returnDate": "",
            "returnTimeOfDay": "ALL_DAY",
            "seniorPassengersCount": "0",
            "site": "southwest",
            "tripType": "oneway",
        }

    def _make_shopping_request(self, query: JSON, max_attempts: int = 7) -> JSON:
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            try:
                response = self.header_session.make_shopping_request(query)
                response_status = int(response.get("status", 0))
                response_body = str(response.get("body", ""))
                if response_status == 200:
                    logger.debug("Successfully made shopping request after %d attempts", attempts)
                    return json.loads(response_body)

                response_status_text = response.get("statusText") or "Error"
                error_msg = f"{response_status_text} ({response_status})"
            except Exception as err:
                response_body = ""
                error_msg = str(err)

            sleep_time = random_sleep_duration(1, 3)
            logger.debug(
                "Shopping request error on attempt %d: %s. Sleeping for %.2f seconds",
                attempts,
                error_msg,
                sleep_time,
            )
            time.sleep(sleep_time)

        logger.debug("Failed to make shopping request after %d attempts: %s", attempts, error_msg)
        logger.debug("Shopping response body: %s", response_body)
        raise RequestError(error_msg, response_body)

    def _normalize_response(self, response: JSON, currency: str) -> list[TrackedFare]:
        fares = []
        for card in self._iter_flight_cards(response):
            flight_numbers = self._parse_flight_numbers(card)
            departure_time = self._parse_departure_time(card)
            fare = self._get_lowest_fare(card.get("fares") or card.get("fareProducts"), currency)

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

        if ("fares" in data or "fareProducts" in data) and (
            "flightNumbers" in data or "flights" in data
        ):
            return [data]

        cards = []
        for value in data.values():
            cards.extend(self._iter_flight_cards(value))

        return cards

    def _parse_flight_numbers(self, card: JSON) -> str:
        flight_numbers = card.get("flightNumbers")
        if flight_numbers:
            if isinstance(flight_numbers, list):
                return "\u200b/\u200b".join(str(number) for number in flight_numbers)

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

    def _get_lowest_fare(self, fares: list[JSON] | JSON | None, currency: str) -> JSON | None:
        lowest_fare = None
        for fare_product_id, fare in self._iter_fares(fares):
            if fare.get("availabilityStatus") not in [None, "AVAILABLE"]:
                continue

            amount = self._find_amount(fare, currency)
            if amount is None:
                continue

            normalized_fare = {
                "amount": amount,
                "fare_product_id": fare.get("_meta", {}).get("fareProductId")
                or fare.get("fareProductId")
                or fare.get("productId")
                or fare_product_id,
                "fare_label": self._find_fare_label(fare) or fare_product_id,
            }

            if lowest_fare is None or normalized_fare["amount"] < lowest_fare["amount"]:
                lowest_fare = normalized_fare

        return lowest_fare

    def _iter_fares(self, fares: list[JSON] | JSON | None) -> list[tuple[str | None, JSON]]:
        if fares is None:
            return []

        if isinstance(fares, list):
            return [(None, fare) for fare in fares]

        if not isinstance(fares, dict):
            return []

        fare_products = fares.get("ADULT", fares)
        return [(fare_product_id, fare) for fare_product_id, fare in fare_products.items()]

    def _find_amount(self, value: Any, currency: str) -> int | None:
        if isinstance(value, list):
            for item in value:
                amount = self._find_amount(item, currency)
                if amount is not None:
                    return amount

            return None

        if not isinstance(value, dict):
            return None

        for price_key in ["totalFare", "discountedTotalFare", "priceTotal"]:
            price = value.get(price_key)
            if isinstance(price, dict) and self._currency_matches(price, currency):
                amount = self._parse_amount(price, currency)
                if amount is not None:
                    return amount

        if self._currency_matches(value, currency):
            amount = self._parse_amount(value, currency)
            if amount is not None:
                return amount

        for child in value.values():
            amount = self._find_amount(child, currency)
            if amount is not None:
                return amount

        return None

    def _currency_matches(self, price: JSON, currency: str) -> bool:
        currency_code = str(price.get("currencyCode", "")).upper()
        return currency_code == currency or (currency == "PTS" and currency_code == "POINTS")

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
            matching_fares = [
                fare
                for fare in fares
                if self._flight_numbers_match(self.config.flight_number, fare.flight_numbers)
            ]

        if not matching_fares:
            raise FareTrackingError("No matching flight fares were found")

        return min(matching_fares, key=lambda fare: fare.amount)

    def _flight_numbers_match(self, configured_flight_numbers: str, fare_flight_numbers: str) -> bool:
        configured_numbers = self._parse_configured_flight_numbers(configured_flight_numbers)
        fare_numbers = self._parse_configured_flight_numbers(fare_flight_numbers)

        if not configured_numbers or not fare_numbers:
            return False

        if len(configured_numbers) == 1:
            return configured_numbers[0] in fare_numbers

        return configured_numbers == fare_numbers

    def _parse_configured_flight_numbers(self, flight_numbers: str) -> list[str]:
        return [
            self._normalize_flight_number(number)
            for number in flight_numbers.replace("\u200b", "").replace("WN", "").split("/")
            if self._normalize_flight_number(number)
        ]

    def _normalize_flight_number(self, flight_number: str) -> str:
        return flight_number.strip().replace("\u200b", "").removeprefix("WN")


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

    def _check(self, send_notifications: bool = True) -> list[StandaloneFareDrop]:
        tracker_key = self.client.get_tracker_key()
        try:
            self.header_session.refresh_headers()
            current_fares = self.client.get_current_fares()
            previous_fares = self.state.get_previous_prices(tracker_key)
            drops = self._get_fare_drops(previous_fares, current_fares)
            if send_notifications:
                self._notify_on_drops(drops)
            self.state.save_prices(tracker_key, current_fares)
            self.notification_handler.healthchecks_success(
                f"Successful standalone fare check,\ntracker = {tracker_key}"
            )
            return drops
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
        finally:
            self.header_session.close()

        return []

    def _get_fare_drops(
        self, previous_fares: dict[str, TrackedFare], current_fares: dict[str, TrackedFare]
    ) -> list[StandaloneFareDrop]:
        drops = []
        for currency, current_fare in current_fares.items():
            previous_fare = previous_fares.get(currency)
            if previous_fare is not None and current_fare.amount < previous_fare.amount:
                drops.append(StandaloneFareDrop(self.config, previous_fare, current_fare))

        return drops

    def _notify_on_drops(self, drops: list[StandaloneFareDrop]) -> None:
        for drop in drops:
            self.notification_handler.standalone_fare_drop(
                drop.previous_fare, drop.current_fare
            )

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
