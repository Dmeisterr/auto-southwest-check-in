import copy
import json
import zoneinfo
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from lib.flight import Flight

TEST_AIRPORT_INFO = {
    "AMD": {"timezone": "Asia/Kolkata", "name": "Ahmedabad"},
    "IBZ": {"timezone": "Europe/Madrid", "name": "Ibiza"},
}

TEST_FLIGHT_INFO = {
    "international": True,
    "segments": [
        {
            "origination_airport_code": "AMD",
            "destination_airport_code": "IBZ",
            "depart_at": "1999-12-31T23:59:00.000+05:30",
            "flight_number": "1000",
        }
    ],
}


@pytest.fixture(autouse=True)
def _set_airport_info_mock(mocker: MockerFixture) -> None:
    mocker.patch("pathlib.Path.read_text", return_value=json.dumps(TEST_AIRPORT_INFO))


class TestFlight:
    @pytest.fixture(autouse=True)
    def _set_up_flight(self) -> None:
        # Reservation info can be left empty as it is only used for caching, but isn't relevant to
        # the functionality of the flight class
        self.flight = Flight(TEST_FLIGHT_INFO, {}, "test_num")

    def test_flights_with_the_same_flight_numbers_and_departure_times_are_equal(self) -> None:
        flight1 = Flight(TEST_FLIGHT_INFO, {}, "")
        flight2 = Flight(TEST_FLIGHT_INFO, {}, "")
        assert flight1 == flight2

    @pytest.mark.parametrize(
        ("flight_num", "departure_time"),
        [
            # Test different flight numbers
            ("2000", "1999-12-31T23:59:00.000+05:30"),
            # Test different departure times
            ("1000", "1999-12-31T16:59:00.000+05:30"),
        ],
    )
    def test_flights_with_different_flight_numbers_or_departure_times_are_not_equal(
        self, flight_num: str, departure_time: str
    ) -> None:
        flight_info = copy.deepcopy(TEST_FLIGHT_INFO)
        flight_info["segments"][0]["flight_number"] = flight_num
        flight_info["segments"][0]["depart_at"] = departure_time

        new_flight = Flight(flight_info, {}, "")
        assert self.flight != new_flight

    @pytest.mark.parametrize(
        "can_reaccom",
        [True, False],
    )
    def test_flight_can_be_reaccomodated(self, can_reaccom: bool) -> None:
        self.flight.reservation_info = {"permissions": {"can_reaccom": can_reaccom}}
        assert self.flight.can_be_reaccommodated == can_reaccom

    @pytest.mark.parametrize(
        ("twenty_four_hr", "expected_time"), [(True, "13:59"), (False, "1:59 PM")]
    )
    def test_get_display_time_formats_time_correctly(
        self, twenty_four_hr: bool, expected_time: str
    ) -> None:
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        self.flight._local_departure_time = datetime(1999, 12, 31, 13, 59, tzinfo=tz)
        assert self.flight.get_display_time(twenty_four_hr) == f"1999-12-31 {expected_time} IST"

    def test_set_flight_info_sets_all_the_correct_info(self) -> None:
        flight = Flight(TEST_FLIGHT_INFO, {}, "test_num")

        assert flight.departure_airport == "Ahmedabad"
        assert flight.destination_airport == "Ibiza"
        assert flight.departure_time == datetime(1999, 12, 31, 18, 29, tzinfo=timezone.utc)
        assert flight.flight_number == "1000"

    def test_get_airport_info_returns_all_airport_info(self, mocker: MockerFixture) -> None:
        expected_airport_info = {"test_code": {"timezone": "Asia/Kolkata", "name": "Test Airport"}}
        mocker.patch.object(Path, "read_text", return_value=json.dumps(expected_airport_info))
        airport_info = self.flight._get_airport_info()
        assert airport_info == expected_airport_info

    def test_convert_to_utc_converts_local_time_to_utc(self) -> None:
        tz_str = "Asia/Kolkata"
        tz = zoneinfo.ZoneInfo(tz_str)
        utc_flight_time = self.flight._convert_to_utc("1999-12-31T23:59:00.000+05:30", tz_str)

        assert utc_flight_time == datetime(1999, 12, 31, 18, 29, tzinfo=timezone.utc)
        assert self.flight._local_departure_time == datetime(1999, 12, 31, 23, 59, tzinfo=tz)
