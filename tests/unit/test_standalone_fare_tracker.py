from pathlib import Path
from unittest import mock

import pytest
from pytest_mock import MockerFixture

from lib.config import TrackedFareConfig
from lib.notification_handler import NotificationHandler
from lib.standalone_fare_tracker import (
    BOOKING_PAGE_URL,
    BOOKING_SHOPPING_URL,
    BROWSER_FETCH_SCRIPT,
    FareTrackerState,
    FareTrackingError,
    HeaderSession,
    StandaloneFareClient,
    StandaloneFareMonitor,
    TrackedFare,
)
from lib.utils import DriverTimeoutError, RequestError


def create_tracker_config(flight_number: str | None = "1234") -> TrackedFareConfig:
    config = TrackedFareConfig()
    config.origin_airport = "PHX"
    config.destination_airport = "DEN"
    config.departure_date = "2026-08-15"
    config.flight_number = flight_number
    config.retrieval_interval = 24 * 3600
    config.notifications = []
    config.healthchecks_url = None
    return config


@pytest.fixture
def mock_header_session(mocker: MockerFixture) -> mock.Mock:
    header_session = mocker.patch("lib.standalone_fare_tracker.HeaderSession")
    header_session.headers = {"test": "headers"}
    return header_session


@pytest.fixture
def shopping_response() -> dict:
    return {
        "shoppingPage": {
            "cards": [
                {
                    "departureTime": "10:00",
                    "flightNumbers": "1234",
                    "fares": [
                        {
                            "_meta": {"fareProductId": "BASIC"},
                            "fareProductName": "Basic",
                            "price": {
                                "totalFare": {"amount": "120.00", "currencyCode": "USD"}
                            },
                        },
                        {
                            "_meta": {"fareProductId": "CHOICE"},
                            "fareProductName": "Choice",
                            "price": {
                                "totalFare": {"amount": "140.00", "currencyCode": "USD"}
                            },
                        },
                    ],
                },
                {
                    "departureTime": "12:00",
                    "flightNumbers": "2345",
                    "fares": [
                        {
                            "_meta": {"fareProductId": "BASIC"},
                            "fareProductName": "Basic",
                            "price": {
                                "totalFare": {"amount": "99.00", "currencyCode": "USD"}
                            },
                        }
                    ],
                },
            ]
        }
    }


