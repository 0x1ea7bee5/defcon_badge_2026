# wifi_defs.h

Shared constants, enumerations, and data structures for the wifi subsystem.
All other wifi files include this header.

---

## Constants

### Frame Control

| Constant | Value | Description |
|---|---|---|
| `FC_TYPE_MGMT` | `0x00` | Management frame type (in FC byte 0) |
| `FC_TYPE_CTRL` | `0x04` | Control frame type |
| `FC_SUBTYPE_BEACON` | `0x80` | Beacon management subtype |
| `FC_SUBTYPE_ACTION` | `0xD0` | Action management subtype |
| `FC_SUBTYPE_ACTION_NOACK` | `0xE0` | Action No Ack management subtype |
| `FC_SUBTYPE_NDPA` | `0x50` | NDP Announcement control subtype |

### Action Frame Categories and Codes

| Constant | Value | Description |
|---|---|---|
| `ACTION_CAT_VHT` | 21 | VHT category (802.11ac) |
| `ACTION_CAT_HE` | 30 | HE category (802.11ax) |
| `VHT_ACTION_CBF` | 0 | VHT Compressed Beamforming action |
| `HE_ACTION_CBF_CQI` | 2 | HE Compressed BF and CQI action |

### Subcarrier Counts

Naming scheme: `{STD}_NSC_{BW}_{NG}` where NG is the grouping factor.

| Bandwidth | Ng=1 | Ng=2 | Ng=4 |
|---|---|---|---|
| 20 MHz | 52 | 26 | 13 |
| 40 MHz | 108 | 54 | 27 |
| 80 MHz | 234 | 117 | 59 |
| 160 MHz | 468 | 234 | 117 |

HE uses the same table (`HE_NSC_*`). EHT 320 MHz is not yet tabled; the
subcarrier count lookup currently caps at 160 MHz.

### Angle Bit Widths

| Standard | Bits per phi | Bits per psi |
|---|---|---|
| VHT (`VHT_BPHI` / `VHT_BPSI`) | 7 | 5 |
| HE codebook size 4 (`HE_BPHI_CB4` / `HE_BPSI_CB4`) | 4 | 2 |
| HE codebook size 7 (`HE_BPHI_CB7` / `HE_BPSI_CB7`) | 7 | 5 |

---

## Enumerations

### `wifi_phy_type_t`
PHY generation of a beamforming feedback report.

| Value | Meaning |
|---|---|
| `WIFI_PHY_VHT` | 802.11ac |
| `WIFI_PHY_HE` | 802.11ax |

### `wifi_bw_t`
Channel bandwidth.

| Value | Bandwidth |
|---|---|
| `WIFI_BW_20MHZ` | 20 MHz |
| `WIFI_BW_40MHZ` | 40 MHz |
| `WIFI_BW_80MHZ` | 80 MHz |
| `WIFI_BW_160MHZ` | 160 MHz |

### `wifi_cap_flags_t`
Bitmask flags for AP capabilities stored in `wifi_ap_info_t.cap_flags`.

| Flag | Bit | Meaning |
|---|---|---|
| `WIFI_CAP_HT` | 0 | HT (802.11n) capable |
| `WIFI_CAP_VHT` | 1 | VHT (802.11ac) capable |
| `WIFI_CAP_HE` | 2 | HE (802.11ax) capable |

---

## Data Structures

### `wifi_cbf_result_t`

Result of parsing a compressed beamforming feedback frame.

**Angle storage:**
The steering matrix V (Nr × Nc) is encoded per subcarrier using Givens
rotations. Angles are stored in two flat arrays:

```
phi[subcarrier * phi_count + angle_idx]
psi[subcarrier * psi_count + angle_idx]
```

Counts per subcarrier are derived from the stream dimensions:

```
phi_count = Nc * Nr - Nc * (Nc + 1) / 2
psi_count = Nc * (Nc - 1) / 2
```

The angle ordering within a subcarrier matches 802.11-2020:
- phi_{l,m} for l = 0..Nc-1, m = l+1..Nr-1 (phi angles)
- psi_{l,m} for l = 0..Nc-1, m = l+1..Nc-1 (psi angles)

**Pitfall:** `phi` and `psi` are dynamically allocated. Always call
`wifi_cbf_result_free()` after processing a result.

**SNR units:** signed 8-bit, 0.25 dB per LSB.

| Field | Type | Description |
|---|---|---|
| `src_mac` | `uint8_t[6]` | Transmitter MAC address |
| `dialog_token` | `uint8_t` | Sounding dialog token |
| `timestamp_us` | `int64_t` | Reception timestamp (microseconds) |
| `snr[8]` | `int8_t[]` | SNR per spatial stream (0.25 dB units) |
| `num_streams` | `uint8_t` | Nc — columns sounded |
| `num_rows` | `uint8_t` | Nr — receive antenna rows |
| `rssi` | `int8_t` | Received signal strength (dBm) |
| `channel` | `uint8_t` | Received on channel |
| `num_subcarriers` | `uint16_t` | Subcarriers in the report |
| `phi_count` | `uint8_t` | Phi angles per subcarrier |
| `psi_count` | `uint8_t` | Psi angles per subcarrier |
| `phy_type` | `wifi_phy_type_t` | VHT / HE / EHT |
| `bandwidth` | `wifi_bw_t` | Channel bandwidth |
| `phi` | `int16_t *` | Dynamically allocated phi angle array |
| `psi` | `int16_t *` | Dynamically allocated psi angle array |

### `wifi_ndpa_result_t`

Result of parsing an NDP Announcement frame.

| Field | Type | Description |
|---|---|---|
| `src_mac` | `uint8_t[6]` | Transmitter MAC (TA field) |
| `dst_mac` | `uint8_t[6]` | Receiver MAC (RA field) |
| `dialog_token` | `uint8_t` | Sounding dialog token |

### `wifi_ap_info_t`

AP information extracted from a beacon frame.

| Field | Type | Description |
|---|---|---|
| `src_mac` | `uint8_t[6]` | AP MAC address |
| `ssid` | `char[33]` | Null-terminated SSID string |
| `timestamp_us` | `int64_t` | Reception timestamp (microseconds) |
| `rssi` | `int8_t` | Received signal strength (dBm) |
| `channel` | `uint8_t` | Beacon channel |
| `cap_flags` | `uint32_t` | `wifi_cap_flags_t` bitmask |
| `ht_cap_info` | `uint16_t` | Raw HT Capabilities Info field |
| `vht_cap_info` | `uint32_t` | Raw VHT Capabilities Info field |
| `rates` | `wifi_rate_set_t` | Supported + extended supported rates |

### `wifi_rate_set_t`

| Field | Type | Description |
|---|---|---|
| `rates[16]` | `uint8_t[]` | Rate values in 500 kbps units; MSB = basic rate flag |
| `count` | `uint8_t` | Number of valid entries |
