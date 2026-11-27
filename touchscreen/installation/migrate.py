#!/usr/bin/env python3
"""Carry display preferences into the native UI; never alter printer calibration."""

import argparse
import configparser
import json
import re
from pathlib import Path


def migrate_timezone(config, legacy):
    """Repair upgrades that previously omitted HarkScreen's auto preference."""
    target = Path(config).with_name("native-screen-settings.json")
    try:
        old = json.loads(Path(legacy).with_name("harkscreen-settings.json").read_text())
        prefs = json.loads(target.read_text()) if target.exists() else {}
    except (OSError, ValueError):
        return
    if not isinstance(old, dict) or not isinstance(prefs, dict):
        return
    if "timezone" in old and "timezone" not in prefs:
        prefs["timezone"] = old["timezone"]
        temp = target.with_suffix(".json.new")
        temp.write_text(json.dumps(prefs, indent=2) + "\n")
        temp.chmod(0o600)
        temp.replace(target)


def migrate(config, example, legacy, model=""):
    config = Path(config)
    if config.exists():
        # Image selection is now automatic; keep all other settings and comments.
        previous = config.read_text()
        cleaned = re.sub(
            r"(?m)^[ \t]*status_image(?:_without_cfs)?[ \t]*[:=][^\n]*(?:\n|$)",
            "",
            previous,
        )
        if cleaned != previous:
            config.write_text(cleaned)
        migrate_timezone(config, legacy)
        return
    native = configparser.ConfigParser()
    native.read(example)
    previous = configparser.ConfigParser()
    previous.read(legacy)
    if model:
        native.set("brand", "model", model)
    for section, keys in {
        "brand": ("name", "model"),
        "screen": (
            "moonraker",
            "rotate",
            "touch_device",
            "touch_max_x",
            "touch_max_y",
            "touch_swap_xy",
            "touch_invert_x",
            "touch_invert_y",
            "sleep_after",
            "cutter_calibration_method",
        ),
    }.items():
        for key in keys:
            if previous.has_option(section, key):
                native.set(section, key, previous.get(section, key))
    with config.open("w") as stream:
        native.write(stream)
    prefs = {"accent": "#3d8bfd"}
    try:
        old = json.loads(Path(legacy).with_name("harkscreen-settings.json").read_text())
    except (OSError, ValueError):
        old = {}
    for key in (
        "timezone",
        "brightness",
        "sleep_after",
        "idle",
        "light_off",
        "gcode_storage",
        "usb_ejected",
        "cfs_startup_read",
        "cfs_insertion_read",
        "cfs_auto_refill",
    ):
        if key in old:
            prefs[key] = old[key]
    target = config.with_name("native-screen-settings.json")
    if prefs and not target.exists():
        target.write_text(json.dumps(prefs, indent=2) + "\n")
        target.chmod(0o600)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--example", required=True)
    p.add_argument("--legacy", required=True)
    p.add_argument("--model", default="")
    a = p.parse_args()
    migrate(a.config, a.example, a.legacy, a.model)


if __name__ == "__main__":
    main()
