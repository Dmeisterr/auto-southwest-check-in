"""Runs standalone fare shopping requests against mocked Southwest responses"""

import json

from lib.config import TrackedFareConfig
from lib.standalone_fare_tracker import StandaloneFareClient


def create_config(flight_number: str | None = "1234") -> TrackedFareConfig:
    config = TrackedFareConfig()
    config.origin_airport = "PHX"
    config.destination_airport = "DEN"
    config.departure_date = "2026-08-15"
    config.flight_number = flight_number
    return config


class MockHeaderSession:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses

    def make_shopping_request(self, query: dict) -> dict:
        response = self.responses.pop(0)
        return {"status": 200, "statusText": "OK", "body": json.dumps(response)}


def test_standalone_fare_client_retrieves_cash_and_points() -> None:
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

    header_session = MockHeaderSession([cash_response, points_response])
    client = StandaloneFareClient(create_config(), header_session)

    fares = client.get_current_fares()

    assert fares["USD"].amount == 12000
    assert fares["PTS"].amount == 8000


def test_standalone_fare_client_skips_unavailable_currency() -> None:
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

    header_session = MockHeaderSession([cash_response, empty_response])
    client = StandaloneFareClient(create_config(), header_session)

    fares = client.get_current_fares()

    assert list(fares) == ["USD"]
