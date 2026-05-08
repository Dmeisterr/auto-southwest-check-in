from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import ConfigError, GlobalConfig

JSON = dict[str, Any]

DEFAULT_CONFIG_UI_PORT = 8765


def get_config_path() -> Path:
    config = GlobalConfig()
    return config._get_config_file_path()


def get_default_config() -> JSON:
    return {
        "$schema": "config.schema.json",
        "notifications": [],
        "retrieval_interval": 24,
        "accounts": [],
        "reservations": [],
        "fare_trackers": [],
    }


def read_config(config_path: Path) -> JSON:
    try:
        config = json.loads(config_path.read_text())
    except FileNotFoundError:
        return get_default_config()

    if not isinstance(config, dict):
        raise ConfigError("Configuration must be a JSON dictionary")

    return config


def write_config(config_path: Path, config: JSON) -> None:
    validate_config(config)
    config_path.write_text(json.dumps(config, indent=4, sort_keys=False) + "\n")


def validate_config(config: JSON) -> None:
    if not isinstance(config, dict):
        raise ConfigError("Configuration must be a JSON dictionary")

    parsed_config = GlobalConfig()
    parsed_config._parse_config(config)


class ConfigUIHandler(BaseHTTPRequestHandler):
    config_path = Path()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(CONFIG_UI_HTML)
            return

        if path == "/api/config":
            try:
                config = read_config(self.config_path)
                self._send_json({"path": str(self.config_path), "config": config})
            except (ConfigError, json.decoder.JSONDecodeError) as err:
                self._send_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/config":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
            config = payload["config"]
            write_config(self.config_path, config)
            self._send_json({"path": str(self.config_path), "config": config})
        except (KeyError, TypeError, ConfigError, json.decoder.JSONDecodeError) as err:
            self._send_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, message_format: str, *args: Any) -> None:
        del message_format, args
        return

    def _read_json_body(self) -> JSON:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        payload = json.loads(body.decode())
        if not isinstance(payload, dict):
            raise ConfigError("Request body must be a JSON dictionary")

        return payload

    def _send_json(self, data: JSON, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_config_ui(port: int = DEFAULT_CONFIG_UI_PORT) -> None:
    config_path = get_config_path()
    ConfigUIHandler.config_path = config_path
    server = ThreadingHTTPServer(("127.0.0.1", port), ConfigUIHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Config UI running at {url}")
    print(f"Editing config file: {config_path}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping config UI")
    finally:
        server.server_close()


CONFIG_UI_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto Southwest Config</title>
<style>
:root {
    color-scheme: light;
    --bg: #f7f8fb;
    --panel: #ffffff;
    --text: #1d2733;
    --muted: #657282;
    --line: #d7dde6;
    --accent: #007f7a;
    --accent-strong: #00645f;
    --danger: #b42318;
    --warning: #a15c00;
}
* {
    box-sizing: border-box;
}
body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
        sans-serif;
    font-size: 14px;
}
header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 24px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
}
h1 {
    margin: 0;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0;
}
main {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(320px, 440px);
    gap: 20px;
    max-width: 1280px;
    margin: 0 auto;
    padding: 20px;
}
section {
    min-width: 0;
}
.path {
    color: var(--muted);
    font-size: 12px;
    overflow-wrap: anywhere;
}
.toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
}
.actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
button {
    min-height: 36px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel);
    color: var(--text);
    cursor: pointer;
    font: inherit;
    padding: 0 12px;
}
button.primary {
    border-color: var(--accent);
    background: var(--accent);
    color: #fff;
}
button.primary:hover {
    background: var(--accent-strong);
}
button.danger {
    border-color: #f1c4bf;
    color: var(--danger);
}
.tracker-list {
    display: grid;
    gap: 10px;
}
.tracker {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel);
    padding: 14px;
}
.tracker-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
}
.tracker-title {
    font-weight: 700;
}
.grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
}
label {
    display: grid;
    gap: 5px;
    color: var(--muted);
    font-size: 12px;
    font-weight: 650;
}
input,
textarea {
    width: 100%;
    min-height: 38px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: #fff;
    color: var(--text);
    font: inherit;
    padding: 8px 10px;
}
input:focus,
textarea:focus {
    outline: 2px solid rgba(0, 127, 122, 0.2);
    border-color: var(--accent);
}
textarea {
    min-height: 520px;
    resize: vertical;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    line-height: 1.45;
}
.json-panel {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel);
    padding: 14px;
}
.status {
    min-height: 20px;
    color: var(--muted);
    font-size: 13px;
}
.status.error {
    color: var(--danger);
}
.status.warn {
    color: var(--warning);
}
@media (max-width: 900px) {
    main {
        grid-template-columns: 1fr;
        padding: 14px;
    }
    header {
        align-items: flex-start;
        flex-direction: column;
    }
    .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}
