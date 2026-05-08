# Configuration
This guide contains all the information you need to configure Auto-Southwest Check-In to your needs. A default/example configuration
file can be found at [config.example.json](config.example.json)

Auto-Southwest Check-In supports both global configuration and account/reservation-specific configuration. See
[Accounts and Reservations](#accounts-and-reservations) for more information.

**Note**: Many configuration items may also be configured via environment variables (except for account and
reservation-specific configurations).

## Table of Contents
- [Check Fares](#check-fares)
- [Notifications](#notifications)
    * [Test the Notifications](#test-the-notifications)
- [Browser Path](#browser-path)
- [Retrieval Interval](#retrieval-interval)
- [Config UI](#config-ui)
- [Raspberry Pi systemd Fare Tracking](#raspberry-pi-systemd-fare-tracking)
- [macOS launchd Fare Tracking](#macos-launchd-fare-tracking)
- [Accounts and Reservations](#accounts-and-reservations)
    * [Accounts](#accounts)
    * [Reservations](#reservations)
- [Standalone Fare Trackers](#standalone-fare-trackers)
- [Healthchecks URL](#healthchecks-url)

## Check Fares
Default: true \
Type: Boolean or String \
Environment Variable: `AUTO_SOUTHWEST_CHECK_IN_CHECK_FARES`
> Using the environment variable will override the applicable setting in `config.json`.

In addition to automatically checking in, flights can be automatically checked for price drops on an interval
(see [Retrieval Interval](#retrieval-interval)). If a lower fare is found, the user will be notified.

**Note**: Companion passes are not supported for fare checking.
```json
{
    "check_fares": "<value>"
}
```

### Check Fares Values
- `false` or `"no"`: Do not check for lower fares
- `true` or `"same_flight"`: Check for lower fares on the same flight
- `"same_day_nonstop"`: Check for lower fares on all nonstop flights on the same day as the flight
- `"same_day"`: Check for lower fares on all flights on the same day as the flight

## Notifications
Default: [] \
Type: List \
Environment Variables:
- `AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_URL`
- `AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_LEVEL`
- `AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_24_HOUR_TIME`
> When using the environment variable, you may only specify a single URL. If a level or 24-hour time
> is specified, but no URL is specified, it will have no effect.
> If you are also using `config.json`, it will add the notification service as long as the URL is not a duplicate.

Users can be notified on successful and failed check-ins, flight scheduling, and fare drops. This is done through
the [Apprise library]. Information on how to create notification URLs can be found on the [Apprise Readme]. You can
optionally include a [notification level](#notification-level) and [24-hour time](#notification-24-hour-time) setting
for each notification service you use.
```json
{
  "notifications": [
    {"url": "service://my_first_service_url", "level": 3, "24_hour_time": true},
    {"url": "service://my_second_service_url"}
  ]
}
```

For Gmail, use a Google app password instead of your normal Google password. The easiest local setup is to put
the Apprise email URL in an environment file instead of committing it to `config.json`:
```shell
AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_URL="mailtos://gmail.com?user=your.email@gmail.com&pass=your-app-password"
```

If you want to send to a different recipient, append `&to=recipient@example.com`. If your app password contains
spaces, remove the spaces.

### Notification Level
Default: 2 \
Type: Integer

The following levels are available: \
`Level 1`: Receive notices of skipped reservation retrievals due to driver timeouts and Too Many Requests errors
during logins as well as all messages in later levels.\
`Level 2`: Receive successful scheduling messages, lower fare messages, and all messages in later levels.\
`Level 3`: Receive successful check-in messages, and all messages in later levels.\
`Level 4`: Receive only error messages (failed scheduling and check-ins).

### Notification 24 Hour Time
Default: false \
Type: Boolean

Display flight times in notifications in 24-hour format instead of 12-hour format. Console messages
will always display in 12-hour format.

### Test the Notifications
To test if the notification URLs work, you can run the following command
```shell
$ python3 southwest.py --test-notifications
```

## Browser Path
Default: The path to your Chrome or Chromium browser (if installed) \
Type: String \
Environment Variable: `AUTO_SOUTHWEST_CHECK_IN_BROWSER_PATH`
> Using the environment variable will override the applicable setting in `config.json`.

If you use another Chromium-based browser besides Google Chrome or Chromium (such as Brave), you need to specify the path to
the browser executable.

**Note**: Microsoft Edge is not supported
```json
{
    "browser_path": "/usr/bin/browser_path"
}
```

## Retrieval Interval
Default: 24 hours \
Type: Integer \
Environment Variable: `AUTO_SOUTHWEST_CHECK_IN_RETRIEVAL_INTERVAL`
> Using the environment variable will override the applicable setting in `config.json`.

You can choose how often the script checks for lower fares on scheduled flights (in hours). Additionally, this
interval will also determine how often the script checks for new flights if login credentials are provided. To
disable account/fare monitoring, set this option to `0` (The account/fares will only be checked once).
```json
{
    "retrieval_interval": 24
}
```

## Config UI
You can edit `config.json` from a local browser UI:
```shell
$ python3 southwest.py --config-ui
```

The default URL is `http://127.0.0.1:8765`. To use a different port:
```shell
$ python3 southwest.py --config-ui --config-ui-port 9090
```

## Raspberry Pi systemd Fare Tracking
For standalone fare tracking on a Raspberry Pi, use the included `systemd` timer. It runs the trackers once every
8 hours, then exits. State is kept in `logs/fare-tracker-state.json`, so the next run can compare against the
previous observed cash and points prices.

Install the project under your Pi user's home directory. The unit templates use `~/auto-southwest-check-in`;
if you clone somewhere else, edit the `WorkingDirectory` and `ExecStart` paths in the service file before installing it.
```shell
$ sudo apt update
$ sudo apt install -y python3-venv chromium-browser
$ git clone https://github.com/jdholtz/auto-southwest-check-in.git ~/auto-southwest-check-in
$ cd ~/auto-southwest-check-in
$ python3 -m venv venv
$ venv/bin/pip install -r requirements.txt
```

On some Raspberry Pi OS releases, the browser package is named `chromium` instead of `chromium-browser`.

Create `config.json` with `retrieval_interval` set to `0`. The timer controls the 8-hour schedule, so the script
should not run its own loop:
```json
{
    "$schema": "config.schema.json",
    "retrieval_interval": 0,
    "notifications": [],
    "accounts": [],
    "reservations": [],
    "fare_trackers": [
        {
            "originAirport": "PHX",
            "destinationAirport": "BNA",
            "departureDate": "2026-07-02",
            "flightNumber": "4507"
        }
    ]
}
```

Create a local environment file for Gmail notifications:
```shell
$ mkdir -p ~/.config/auto-southwest-check-in
$ cp deploy/systemd/fare-trackers.env.example ~/.config/auto-southwest-check-in/fare-trackers.env
$ nano ~/.config/auto-southwest-check-in/fare-trackers.env
$ chmod 600 ~/.config/auto-southwest-check-in/fare-trackers.env
```

The env file should contain your Gmail notification URL:
```shell
AUTO_SOUTHWEST_CHECK_IN_NOTIFICATION_URL="mailtos://gmail.com?user=your.email@gmail.com&pass=your-app-password"
```

Test Gmail before enabling the timer:
```shell
$ cd ~/auto-southwest-check-in
$ set -a
$ . ~/.config/auto-southwest-check-in/fare-trackers.env
$ set +a
$ venv/bin/python southwest.py --test-notifications
```

Install and start the timer:
```shell
$ mkdir -p ~/.config/systemd/user
$ cp deploy/systemd/auto-southwest-fare-trackers.service ~/.config/systemd/user/
$ cp deploy/systemd/auto-southwest-fare-trackers.timer ~/.config/systemd/user/
$ systemctl --user daemon-reload
$ systemctl --user enable --now auto-southwest-fare-trackers.timer
$ sudo loginctl enable-linger "$USER"
```

Useful commands:
```shell
$ systemctl --user list-timers auto-southwest-fare-trackers.timer
$ systemctl --user start auto-southwest-fare-trackers.service
$ journalctl --user -u auto-southwest-fare-trackers.service -n 100 --no-pager
```

You can also run the same one-shot mode manually:
```shell
$ venv/bin/python southwest.py --fare-trackers-once --verbose
```

## macOS launchd Fare Tracking
For macOS, use the included `launchd` plist to run standalone fare trackers on a schedule. The included plist
runs this command every 8 hours:
```shell
$ venv/bin/python southwest.py --fare-trackers-once --verbose
```

The interval is controlled by `StartInterval` in
`deploy/launchd/com.auto-southwest.fare-trackers.plist`. The value is seconds, so use `hours * 3600`.
For example, 8 hours is `28800`.

Install and start it:
```shell
$ cp deploy/launchd/com.auto-southwest.fare-trackers.plist ~/Library/LaunchAgents/
$ launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.auto-southwest.fare-trackers.plist
$ launchctl kickstart -k "gui/$(id -u)/com.auto-southwest.fare-trackers"
```

Useful commands:
```shell
$ launchctl print "gui/$(id -u)/com.auto-southwest.fare-trackers"
$ launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.auto-southwest.fare-trackers.plist
$ tail -n 100 logs/launchd-fare-trackers.out.log
$ tail -n 100 logs/launchd-fare-trackers.err.log
```

## Accounts and Reservations
You can also add more [accounts](#accounts) and [reservations](#reservations) to the script through the configuration file.
Additionally, you can optionally specify [configuration options](#account-and-reservation-specific-configuration) for each
account and reservation.

### Accounts
Default: [] \
Type: List \
Environment Variables:
 - `AUTO_SOUTHWEST_CHECK_IN_USERNAME`
 - `AUTO_SOUTHWEST_CHECK_IN_PASSWORD`
> When using the environment variables, you may only specify a single set of credentials.

You can add more accounts to the script, allowing you to run multiple accounts at the same time and/or not
provide a username and password as arguments.
```json
{
    "accounts": [
        {"username": "user1", "password": "pass1"},
        {"username": "user2", "password": "pass2"}
    ]
}
```

### Reservations
Default: [] \
Type: List \
Environment Variables:
 - `AUTO_SOUTHWEST_CHECK_IN_CONFIRMATION_NUMBER`
 - `AUTO_SOUTHWEST_CHECK_IN_FIRST_NAME`
 - `AUTO_SOUTHWEST_CHECK_IN_LAST_NAME`
> When using the environment variables, you may only specify a single reservation.

You can also add more reservations to the script, allowing you check in to multiple reservations in the same instance
and/or not provide reservation information as arguments.
```json
{
    "reservations": [
        {"confirmationNumber": "num1", "firstName": "John", "lastName": "Doe"},
        {"confirmationNumber": "num2", "firstName": "Jane", "lastName": "Doe"}
    ]
}
```

## Standalone Fare Trackers
Default: [] \
Type: List

Standalone fare trackers monitor Southwest prices without requiring an existing reservation. Each tracker searches a one-way route/date,
checks both cash and points, and notifies you when either price drops below the last observed price. The first successful check stores
the current price and does not send a drop notification.

If `flightNumber` is provided, only matching itineraries are tracked. For a nonstop flight, use the single flight number.
For a connecting itinerary, either use one leg number like `"2230"` or the full sequence like `"2230/5678"`.
When a single leg number is used, any itinerary containing that leg can match; use the full sequence when you need the
exact connection. If `flightNumber` is omitted, the lowest available fare on the route/date is tracked. Round trips can
be tracked by adding one tracker for each direction.
```json
{
    "fare_trackers": [
        {
            "originAirport": "PHX",
            "destinationAirport": "DEN",
            "departureDate": "2026-08-15",
            "flightNumber": "1234"
        },
        {
            "originAirport": "DEN",
            "destinationAirport": "PHX",
            "departureDate": "2026-08-22"
        },
        {
            "originAirport": "KOA",
            "destinationAirport": "PHX",
            "departureDate": "2026-10-22",
            "flightNumber": "2230/5678"
        }
    ]
}
```

### Account and Reservation-specific configuration
Setting specific configuration values for an account, reservation, or standalone fare tracker allows you to fully customize how you want them to be
monitored by the script. Here is a list of configuration values that can be applied to an individual account or reservation:
- [Check Fares](#check-fares)
- [Notifications](#notifications)
- [Retrieval Interval](#retrieval-interval)
- [Healthchecks URL](#healthchecks-url)

Standalone fare trackers support [Notifications](#notifications), [Retrieval Interval](#retrieval-interval), and
[Healthchecks URL](#healthchecks-url).

Not all options have to be specified for each account or reservation. If an option is not specified, the top-level value is used
(or the default value if no top-level value is specified either) with exception to the Healthchecks URL. Any accounts or reservations
specified through the command line will use all of the top-level values.

An important note about notification services: An account or reservation with specific notification services will send notifications to those
services as well as services specified globally. If a service is in both the global and account/reservation configuration, the account/reservation
configuration will take precedence.

#### Examples
Here are a few examples of how the configuration options can be specified:

In this example, `user1`'s account will not check for lower flight fares. However, `user2`'s account will as the top-level value for
`check_fares` is `true`.
```json
{
    "check_fares": true,
    "accounts": [
        {"username": "user1", "password": "pass1", "check_fares": false},
        {"username": "user2", "password": "pass2"}
    ]
}
```

In this example, the script will send notifications attached to this reservation to both `top-level.url` and `my-special.url`.
```json
{
    "notifications": [{"url": "https://top-level.url"}],
    "reservations": [
        {
            "confirmationNumber": "num1",
            "firstName": "John",
            "lastName": "Doe",
            "notifications": [{"url": "https://my-special.url"}]
        }
    ]
}
```

## Healthchecks URL
Default: No URL \
Type: String

Monitor successful and failed fare checks using a [Healthchecks.io] URL. When a fare check
fails, the `/fail` endpoint of your Healthchecks URL will be pinged to notify you of the failure.

This configuration option can only be applied within reservation and account configurations (specifying it at the top-level
will have no effect). It can also be applied within standalone fare trackers. Due to this, no environment variable is
provided as a replacement for this configuration option.
```json
{
    "accounts": [
        {
            "username": "user1",
            "password": "pass1",
            "healthchecks_url": "https://hc-ping.com/uuid"
        }
    ]
}
```


[Apprise library]: https://github.com/caronc/apprise
[Apprise Readme]: https://github.com/caronc/apprise#supported-notifications
[Healthchecks.io]: https://healthchecks.io
