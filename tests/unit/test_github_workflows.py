from pathlib import Path


def test_fare_tracker_workflow_runs_on_schedule_and_saves_state() -> None:
    workflow = Path(".github/workflows/fare-trackers.yml").read_text()

    assert 'cron: "0 */8 * * *"' in workflow
    assert "AUTO_SOUTHWEST_CHECK_IN_CONFIG_JSON" in workflow
    assert (
        "python southwest.py --fare-trackers-once --fare-trackers-summary-file "
        "fare-drops.md --verbose"
    ) in workflow
    assert "gh issue create" in workflow
    assert "issues: write" in workflow
    assert "logs/fare-tracker-state.json" in workflow
    assert "actions/cache/restore@v4" in workflow
    assert "actions/cache/save@v4" in workflow
