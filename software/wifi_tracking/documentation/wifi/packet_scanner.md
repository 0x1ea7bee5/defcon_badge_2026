# packet_scanner.c / packet_scanner.h

Promiscuous-mode packet capture and 802.11 frame parsing for beamforming
feedback (VHT/HE), NDP Announcements, and beacon frames.

---

## Design Overview

A single ESP-IDF promiscuous callback (`promiscuous_rx_cb`) is registered
internally by `start_monitor`. It dispatches each received frame to three
independent parse functions, calling the user-supplied callbacks when a
frame matches.

```
ESP-IDF promiscuous driver
        |
        v
  promiscuous_rx_cb()
     |        |        |
     v        v        v
scan_for_cbf  scan_for_ndpa  scan_for_ssid
     |        |        |
  on_cbf   on_ndpa  on_ssid   (user callbacks)
```

Parse functions are also callable directly with raw frame bytes, which
makes unit testing straightforward without requiring the WiFi driver.

---

## Public API

### `scan_for_cbf`

```c
bool scan_for_cbf(const uint8_t *frame, uint16_t len,
                  const wifi_pkt_rx_ctrl_t *rx_ctrl,
                  wifi_cbf_result_t *out);
```

Filters Action and Action No Ack frames for VHT (category 21, action 0)
and HE (category 30, action 0) compressed beamforming feedback. Parses
the MIMO Control field, angle report, and per-stream SNR bytes into `out`.

**Pitfalls:**
- `out->phi` and `out->psi` are heap-allocated on success. Call
  `wifi_cbf_result_free(out)` after use.
