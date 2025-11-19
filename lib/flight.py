from __future__ import annotations

import json
import os
import zoneinfo
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JSON = dict[str, Any]

# This file contains the names and timezones of all airports used by Southwest Airlines
# mapped by their IATA code.
AIRPORT_INFO_PATH = "utils/airport_info.json"


class Flight:
    """
    A helper class that parses flight information received from the Southwest API.

    The flight time is automatically translated from the flight's local timezone to UTC.
    """

    def __init__(self, flight_info: JSON, reservation_info: JSON, confirmation_number: str) -> None:
        self.confirmation_number = confirmation_number
        self.reservation_info = reservation_info
        self.is_same_day = False

        self.departure_airport = None
        self.destination_airport = None
        self._local_departure_time = None
        self.departure_time = None
        self.flight_number = None

        # Track to notify the user of filling out their passport information.
        # Southwest only fills the country's value for international flights
        self.is_international = flight_info["international"]

        # TODO: When would there be more than one segment?
        flight_seg = flight_info["segments"][0]
        self._set_flight_info(flight_seg)

    def __eq__(self, other: object) -> bool:
        # Define how two flights are equal to each other
        return (
            isinstance(other, Flight)
            and self.flight_number == other.flight_number
            and self.departure_time == other.departure_time
        )

    @property
    def can_be_reaccommodated(self) -> bool:
        """
        Returns whether or not the flight can be changed for free (Southwest uses 'reaccommodated').
        """
        return self.reservation_info["permissions"]["can_reaccom"]

    def get_display_time(self, twenty_four_hr_time: bool) -> str:
        if twenty_four_hr_time:
            time_format = "%H:%M"
        else:
            # The '#' removes leading zeros in Windows and '-' in Linux/Mac
            time_format = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"

        date_format = f"%Y-%m-%d {time_format} %Z"
        return datetime.strftime(self._local_departure_time, date_format)

    def _set_flight_info(self, flight: JSON) -> None:
        airport_info = self._get_airport_info()
        departure_airport_code = flight["origination_airport_code"]
        destination_airport_code = flight["destination_airport_code"]

        # Set the names of the airports
        dep_airport_info = airport_info[departure_airport_code]
        dest_airport_info = airport_info[destination_airport_code]
        self.departure_airport = dep_airport_info["name"]
        self.destination_airport = dest_airport_info["name"]

        # Set the departure time
        self.departure_time = self._convert_to_utc(
            flight["depart_at"], dep_airport_info["timezone"]
        )

        # Set miscellaneous flight information
        self.flight_number = flight["flight_number"]

    def _get_airport_info(self) -> Any:
        project_dir = Path(__file__).parents[1]
        tz_file = project_dir / AIRPORT_INFO_PATH
        return json.loads(tz_file.read_text())

    def _convert_to_utc(self, flight_date: str, airport_timezone: str) -> datetime:
        flight_date = datetime.fromisoformat(flight_date)
        airport_tz = zoneinfo.ZoneInfo(airport_timezone)
        # Save the local departure time to display to the user later
        self._local_departure_time = flight_date.replace(tzinfo=airport_tz)

        return self._local_departure_time.astimezone(timezone.utc)