class TestStandaloneFareClient:
    def test_get_current_fare_requests_shopping_results(
        self, mocker: MockerFixture, mock_header_session: mock.Mock, shopping_response: dict
    ) -> None:
        mock_make_request = mocker.patch.object(
            StandaloneFareClient, "_make_shopping_request", return_value=shopping_response
        )
        client = StandaloneFareClient(create_tracker_config(), mock_header_session)

        fare = client._get_current_fare("USD")

        assert fare.amount == 12000
        assert fare.flight_numbers == "1234"
        assert fare.fare_product_id == "BASIC"
        assert fare.fare_label == "Basic"

        request_query = mock_make_request.call_args[0][0]
        assert BOOKING_SHOPPING_URL == (
            "https://www.southwest.com/api/air-booking/v1/air-booking/page/air/booking/shopping"
        )
        assert request_query["fareType"] == "USD"
        assert request_query["originationAirportCode"] == "PHX"
        assert request_query["destinationAirportCode"] == "DEN"

    def test_make_shopping_request_uses_browser_session(
        self, mock_header_session: mock.Mock, shopping_response: dict
    ) -> None:
        mock_header_session.make_shopping_request.return_value = {
            "status": 200,
            "statusText": "OK",
            "body": '{"shoppingPage": {"cards": []}}',
        }
        client = StandaloneFareClient(create_tracker_config(), mock_header_session)

        response = client._make_shopping_request(client._get_search_query("USD"))

        assert response == {"shoppingPage": {"cards": []}}
        mock_header_session.make_shopping_request.assert_called_once()

    def test_route_date_tracker_selects_cheapest_fare(
        self, mock_header_session: mock.Mock, shopping_response: dict
    ) -> None:
        client = StandaloneFareClient(
            create_tracker_config(flight_number=None), mock_header_session
        )
        fares = client._normalize_response(shopping_response, "USD")

        selected_fare = client._select_fare(fares)

        assert selected_fare.amount == 9900
        assert selected_fare.flight_numbers == "2345"

    def test_exact_flight_tracker_raises_error_when_no_flight_matches(
        self, mock_header_session: mock.Mock, shopping_response: dict
    ) -> None:
        config = create_tracker_config("9999")
        client = StandaloneFareClient(config, mock_header_session)
        fares = client._normalize_response(shopping_response, "USD")

        with pytest.raises(FareTrackingError):
            client._select_fare(fares)

    def test_normalize_response_handles_points_and_unavailable_fares(
        self, mock_header_session: mock.Mock
    ) -> None:
        response = {
            "shoppingPage": {
                "cards": [
                    {
                        "departureTime": "10:00",
                        "flights": [{"number": "WN1234"}],
                        "fares": [
                            {
                                "_meta": {"fareProductId": "BASIC"},
                                "price": {
                                    "totalFare": {"amount": "8,000", "currencyCode": "POINTS"}
                                },
                            },
                            {"_meta": {"fareProductId": "CHOICE"}},
                        ],
                    }
                ]
            }
        }
        client = StandaloneFareClient(create_tracker_config(), mock_header_session)

        fares = client._normalize_response(response, "PTS")

        assert fares == [TrackedFare("PTS", 8000, "1234", "10:00", "BASIC", None)]

    def test_normalize_response_handles_live_shopping_response_shape(
        self, mock_header_session: mock.Mock
    ) -> None:
        response = {
            "data": {
                "searchResults": {
                    "airProducts": [
                        {
                            "details": [
                                {
                                    "departureTime": "18:55",
                                    "flightNumbers": ["4507"],
                                    "fareProducts": {
                                        "ADULT": {
                                            "WGA": {
                                                "productId": "product_id",
                                                "availabilityStatus": "AVAILABLE",
                                                "fare": {
                                                    "baseFare": {
                                                        "currencyCode": "USD",
                                                        "value": "183.26",
                                                    },
                                                    "totalFare": {
                                                        "currencyCode": "USD",
                                                        "value": "212.40",
                                                    }
                                                },
                                            },
                                            "BUS": {
                                                "productId": "business_product_id",
                                                "availabilityStatus": "AVAILABLE",
                                                "fare": {
                                                    "totalFare": {
                                                        "currencyCode": "USD",
                                                        "value": "397.40",
                                                    }
                                                },
                                            },
                                        }
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        }
        client = StandaloneFareClient(create_tracker_config("4507"), mock_header_session)

        fares = client._normalize_response(response, "USD")

        assert fares == [TrackedFare("USD", 21240, "4507", "18:55", "product_id", "WGA")]


class TestHeaderSession:
    def test_refresh_headers_keeps_browser_open_on_booking_page(
        self, mocker: MockerFixture
    ) -> None:
        monitor = mocker.Mock()
        driver = mocker.Mock()
        webdriver = mocker.patch("lib.standalone_fare_tracker.WebDriver").return_value
        webdriver.get_driver_with_headers.return_value = driver
        header_session = HeaderSession(monitor)

        header_session.refresh_headers()

        webdriver.get_driver_with_headers.assert_called_once()
        driver.get.assert_called_once_with(BOOKING_PAGE_URL)
        assert header_session.driver == driver

    def test_make_shopping_request_executes_fetch_in_browser(
        self, mocker: MockerFixture
    ) -> None:
        driver = mocker.Mock()
        driver.execute_async_script.return_value = {"status": 200, "body": "{}"}
        header_session = HeaderSession(mocker.Mock())
        header_session.driver = driver
        header_session.headers = {
            "X-API-Key": "api-key",
            "User-Agent": "browser-controlled",
            "Sec-Fetch-Site": "browser-controlled",
        }

        result = header_session.make_shopping_request({"fareType": "USD"})

        assert result == {"status": 200, "body": "{}"}
        driver.execute_async_script.assert_called_once_with(
            BROWSER_FETCH_SCRIPT,
            BOOKING_SHOPPING_URL,
            {"X-API-Key": "api-key"},
            {"fareType": "USD"},
        )

    def test_close_quits_browser(self, mocker: MockerFixture) -> None:
        driver = mocker.Mock()
        webdriver = mocker.Mock()
        header_session = HeaderSession(mocker.Mock())
        header_session.driver = driver
        header_session.webdriver = webdriver

        header_session.close()

        webdriver._quit_driver.assert_called_once_with(driver)
        assert header_session.driver is None
        assert header_session.webdriver is None


class TestFareTrackerState:
    def test_state_reads_and_saves_prices(self, tmp_path: Path) -> None:
        state = FareTrackerState(tmp_path / "state.json")
        fare = TrackedFare("USD", 12000, "1234", "10:00")

        state.save_prices("tracker", {"USD": fare})

        assert state.get_previous_prices("tracker") == {"USD": fare}

    def test_state_returns_empty_when_file_is_missing(self, tmp_path: Path) -> None:
        state = FareTrackerState(tmp_path / "missing.json")
        assert state.get_previous_prices("tracker") == {}


class TestStandaloneFareMonitor:
    @pytest.fixture(autouse=True)
    def set_up_monitor(self, mocker: MockerFixture) -> None:
        self.config = create_tracker_config()
        self.state = mocker.patch("lib.standalone_fare_tracker.FareTrackerState")
        self.monitor = StandaloneFareMonitor(self.config, state=self.state)

    def test_monitor_monitors_once_if_retrieval_interval_is_zero(
        self, mocker: MockerFixture
    ) -> None:
        mock_smart_sleep = mocker.patch.object(StandaloneFareMonitor, "_smart_sleep")
        mock_check = mocker.patch.object(StandaloneFareMonitor, "_check")

        self.monitor.config.retrieval_interval = 0
        self.monitor._monitor()

        mock_check.assert_called_once()
        mock_smart_sleep.assert_not_called()

    def test_check_initializes_state_without_notification(self, mocker: MockerFixture) -> None:
        current_fares = {"USD": TrackedFare("USD", 12000, "1234", "10:00")}
        mocker.patch.object(HeaderSession, "refresh_headers")
        mocker.patch.object(StandaloneFareClient, "get_current_fares", return_value=current_fares)
        self.state.get_previous_prices.return_value = {}
        mock_fare_drop = mocker.patch.object(NotificationHandler, "standalone_fare_drop")

        drops = self.monitor._check()

        assert drops == []
        mock_fare_drop.assert_not_called()
        self.state.save_prices.assert_called_once_with("PHX-DEN-2026-08-15-1234", current_fares)

    def test_check_sends_notification_on_price_drop(self, mocker: MockerFixture) -> None:
        previous_fares = {"USD": TrackedFare("USD", 12000, "1234", "10:00")}
        current_fares = {"USD": TrackedFare("USD", 9900, "1234", "10:00")}
        mocker.patch.object(HeaderSession, "refresh_headers")
        mocker.patch.object(StandaloneFareClient, "get_current_fares", return_value=current_fares)
        self.state.get_previous_prices.return_value = previous_fares
        mock_fare_drop = mocker.patch.object(NotificationHandler, "standalone_fare_drop")

        drops = self.monitor._check()

        assert len(drops) == 1
        assert drops[0].previous_fare == previous_fares["USD"]
        assert drops[0].current_fare == current_fares["USD"]
        mock_fare_drop.assert_called_once_with(previous_fares["USD"], current_fares["USD"])
        self.state.save_prices.assert_called_once_with("PHX-DEN-2026-08-15-1234", current_fares)

    def test_check_can_skip_individual_drop_notifications(self, mocker: MockerFixture) -> None:
        previous_fares = {"USD": TrackedFare("USD", 12000, "1234", "10:00")}
        current_fares = {"USD": TrackedFare("USD", 9900, "1234", "10:00")}
        mocker.patch.object(HeaderSession, "refresh_headers")
        mocker.patch.object(StandaloneFareClient, "get_current_fares", return_value=current_fares)
        self.state.get_previous_prices.return_value = previous_fares
        mock_fare_drop = mocker.patch.object(NotificationHandler, "standalone_fare_drop")

        drops = self.monitor._check(send_notifications=False)

        assert len(drops) == 1
        mock_fare_drop.assert_not_called()

    @pytest.mark.parametrize("exception", [DriverTimeoutError, RequestError(""), FareTrackingError])
    def test_check_catches_expected_errors(
        self, mocker: MockerFixture, exception: Exception
    ) -> None:
        mocker.patch.object(HeaderSession, "refresh_headers", side_effect=exception)
        mock_healthchecks_fail = mocker.patch.object(NotificationHandler, "healthchecks_fail")

        self.monitor._check()

        if exception is DriverTimeoutError:
            mock_healthchecks_fail.assert_not_called()
        else:
            mock_healthchecks_fail.assert_called_once()
