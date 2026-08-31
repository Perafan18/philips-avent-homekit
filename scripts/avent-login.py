#!/usr/bin/env python3
"""
avent-login.py — Standalone Tuya login for Philips Avent baby monitors, WITHOUT
Home Assistant. Produces the `philips_avent_bridge.json` that the aventproxy Go
bridge consumes in `addon` mode, so the camera can be served as plain RTSP and
picked up by homebridge-camera-ffmpeg (or any RTSP client).

It reuses the tested login / data-center routing / camera-discovery logic from
the upstream aventproxy integration (github.com/thekoma/aventproxy) — that code
is NOT vendored here; point --aventproxy-dir at a checkout of it (setup.sh does
this for you).

Nothing is hardcoded: your email, password, country and the MFA code are all
supplied at runtime. The password is read hidden and never written to disk; the
output JSON holds only the resulting Tuya session (sid/ecode), not your password.

Usage:
    python3 avent-login.py \
        --aventproxy-dir ./vendor/aventproxy \
        --email you@example.com \
        [--country MX] [--bridge-port 38554] \
        [--output ./philips_avent_bridge.json]

You will be prompted for your password and, after Tuya emails it, the 6-digit
verification code.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys


def load_upstream(aventproxy_dir: str):
    pkg = os.path.join(aventproxy_dir, "custom_components", "philips_avent")
    if not os.path.isdir(pkg):
        sys.exit(
            f"Could not find the aventproxy integration at {pkg}\n"
            "Pass --aventproxy-dir pointing at a checkout of "
            "https://github.com/thekoma/aventproxy (or run setup.sh)."
        )
    # Append (not insert) so upstream modules named like stdlib ones
    # (select.py, number.py, ...) never shadow the real stdlib.
    sys.path.append(pkg)
    import const  # noqa
    import region as region_mod  # noqa
    import payload  # noqa
    from api import PhilipsAventAPI, TuyaAPIError, new_device_id  # noqa
    return const, region_mod, payload, PhilipsAventAPI, TuyaAPIError, new_device_id


def load_or_make_device_id(path: str, new_device_id) -> str:
    try:
        with open(path) as fh:
            v = fh.read().strip()
            if v:
                return v
    except FileNotFoundError:
        pass
    v = new_device_id()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(v)
    return v


async def run(args) -> int:
    import aiohttp
    const, region_mod, payload, PhilipsAventAPI, TuyaAPIError, new_device_id = \
        load_upstream(args.aventproxy_dir)

    email = args.email or input("Baby Monitor+ email: ").strip()
    country = (args.country or "").upper()  # empty => probe all data centers
    password = getpass.getpass("Baby Monitor+ password (hidden): ")

    device_id = load_or_make_device_id(args.device_id_file, new_device_id)
    candidates = region_mod.login_candidates(country or None)
    print(f"Trying data centers (best first): {[dc for dc, _ in candidates]}")

    async with aiohttp.ClientSession() as session:
        api = None
        calling_code = data_center = ""

        # Phase 1: find the account's data center (the one that asks for MFA).
        for dc, code in candidates:
            trial = PhilipsAventAPI(
                session, api_url=region_mod.api_url(dc),
                country_code=code, device_id=device_id,
            )
            try:
                tok = await trial.get_rsa_token(email, code)
                enc = PhilipsAventAPI.encrypt_password(password, tok["pbKey"])
                await trial.login_password(email, enc, tok["token"], mfa_code="")
                print(f"Logged in without MFA on '{dc}' (unusual).")
                api, calling_code, data_center = trial, code, dc
                break
            except TuyaAPIError as e:
                if e.code == "MFA_NEED_SEND_CODE":
                    print(f"Data center: '{dc}' (MFA required).")
                    api, calling_code, data_center = trial, code, dc
                    break
                if region_mod.is_wrong_data_center(e.code):
                    print(f"  '{dc}' is not it ({e.code}); trying next...")
                    continue
                print(f"Login failed on '{dc}': {e.code} — {e}")
                return 2
        if api is None:
            print("Could not find the account's data center.")
            return 2

        # Phase 2 + 3: trigger the emailed MFA code, then complete the login.
        result = {"sid": api.sid} if api.sid else None
        if not api.sid:
            tok = await api.get_rsa_token(email, calling_code)
            enc = PhilipsAventAPI.encrypt_password(password, tok["pbKey"])
            await api.trigger_mfa(email, enc, tok["token"])
            code = input("6-digit code emailed to you: ").strip()
            tok = await api.get_rsa_token(email, calling_code)
            enc = PhilipsAventAPI.encrypt_password(password, tok["pbKey"])
            result = await api.login_password(email, enc, tok["token"], mfa_code=code)
            api.sid = result["sid"]

        # Self-correct the API host from the account's own `domain` block.
        api_host = region_mod.api_host(data_center)
        hosts = region_mod.hosts_from_domain(result.get("domain"))
        if hosts.get("api_host"):
            api_host = hosts["api_host"]
            api.api_url = region_mod.api_url_for_host(api_host)

        print(f"Session OK. host={api_host} ecode={'yes' if result.get('ecode') else 'no'}")

        cameras = await api.discover_cameras()
        if not cameras:
            print("No cameras discovered. Is the monitor online in Baby Monitor+?")
        for cam in cameras:
            cid = cam.get("devId") or cam.get("deviceId") or cam.get("id", "?")
            name = cam.get("name") or cam.get("deviceName", "camera")
            path = const.sanitize_rtsp_path(name, cid)
            print(f"  camera: {name!r}  ->  rtsp://127.0.0.1:{args.bridge_port}/{path}")

        config = payload.build_bridge_config(
            signing_key=const.TUYA_SIGNING_KEY, sid=api.sid,
            ecode=result.get("ecode", ""), partner=result.get("partnerIdentity", ""),
            app_key=const.TUYA_APP_KEY, device_id=device_id,
            package_name=const.TUYA_PACKAGE_NAME, api_host=api_host,
            bridge_port=args.bridge_port, cameras=cameras, talkback=False,
        )
        payload.write_bridge_config_file(args.output, config)
        print(f"\nWrote {args.output} (mode 0600). Start the bridge with:")
        print(f"  ./bin/avent-webrtc-bridge addon --config {args.output}")
        return 0


def main():
    p = argparse.ArgumentParser(description="Standalone Tuya login for Philips Avent -> RTSP bridge JSON")
    p.add_argument("--aventproxy-dir", default=os.environ.get("AVENTPROXY_DIR", "./vendor/aventproxy"),
                   help="Path to a checkout of github.com/thekoma/aventproxy")
    p.add_argument("--email", default=os.environ.get("AVENT_EMAIL"))
    p.add_argument("--country", default=os.environ.get("AVENT_COUNTRY", ""),
                   help="ISO-2 country of the Baby Monitor+ account (optional; probes all if omitted)")
    p.add_argument("--bridge-port", type=int, default=int(os.environ.get("AVENT_BRIDGE_PORT", "38554")))
    p.add_argument("--output", default="./philips_avent_bridge.json")
    p.add_argument("--device-id-file", default="./device_id.txt")
    args = p.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)


if __name__ == "__main__":
    main()