@media (max-width: 560px) {
    .grid {
        grid-template-columns: 1fr;
    }
    .toolbar {
        align-items: flex-start;
        flex-direction: column;
    }
}
</style>
</head>
<body>
<header>
    <div>
        <h1>Auto Southwest Config</h1>
        <div id="configPath" class="path"></div>
    </div>
    <div class="actions">
        <button id="reloadBtn" type="button">Reload</button>
        <button id="saveBtn" class="primary" type="button">Save</button>
    </div>
</header>
<main>
    <section>
        <div class="toolbar">
            <h2>Fare Trackers</h2>
            <button id="addTrackerBtn" type="button">Add Tracker</button>
        </div>
        <div id="trackerList" class="tracker-list"></div>
    </section>
    <section class="json-panel">
        <div class="toolbar">
            <h2>JSON</h2>
            <button id="applyJsonBtn" type="button">Apply JSON</button>
        </div>
        <textarea id="jsonEditor" spellcheck="false"></textarea>
        <div id="status" class="status"></div>
    </section>
</main>
<script>
let config = {};
let configPath = "";

const trackerList = document.querySelector("#trackerList");
const jsonEditor = document.querySelector("#jsonEditor");
const statusEl = document.querySelector("#status");
const configPathEl = document.querySelector("#configPath");

function setStatus(message, kind = "") {
    statusEl.textContent = message;
    statusEl.className = `status ${kind}`;
}

function ensureShape() {
    config.notifications ??= [];
    config.accounts ??= [];
    config.reservations ??= [];
    config.fare_trackers ??= [];
}

function trackerTitle(tracker, index) {
    const route = `${tracker.originAirport || "Origin"} to ${tracker.destinationAirport || "Dest"}`;
    const flight = tracker.flightNumber ? `, flight ${tracker.flightNumber}` : "";
    return `#${index + 1} ${route}${flight}`;
}

function renderTrackers() {
    ensureShape();
    trackerList.replaceChildren();
    config.fare_trackers.forEach((tracker, index) => {
        const item = document.createElement("div");
        item.className = "tracker";
        item.innerHTML = `
            <div class="tracker-head">
                <div class="tracker-title"></div>
                <button class="danger" type="button">Remove</button>
            </div>
            <div class="grid">
                <label>Origin
                    <input data-key="originAirport" maxlength="3" value="">
                </label>
                <label>Destination
                    <input data-key="destinationAirport" maxlength="3" value="">
                </label>
                <label>Date
                    <input data-key="departureDate" type="date" value="">
                </label>
                <label>Flight
                    <input data-key="flightNumber" value="">
                </label>
            </div>
            <label style="margin-top: 10px;">Healthchecks URL
                <input data-key="healthchecks_url" value="">
            </label>
        `;

        item.querySelector(".tracker-title").textContent = trackerTitle(tracker, index);
        item.querySelector(".danger").addEventListener("click", () => {
            config.fare_trackers.splice(index, 1);
            render();
            setStatus("Tracker removed.", "warn");
        });

        item.querySelectorAll("input").forEach((input) => {
            const key = input.dataset.key;
            input.value = tracker[key] || "";
            input.addEventListener("input", () => {
                const value = input.value.trim();
                if (value) {
                    tracker[key] = key.includes("Airport") ? value.toUpperCase() : value;
                } else {
                    delete tracker[key];
                }
                renderJson();
                item.querySelector(".tracker-title").textContent = trackerTitle(tracker, index);
            });
        });

        trackerList.append(item);
    });
}

function renderJson() {
    jsonEditor.value = JSON.stringify(config, null, 4);
}

function render() {
    ensureShape();
    renderTrackers();
    renderJson();
}

async function loadConfig() {
    const response = await fetch("/api/config");
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || "Unable to load config");
    }
    config = data.config;
    configPath = data.path;
    configPathEl.textContent = configPath;
    render();
    setStatus("Loaded.");
}

async function saveConfig() {
    const response = await fetch("/api/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({config}),
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || "Unable to save config");
    }
    config = data.config;
    render();
    setStatus("Saved.");
}

document.querySelector("#addTrackerBtn").addEventListener("click", () => {
    ensureShape();
    config.fare_trackers.push({
        originAirport: "",
        destinationAirport: "",
        departureDate: "",
    });
    render();
    setStatus("Tracker added.");
});

document.querySelector("#applyJsonBtn").addEventListener("click", () => {
    try {
        const parsed = JSON.parse(jsonEditor.value);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
            throw new Error("JSON must be an object.");
        }
        config = parsed;
        render();
        setStatus("JSON applied.");
    } catch (error) {
        setStatus(error.message, "error");
    }
});

document.querySelector("#reloadBtn").addEventListener("click", () => {
    loadConfig().catch((error) => setStatus(error.message, "error"));
});

document.querySelector("#saveBtn").addEventListener("click", () => {
    saveConfig().catch((error) => setStatus(error.message, "error"));
});

loadConfig().catch((error) => setStatus(error.message, "error"));
</script>
</body>
</html>
"""
