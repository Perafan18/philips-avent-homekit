# Philips Avent → HomeKit (native, via Homebridge)

Bring a **Philips Avent Connected Baby Monitor** (SCD6xx/SCD9xx, a Tuya
white-label) into **Apple Home** through **Homebridge**, running **natively** —
no Home Assistant, no Docker. Live 1080p video **and** audio.

> **Credit where it's due.** The hard part — reverse-engineering Tuya's Mobile
> SDK auth and turning the camera's WebRTC feed into RTSP — is the work of
> [**thekoma/aventproxy**](https://github.com/thekoma/aventproxy) (MIT). That
> project targets Home Assistant (an integration + a WebRTC-bridge add-on).
> **This repo adds a thin layer** so you can run its Go bridge standalone and
> feed the RTSP into Homebridge instead. It does not copy upstream's code — it
> fetches it at setup and reuses its login/discovery logic.

## Why not just point ffmpeg at the camera's RTSP port?

The camera exposes an RTSP server on port 554, **but the H.264 payload is
encrypted/scrambled** (decoy SPS that change per GOP; slices don't decode). The
embedded RTSP server also rejects every `OPTIONS` request, so ffmpeg and
gortsplib abort before `DESCRIBE`. Nobody has decoded that direct stream; the
only working path is the same WebRTC channel the official app uses, via the
Tuya cloud — which is exactly what aventproxy does.

> **Security notes:**
> - The upstream Go bridge binds its RTSP server to **all interfaces** by
>   default, exposing the plaintext stream to your whole LAN. `setup.sh` applies
>   a patch (`patches/`) that makes it bind **loopback** instead (override with
>   `AVENT_RTSP_BIND`). **Do not skip that.**
> - These cameras also leave port **6000** open — it's Tuya's LAN/RTSP
>   negotiation (not an X11 server, despite the port number), but it's still
>   attack surface. Isolating the camera on a guest VLAN is good hygiene.

## How it works

```
Baby Monitor+ account (Tuya cloud)                 Your Mac / Linux box
       │  password + emailed MFA                          │
       ▼                                                   │
  Tuya cloud (a1.tuya**.com)  ◄── avent-login.py ──────────┤ writes philips_avent_bridge.json
       │  WebRTC (STUN/TURN)                               │  (session only, no password)
       ▼                                                   ▼
   camera  ─────────────►  avent-webrtc-bridge addon  ──►  rtsp://127.0.0.1:38554/<Camera>
                                                            │
                                                            ▼
                             homebridge-camera-ffmpeg  ──►  Apple Home
```

Everything is native: one Go binary + a small Python venv, kept alive by a
macOS LaunchAgent (or a systemd unit on Linux). The HomeKit advertisement is
done by Homebridge; the bridge just serves unicast RTSP.

## Prerequisites

- A Philips Avent **Baby Monitor+** account with the camera set up and online.
- **Homebridge** (already paired to your Home) + the **homebridge-camera-ffmpeg** plugin.
- **Go 1.24+**, **Python 3.11+**, **git**. macOS: `brew install go`.
- For **audio**: an ffmpeg with `libfdk_aac` (see step 4) — HomeKit requires AAC-ELD.

## Setup

```bash
git clone https://github.com/Perafan18/philips-avent-homekit.git
cd philips-avent-homekit

# 1) Fetch upstream aventproxy, build the Go bridge, make the venv
./scripts/setup.sh

# 2) Log in (prompts for password + the 6-digit code Tuya emails you).
#    Writes philips_avent_bridge.json — the session only, never your password.
./.venv/bin/python scripts/avent-login.py --email you@example.com --country XX
#    Note the RTSP path it prints, e.g. .../Philips_Avent_Connected_Baby_Camera

# 3) Serve the camera as RTSP (test it, then run it as a service)
./bin/avent-webrtc-bridge addon --config philips_avent_bridge.json
#    verify:  ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:38554/<Camera> -frames:v 1 -update 1 test.jpg

# 4) (audio) get an ffmpeg with libfdk_aac (HomeKit needs AAC-ELD)
./scripts/get-ffmpeg.sh            # official static binary — the easy path
# or ./scripts/build-ffmpeg-fdk.sh # fallback: compile one yourself
```

Run the bridge permanently with a template from `templates/`: the macOS
LaunchAgent (`launchctl bootstrap gui/$(id -u) …`, no sudo) or the Linux
systemd unit (`systemctl enable --now aventproxy`). Replace `__INSTALL_DIR__`
(and `__USER__`) first.

### Keep it alive (session watchdog)

The Tuya session expires periodically. `scripts/session-watchdog.py` keeps it
alive with a keepalive call and posts a **one-time macOS notification** when a
human re-login is actually needed (or when the camera drops offline) instead of
you finding a black screen. Store the password once in the Keychain, copy
`templates/watchdog.env.example` to `watchdog.env`, and load
`templates/com.example.aventproxy-watchdog.plist` (runs every 6h). Run
`session-watchdog.py --probe` once to see whether your unit even needs MFA on
re-login (on the SCD641 it does — so re-login stays a rare human step).

## Homebridge

1. Install the **homebridge-camera-ffmpeg** plugin.
2. Copy `templates/homebridge-camera-config.example.json` into your Homebridge
   `config.json` `platforms` array. Replace `<CameraName>` and, for audio, set
   `videoProcessor` to the ffmpeg from step 4.
3. Restart Homebridge.
4. **Add the camera manually** in the Home app: `+ → Add Accessory →
   More options… →` pick the camera → enter your Homebridge setup code →
   *Add Anyway* (uncertified). Cameras can't be bridged, so they always need
   this one-time manual add.

## Gotchas we hit (so you don't have to)

- **`unbridge: true` is required.** homebridge-camera-ffmpeg bridges cameras by
  default; HomeKit ignores bridged cameras. Unbridged, it's a separate
  accessory you add manually.
- **Audio needs AAC-ELD → `libfdk_aac`.** The stock/Homebrew ffmpeg doesn't have
  it. The easiest fix is the **prebuilt static binary** from
  `ffmpeg-for-homebridge` (`scripts/get-ffmpeg.sh`) — it ships macOS arm64/x86_64
  and Linux builds with `libfdk_aac` + hardware accel, and has no Homebrew
  dependencies to break on `brew upgrade`. Only if no prebuilt fits you,
  `build-ffmpeg-fdk.sh` compiles one with the Command Line Tools (needed because
  `brew … --with-fdk-aac` refuses on an older Xcode.app). Either way the encoder
  needs `-flags +global_header` (AAC-ELD can't go in ADTS).
- **Don't use `vcodec: copy`.** The source has occasional corrupt slices and
  irregular keyframes; copy passes them through and the picture freezes until
  the next keyframe. Re-encoding (VideoToolbox `-realtime 1 -bf 0`, short GOP)
  conceals errors, emits regular keyframes, and keeps A/V latency low.
- **The Tuya session expires (days).** There is **no refresh token**, and a
  fresh login needs the emailed MFA code every time (the persisted `device_id`
  is *not* treated as a trusted terminal), so this can't be fully automated.
  The session watchdog (above) keeps the SID alive and nudges you when a
  re-login is actually needed; then re-run `avent-login.py` and restart the
  bridge. Signaling still goes through the Tuya cloud — this is not fully local.
- **Data center matters.** A Tuya account lives in one region; `avent-login.py`
  probes them if you don't pass `--country`.

## Status / models

Confirmed with **SCD641**. The upstream integration is model-agnostic (generic
Tuya API) and reports SCD643, SCD951, SCD953, SCD971, SCD921, SCD973/SCD923 as
working. If discovery finds nothing, make sure the camera is online in the app.

## Disclaimer — read before using

This is a **personal project, shared as-is, with no warranty and no support.**
Please don't open issues expecting maintenance.

- **Not affiliated with, endorsed by, or supported by Philips or Tuya.** All
  trademarks belong to their owners.
- It works by talking to the Tuya cloud the same way the official Baby Monitor+
  app does. That can **break at any time** (session expiry, app-key changes,
  API changes) and **may conflict with the vendor's Terms of Service.**
- Use it **only on your own devices and your own account**, at your own risk.
- The reverse-engineering it depends on lives in
  [thekoma/aventproxy](https://github.com/thekoma/aventproxy); this repo only
  adds a standalone + Homebridge wrapper around it.

## License

MIT — see [LICENSE](LICENSE). Builds on and credits
[thekoma/aventproxy](https://github.com/thekoma/aventproxy) (MIT).
