"""Runs standalone fare shopping requests against mocked Southwest responses"""

from requests_mock.mocker import Mocker as RequestMocker

from lib.config import TrackedFareConfig
from lib.standalone_fare_tracker import BOOKING_SHOPPING_URL, StandaloneFareClient
from lib.utils import BASE_URL

SHOPPING_URL = BASE_URL + BOOKING_SHOPPING_URL


def create_config(flight_number: str | None = "1234") -> TrackedFareConfig:
    config = TrackedFareConfig()
    config.origin_airport = "PHX"
    config.destination_airport = "DEN"
    config.departure_date = "2026-08-15"
    config.flight_number = flight_number
    return config


def test_standalone_fare_client_retrieves_cash_and_points(
    requests_mock: RequestMocker,
) -> None:
    cash_response = {
        "shoppingPage": {
            "cards": [
                {
                    "departureTime": "10:00",
                    "flightNumbers": "1234",
                    "fares": [
                        {
                            "_meta": {"fareProductId": "BASIC"},
                            "price": {
                                "totalFare": {"amount": "120.00", "currencyCode": "USD"}
                            },
                        }
                    ],
                }
            ]
        }
    }
    points_response = {
        "shoppingPage": {
            "cards": [
                {
                    "departureTime": "10:00",
                    "flightNumbers": "1234",
                    "fares": [
                        {
                            "_meta": {"fareProductId": "BASIC"},
                            "price": {
                                "totalFare": {"amount": "8,000", "currencyCode": "PTS"}
                            },
                        }
                    ],
                }
            ]
        }
    }
    requests_mock.post(
        SHOPPING_URL,
        [
            {"json": cash_response, "status_code": 200},
            {"json": points_response, "status_code": 200},
        ],
    )

    header_session = type("HeaderSession", (), {"headers": {}})()
    client = StandaloneFareClient(create_config(), header_session)

    fares = client.get_current_fares()

    assert fares["USD"].amount == 12000
    assert fares["PTS"].amount == 8000


def test_standalone_fare_client_skips_unavailable_currency(
    requests_mock: RequestMocker,
) -> None:
    cash_response = {
        "shoppingPage": {
            "cards": [
                {
                    "departureTime": "10:00",
                    "flightNumbers": "1234",
                    "fares": [
                        {
                            "_meta": {"fareProductId": "BASIC"},
                            "price": {
                                "totalFare": {"amount": "120.00", "currencyCode": "USD"}
                            },
                        }
                    ],
                }
            ]
        }
    }
    empty_response = {"shoppingPage": {"cards": []}}
    requests_mock.post(
        SHOPPING_URL,
        [{"json": cash_response, "status_code": 200}, {"json": empty_response, "status_code": 200}],
    )

    header_session = type("HeaderSession", (), {"headers": {}})()
    client = StandaloneFareClient(create_config(), header_session)

    fares = client.get_current_fares()

    assert list(fares) == ["USD"]
