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

> **Security note:** these cameras also tend to leave an X11 port (6000) open.
> Consider isolating the camera on a guest VLAN.

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
git clone https://github.com/<you>/philips-avent-homekit.git
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

# 4) (audio) build an ffmpeg with libfdk_aac
./scripts/build-ffmpeg-fdk.sh
```

Run the bridge permanently with a template from `templates/`: the macOS
LaunchAgent (`launchctl bootstrap gui/$(id -u) …`, no sudo) or the Linux
systemd unit (`systemctl enable --now aventproxy`). Replace `__INSTALL_DIR__`
(and `__USER__`) first.

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
  it, and `ffmpeg-for-homebridge` ships no macOS-arm64 binary. `brew … --with-fdk-aac`
  refuses if Xcode.app is older than Homebrew wants — so `build-ffmpeg-fdk.sh`
  compiles directly with the Command Line Tools. The encoder also needs
  `-flags +global_header` (AAC-ELD can't go in ADTS).
- **Don't use `vcodec: copy`.** The source has occasional corrupt slices and
  irregular keyframes; copy passes them through and the picture freezes until
  the next keyframe. Re-encoding (VideoToolbox `-realtime 1 -bf 0`, short GOP)
  conceals errors, emits regular keyframes, and keeps A/V latency low.
- **The Tuya session expires (days).** Re-run `avent-login.py` to refresh
  `philips_avent_bridge.json`, then restart the bridge. Signaling still goes
  through the Tuya cloud — this is not a fully local solution.
- **Data center matters.** A Tuya account lives in one region; `avent-login.py`
  probes them if you don't pass `--country`.

## Status / models

Confirmed with **SCD641**. The upstream integration is model-agnostic (generic
Tuya API) and reports SCD643, SCD951, SCD953, SCD971, SCD921, SCD973/SCD923 as
working. If discovery finds nothing, make sure the camera is online in the app.

## License

MIT — see [LICENSE](LICENSE). Builds on and credits
[thekoma/aventproxy](https://github.com/thekoma/aventproxy) (MIT).