- VHT: the actual subcarrier count is derived from the received frame
  length (see [VHT subcarrier derivation](#vht-subcarrier-derivation)).
  Real APs may report fewer subcarriers than the spec table maximum.

### `scan_for_ndpa`

```c
bool scan_for_ndpa(const uint8_t *frame, uint16_t len,
                   wifi_ndpa_result_t *out);
```

Filters Control frames with subtype 0x05 (NDP Announcement). Extracts
RA (destination), TA (source), and the sounding dialog token byte.

No dynamic allocation; `out` is fully value-typed.

### `scan_for_ssid`

```c
bool scan_for_ssid(const uint8_t *frame, uint16_t len,
                   const wifi_pkt_rx_ctrl_t *rx_ctrl,
                   wifi_ap_info_t *out);
```

Filters Beacon management frames. Walks the Information Element list to
extract SSID, supported rates, extended supported rates, HT Capabilities,
VHT Capabilities, and presence of HE Capability extension IEs.

No dynamic allocation.

### `start_monitor`

```c
esp_err_t start_monitor(uint8_t channel,
                         cbf_cb_t  on_cbf,
                         ndpa_cb_t on_ndpa,
                         ssid_cb_t on_ssid);
```

Enables promiscuous mode filtered to management and control frames.
Sets the initial channel. Any callback argument may be NULL to skip that
frame type. Returns `ESP_ERR_INVALID_STATE` if already active.

### `switch_channel`

```c
esp_err_t switch_channel(uint8_t channel);
```

Changes the sniffing channel mid-capture via `esp_wifi_set_channel`.
Intended for rapid channel hopping. Safe to call from a FreeRTOS task;
do not call from inside the promiscuous callback.

### `stop_monitor`

```c
esp_err_t stop_monitor(void);
```

Disables promiscuous mode and clears all callback registrations.
After this returns, the WiFi driver can be reconfigured for station or
AP mode.

### `wifi_cbf_result_free`

```c
void wifi_cbf_result_free(wifi_cbf_result_t *result);
```

Frees `result->phi` and `result->psi` and sets both to NULL.
Safe to call on a zeroed struct (no-op).

---

## CBF Frame Layout

Both VHT and HE place SNR immediately after the MIMO Control field,
before the angle data. The only structural difference is the MIMO
Control field length (3 bytes for VHT, 4 bytes for HE).

### VHT (category 21, action 0)

```
body[0]          category = 21
body[1]          action   = 0
body[2..4]       VHT MIMO Control (3 bytes)
body[5..5+Nc-1]  per-stream SNR (Nc bytes, signed 8-bit, 0.25 dB/LSB)
body[5+Nc..]     bit-packed phi/psi angles (Ns subcarriers)
```

### HE (category 30, action 0)

```
body[0]          category = 30
body[1]          action   = 0
body[2..5]       HE MIMO Control (4 bytes)
body[6..6+Nc-1]  per-stream SNR (Nc bytes, signed 8-bit, 0.25 dB/LSB)
body[6+Nc..]     bit-packed phi/psi angles (Ns subcarriers)
```

---

## Angle Extraction Details

### Givens Rotation Counts

For an Nr × Nc steering matrix both phi and psi use the same count:

```
phi_count = psi_count = Nc * Nr - Nc * (Nc + 1) / 2
```

Example values:

| Nc | Nr | phi_count | psi_count |
|---|---|---|---|
| 1 | 2 | 1 | 1 |
| 2 | 2 | 1 | 1 |
| 2 | 3 | 3 | 3 |
| 2 | 4 | 5 | 5 |
| 4 | 4 | 6 | 6 |

### Bit Packing

Angles are packed LSB-first in the beamforming report field. The internal
`extract_bits` helper reads up to 16 bits starting at an arbitrary bit
offset.

Within each subcarrier, phi and psi are **interleaved per rotation pair**:
```
for l = 0..Nc-1:
  for m = l+1..Nr-1:
    phi_{m,l}  (bphi bits)
    psi_{m,l}  (bpsi bits)
```

VHT uses fixed widths `bphi=7`, `bpsi=5`. HE widths depend on
feedback type (SU/MU) and codebook bit:

| Type | Codebook | bphi | bpsi |
|---|---|---|---|
| SU | 0 | 4 | 2 |
| SU | 1 | 6 | 4 |
| MU | 0 | 7 | 5 |
| MU | 1 | 9 | 7 |

### VHT Subcarrier Count

The subcarrier count is determined by the spec table (`subcarrier_count`),
keyed on bandwidth and grouping index Ng:

| BW     | Ng=1 | Ng=2 | Ng=4 |
|--------|------|------|------|
| 20 MHz |   52 |   26 |   13 |
| 40 MHz |  108 |   54 |   27 |
| 80 MHz |  234 |  117 |   59 |
|160 MHz |  468 |  234 |  117 |

If the received frame is shorter than the spec count predicts (e.g., the
AP firmware reports fewer subcarriers or the capture is truncated), the
parser prints a warning and returns false:

```
VHT WARNING: spec=234 avail=196 subcarriers; dropping frame
```

HE uses the same spec table lookup for its subcarrier count.

### SNR Field

**VHT**: SNR bytes are at the end of the body, after the packed angles.
Offset = `5 + ceil(Ns * bits_per_sc / 8)`.

**HE**: SNR bytes are at `body[6]` through `body[6 + Nc - 1]`, before
the angles.

Each byte is a signed 8-bit value in units of 0.25 dB.

---

## Debug Helpers

Two static functions print a full annotated byte dump when called during
parsing. They are gated by calls in `parse_vht_cbf` and `parse_he_cbf`
and can be toggled by commenting out the call sites.

- `dump_vht_cbf_body` — labels each byte as header field, angle byte
  (with approx. subcarrier index), or SNR byte, using the table-expected
  subcarrier count so any mismatch with the frame-derived count is visible.
- `dump_he_cbf_body` — same concept for HE, accounting for the
  SNR-before-angles layout and codebook-dependent bit widths.

---

## Callback Lifetime

Results passed to user callbacks are stack-allocated (ndpa, ssid) or
have their heap fields freed immediately after the callback returns (cbf).
Do not store result pointers beyond the callback scope. Copy any fields
you need.

---

## Known Limitations

- HE subcarrier count is still looked up from the spec table rather than
  derived from frame length; short HE frames may fail to parse.
- MU beamforming feedback (multi-STA NDPA / MU exclusive report) is not
  parsed; only SU and MU-STA compressed beamforming are supported.
- Debug printf statements (`dump_vht_cbf_body`, per-subcarrier counts,
  SNR prints) are present in the parse path and should be removed or
  gated behind a compile flag before production use.
