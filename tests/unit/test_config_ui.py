import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from lib.config import ConfigError
from lib.config_ui import get_default_config, read_config, validate_config, write_config


def test_read_config_returns_default_when_file_is_missing(tmp_path: Path) -> None:
    config = read_config(tmp_path / "config.json")

    assert config == get_default_config()


def test_read_config_reads_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"fare_trackers": []}))

    assert read_config(config_path) == {"fare_trackers": []}


def test_read_config_raises_error_when_config_is_not_an_object(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("[]")

    with pytest.raises(ConfigError):
        read_config(config_path)


def test_write_config_validates_and_writes_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = {
        "fare_trackers": [
            {
                "originAirport": "PHX",
                "destinationAirport": "DEN",
                "departureDate": "2026-08-15",
            }
        ]
    }

    write_config(config_path, config)

    assert json.loads(config_path.read_text()) == config


def test_validate_config_raises_error_for_invalid_config() -> None:
    with pytest.raises(ConfigError):
        validate_config({"fare_trackers": [{"originAirport": "PHX"}]})


def test_get_config_path_uses_global_config(mocker: MockerFixture) -> None:
    mock_config = mocker.patch("lib.config_ui.GlobalConfig").return_value
    mock_config._get_config_file_path.return_value = Path("/tmp/config.json")

    from lib.config_ui import get_config_path

    assert get_config_path() == Path("/tmp/config.json")
