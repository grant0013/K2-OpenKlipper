#!/usr/bin/env python3
"""Replace the native UI with a verified upstream HarkScreen installation."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

SCREEN = Path("/mnt/UDISK/openklipper-screen")
BUILD = Path("/mnt/UDISK/k2-mainline-20260729-v62")
CONF = Path("/mnt/UDISK/printer_data_mainline/config")
BACKUPS = Path("/mnt/UDISK/openklipper-backups")
HARK = Path("/mnt/UDISK/harkscreen")
PIDFILES = [
    Path("/tmp/" + name + ".pid")
    for name in ("openklipper-screen", "openklipper-screen-service")
]


def resolve_ref(ref=None):
    """Match the upstream installer's newest public release, including prereleases."""
    if ref:
        return ref
    request = Request(
        "https://api.github.com/repos/grant0013/K2-OpenKlipper/releases?per_page=10",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "k2-openklipper-touchscreen",
        },
    )
    with urlopen(request, timeout=30) as response:
        releases = json.load(response)
    if not isinstance(releases, list):
        raise TypeError("Could not resolve the latest upstream release.")
    for release in releases:
        if not release.get("draft") and release.get("tag_name"):
            return release["tag_name"]
    return "main"


def ensure_idle():
    url = "http://127.0.0.1:7126/printer/objects/query?print_stats&virtual_sdcard&idle_timeout"
    with urlopen(url, timeout=10) as response:
        status = json.load(response)["result"]["status"]
    state = status.get("print_stats", {}).get("state")
    if state not in ("standby", "complete", "cancelled", "error"):
        raise RuntimeError("Finish or stop the print before returning to HarkScreen.")
    if (
        status.get("virtual_sdcard", {}).get("is_active")
        or status.get("idle_timeout", {}).get("state") == "Printing"
    ):
        raise RuntimeError("Wait for printer operations to finish before reinstalling.")


def download_installer(ref, directory):
    base = (
        "https://raw.githubusercontent.com/grant0013/K2-OpenKlipper/"
        + quote(ref, safe="/")
        + "/"
    )
    contents = {}
    for name in ("install.sh", "harkscreen/start.sh", "switch-to-mainline.sh"):
        with urlopen(base + name, timeout=30) as response:
            contents[name] = response.read().decode("utf-8")
    startup = contents["switch-to-mainline.sh"]
    if (
        "/mnt/UDISK/harkscreen/start.sh" not in startup
        or "openklipper-screen" in startup
    ):
        raise RuntimeError(
            "The selected upstream version does not restore HarkScreen startup."
        )
    if not contents["harkscreen/start.sh"].startswith("#!/bin/sh"):
        raise RuntimeError("The selected upstream HarkScreen launcher is invalid.")
    installer = Path(directory) / "upstream-install.sh"
    installer.write_text(contents["install.sh"])
    return installer


def belongs_to(pid, root):
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        prefix = os.fsencode(str(root) + "/")
        return any(arg.startswith(prefix) for arg in args)
    except OSError:
        return False


def screen_pids():
    pids = set()
    for path in PIDFILES:
        try:
            pid = int(path.read_text().strip())
            if belongs_to(pid, SCREEN):
                pids.add(pid)
        except (OSError, ValueError):
            pass
    return pids


def stop_native():
    for pid in screen_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    while screen_pids() and time.monotonic() < deadline:
        time.sleep(0.1)
    for pid in screen_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def hark_running():
    try:
        pid = int(Path("/tmp/harkscreen.pid").read_text().strip())
        return belongs_to(pid, HARK)
    except (OSError, ValueError):
        return False


def verify_hark():
    startup = (BUILD / "switch-to-mainline.sh").read_text()
    if (
        "/mnt/UDISK/harkscreen/start.sh" not in startup
        or "openklipper-screen" in startup
    ):
        raise RuntimeError(
            "The installed startup script still does not select HarkScreen."
        )
    if not (HARK / "start.sh").is_file():
        raise RuntimeError("The upstream HarkScreen runtime is missing.")
    if not hark_running():
        subprocess.run(["sh", str(HARK / "start.sh")], check=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if hark_running():
            time.sleep(1)
            if hark_running():
                return
        time.sleep(0.2)
    raise RuntimeError(
        "HarkScreen did not stay running; native files have been retained."
    )


def remove_native():
    # Called only after the replacement runtime and its boot path are verified.
    stop_native()
    if SCREEN.exists():
        shutil.rmtree(SCREEN)
    for path in PIDFILES + [
        Path("/tmp/openklipper-screen.sock"),
        CONF / "native-screen.cfg",
        CONF / "native-screen-settings.json",
    ]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def saved_hark_settings():
    """Capture settings before the reinstall creates another backup/defaults."""
    saved = {}
    backups = sorted((p for p in BACKUPS.glob("*") if p.is_dir()),
                     key=lambda p: p.name, reverse=True)
    for name in ("harkscreen.cfg", "harkscreen-settings.json"):
        candidates = [CONF / name]
        for backup in backups:
            candidates.extend((backup / "printer_data_mainline-config" / name,
                               backup / (name + ".previous")))
        for source in candidates:
            if source.is_file():
                data = source.read_bytes()
                if name.endswith(".json") and not isinstance(json.loads(data), dict):
                    raise ValueError("Invalid HarkScreen settings: " + str(source))
                saved[name] = data
                break
    return saved


def restore_hark_settings(saved):
    # Restore display preferences only. The shared system timezone stays as set
    # by the user; HarkScreen's saved auto mode continues to follow it.
    for name, data in saved.items():
        target = CONF / name
        temp = target.with_name(name + ".restore-new")
        temp.write_bytes(data)
        temp.chmod(0o600)
        temp.replace(target)


def restore(ref=None, check=False):
    if os.geteuid() != 0 or not SCREEN.is_dir():
        raise RuntimeError(
            "Run this on the printer as root while the native screen is installed."
        )
    ensure_idle()
    ref = resolve_ref(ref)
    print("Selected upstream version: " + ref)
    with tempfile.TemporaryDirectory(prefix="okl-harkscreen-") as directory:
        installer = download_installer(ref, directory)
        env = dict(os.environ, OKL_REF=ref)
        env.setdefault("OKL_BOOTANIM", "keep")
        # The upstream preflight runs before either native process is stopped.
        subprocess.run(["sh", str(installer), "--check"], env=env, check=True)
        saved = saved_hark_settings()
        if check:
            print(
                "HarkScreen rollback check passed for "
                + ref
                + ". Nothing was uninstalled."
            )
            return
        ensure_idle()
        stop_native()
        try:
            # Full reinstall: --update can exit early when versions match.
            subprocess.run(["sh", str(installer)], env=env, check=True)
            restore_hark_settings(saved)
            if saved:
                subprocess.run(["sh", str(HARK / "start.sh")], check=True)
            verify_hark()
        except Exception:
            if not hark_running() and (SCREEN / "start.sh").is_file():
                subprocess.run(
                    ["sh", str(SCREEN / "start.sh"), str(CONF / "native-screen.cfg")],
                    check=False,
                )
            raise
        remove_native()
        print("HarkScreen restored. Native touchscreen runtime and settings removed.")
        print(
            "Printer configuration, G-code files and installer backups were retained."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify prerequisites without stopping or removing the screen",
    )
    parser.add_argument(
        "--ref",
        default=os.environ.get("OKL_HARK_REF"),
        help="Override the latest upstream release with a tag or branch containing HarkScreen",
    )
    args = parser.parse_args()
    try:
        restore(args.ref, args.check)
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"HarkScreen restore failed: {error}\n")


if __name__ == "__main__":
    main()
