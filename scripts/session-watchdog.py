#!/usr/bin/env python3
"""
session-watchdog.py — keep the Philips Avent Tuya session alive and alert (once)
when it dies or the camera drops offline. No password on disk (uses the macOS
Keychain); no email/MFA automation.

Why it works this way: the Tuya Mobile SDK has no refresh token, and a fresh
login with the persisted device_id STILL requires the emailed MFA code on this
whitelabel (run `--probe` to confirm on your unit). So silent auto-recovery is
impossible; the best a home user can do is keep the SID alive with periodic
activity and get a proactive nudge when a human re-login is actually needed.

Modes:
  --probe   Test whether a fresh login with your persisted device_id needs MFA.
            Prints NO_MFA / MFA_REQUIRED / an error. May send one MFA email.
  (default) Keepalive (smartlife.m.user.info.get). If the SID is dead or the
            camera is offline, post a one-time macOS notification.

Config: EMAIL/COUNTRY from watchdog.env; password from Keychain service
(default 'aventproxy-tuya'); session from philips_avent_bridge.json.
Run it from cron/launchd/systemd every few hours.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVENTPROXY_DIR = os.environ.get("AVENTPROXY_DIR", os.path.join(ROOT, "vendor", "aventproxy"))
CONFIG = os.environ.get("AVENT_CONFIG", os.path.join(ROOT, "philips_avent_bridge.json"))
ENV_FILE = os.path.join(ROOT, "watchdog.env")
KEYCHAIN_SERVICE = os.environ.get("AVENT_KEYCHAIN_SERVICE", "aventproxy-tuya")
BRIDGE_LABEL = os.environ.get("AVENT_BRIDGE_LABEL", "")  # for the rare NO_MFA auto-refresh


def load_upstream():
    pkg = os.path.join(AVENTPROXY_DIR, "custom_components", "philips_avent")
    if not os.path.isdir(pkg):
        sys.exit(f"aventproxy not found at {pkg} (run setup.sh).")
    sys.path.append(pkg)  # append: don't shadow stdlib (select.py etc.)
    import const, region, payload  # noqa
    from api import PhilipsAventAPI, TuyaAPIError  # noqa
    return const, region, payload, PhilipsAventAPI, TuyaAPIError


def load_env() -> dict:
    env = {}
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def keychain_password() -> str:
    out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit(f"No password in Keychain (service '{KEYCHAIN_SERVICE}'). Store it with:\n"
                 f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a you@example.com -w")
    return out.stdout.rstrip("\n")


def notify(title: str, msg: str) -> None:
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                   check=False)


def notify_once(marker: str, title: str, msg: str) -> None:
    path = os.path.join(ROOT, marker)
    if os.path.exists(path):
        return
    notify(title, msg)
    open(path, "w").close()


def clear_marker(marker: str) -> None:
    try:
        os.remove(os.path.join(ROOT, marker))
    except FileNotFoundError:
        pass


async def run(probe: bool) -> int:
    const, region, payload, PhilipsAventAPI, TuyaAPIError = load_upstream()
    import aiohttp
    env = load_env()
    email = env.get("EMAIL") or os.environ.get("AVENT_EMAIL")
    country = (env.get("COUNTRY") or "").upper()
    if not email:
        sys.exit("Set EMAIL in watchdog.env")
    cfg = json.load(open(CONFIG))
    device_id = cfg["device_id"]
    api_host = cfg.get("api_host") or region.api_host("eu")
    routed = region.COUNTRY_ROUTING.get(country, ("1", "us"))
    calling_code = routed[0]

    async with aiohttp.ClientSession() as session:
        if probe:
            password = keychain_password()
            api = PhilipsAventAPI(session, api_url=region.api_url_for_host(api_host),
                                  country_code=calling_code, device_id=device_id)
            tok = await api.get_rsa_token(email, calling_code)
            enc = PhilipsAventAPI.encrypt_password(password, tok["pbKey"])
            try:
                await api.login_password(email, enc, tok["token"], mfa_code="")
                print("NO_MFA (device_id is a trusted terminal -> watchdog can auto-recover)")
                return 0
            except TuyaAPIError as e:
                if e.code == "MFA_NEED_SEND_CODE":
                    print("MFA_REQUIRED (a human re-login is needed on expiry)")
                    return 2
                print(f"REAUTH_ERROR {e.code}")
                return 3

        api = PhilipsAventAPI(session, sid=cfg["sid"],
                              api_url=region.api_url_for_host(api_host), device_id=device_id)
        try:
            await api.get_user_info()   # keepalive + validity check
        except TuyaAPIError as e:
            print(f"SID_DEAD ({e.code})")
            notify_once(".wd-session-notified", "Avent camera",
                        "Session expired. Run scripts/avent-login.py to renew it.")
            return 2
        clear_marker(".wd-session-notified")
        print("SID_ALIVE")

        offline = False
        for cam in cfg.get("cameras", []):
            try:
                dev = await api.get_device(cam["camera_id"])
                if not dev.get("isOnline", True):
                    offline = True
            except TuyaAPIError:
                offline = True
        if offline:
            print("CAMERA_OFFLINE")
            notify_once(".wd-camera-notified", "Avent camera", "The camera is offline. Check power/Wi-Fi.")
            return 4
        clear_marker(".wd-camera-notified")
        print("CAMERA_ONLINE")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run("--probe" in sys.argv)))
