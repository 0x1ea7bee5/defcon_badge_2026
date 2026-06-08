# packet_scanner.c / packet_scanner.h

Promiscuous-mode packet capture and 802.11 frame parsing for beamforming
feedback (VHT/HE/EHT), NDP Announcements, and beacon frames.

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
and HE (category 30, action 2) compressed beamforming feedback. Parses
the MIMO Control field, angle report, and per-stream SNR bytes into `out`.

**Pitfalls:**
- `out->phi` and `out->psi` are heap-allocated on success. Call
  `wifi_cbf_result_free(out)` after use.
- If the frame body is shorter than the angle count predicts, parsing
  fails and any partial allocation is freed before returning false.

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
VHT Capabilities, and presence of HE/EHT Capability extension IEs.

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
Intended for rapid channel hopping to cover multiple channels in sequence.
Safe to call from a FreeRTOS task; avoid calling from the promiscuous
callback itself.

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

## Angle Extraction Details

### Givens Rotation Counts

For an Nr × Nc steering matrix:

```
phi_count(Nc, Nr) = Nc * Nr - Nc * (Nc + 1) / 2
psi_count(Nc)     = Nc * (Nc - 1) / 2
```

Example values:

| Nc | Nr | phi_count | psi_count |
|---|---|---|---|
| 1 | 2 | 1 | 0 |
| 1 | 4 | 3 | 0 |
| 2 | 2 | 1 | 1 |
| 2 | 4 | 5 | 1 |
| 4 | 4 | 6 | 6 |

### Bit Packing

Angles are packed LSB-first in the beamforming report field. The internal
`extract_bits` helper reads up to 16 bits starting at an arbitrary bit
offset. The loop pre-validates that the total bit count fits within the
available report bytes before starting extraction.

Angle ordering within a subcarrier (802.11-2020):
1. phi_{l,m} for l = 0..Nc-1, m = l+1..Nr-1
2. psi_{l,m} for l = 0..Nc-1, m = l+1..Nc-1

### SNR Field

Per-stream SNR bytes follow immediately after the packed angle data.
Each byte is a signed 8-bit value in units of 0.25 dB. The byte offset
is computed from the total angle bit count rounded up to a byte boundary.

---

## Callback Lifetime

Results passed to user callbacks are stack-allocated (ndpa, ssid) or
have their heap fields freed immediately after the callback returns (cbf).
Do not store result pointers beyond the callback scope. Copy any fields
you need.

---

## Known Limitations

- MU beamforming feedback (multi-STA NDPA / MU exclusive report) is not
  parsed; only SU compressed beamforming is supported.
- The HE MIMO Control dialog token position (byte 4 of the 6-byte field)
  should be verified against the final 802.11ax amendment for the target
  chip's firmware.
