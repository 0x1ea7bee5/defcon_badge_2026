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
| `FC_ORDER_MASK` | `0x80` | FC byte 1 bit 7: HT Control (+HTC) field present |

When `FC_ORDER_MASK` is set in FC byte 1, a 4-byte HT Control field is
inserted between the MAC header and the frame body (body offset becomes 28
instead of 24). 802.11ax (HE) devices commonly use this in action frames.

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

HE uses the same table (`HE_NSC_*`). The subcarrier count lookup caps at
160 MHz.

### Angle Bit Widths

From 802.11ax Table 9-98e. The HE Feedback Type (SU vs MU) is bit 3 of
MIMO control byte 1 (`HE_MIMO_FB_TYPE_MASK`). The Codebook bit is bit 2
(`HE_MIMO_CODEBOOK_MASK`).

| Standard / Mode | Codebook | Bφ | Bψ | Constants |
|---|---|---|---|---|
| VHT | — | 7 | 5 | `VHT_BPHI`, `VHT_BPSI` |
| HE SU | 0 | 4 | 2 | `HE_SU_BPHI_CB0`, `HE_SU_BPSI_CB0` |
| HE SU | 1 | 6 | 4 | `HE_SU_BPHI_CB1`, `HE_SU_BPSI_CB1` |
| HE MU | 0 | 7 | 5 | `HE_MU_BPHI_CB0`, `HE_MU_BPSI_CB0` |
| HE MU | 1 | 9 | 7 | `HE_MU_BPHI_CB1`, `HE_MU_BPSI_CB1` |

### HE MIMO Control Field

The HE MIMO Control field is **4 bytes** (32 bits):

| Byte | Bits | Field |
|---|---|---|
| 0 | [2:0] | Nc Index (streams − 1) |
| 0 | [5:3] | Nr Index (rows − 1) |
| 0 | [7:6] | Channel Width (0=20, 1=40, 2=80, 3=160) |
| 1 | [1:0] | Grouping (Ng index: 0=Ng1, 1=Ng2, 2=Ng4) |
| 1 | [2] | Codebook Information |
| 1 | [3] | Feedback Type (0=SU, 1=MU) |
| 1 | [7:4] | Remaining/First Feedback Segment |
| 2 | [6:0] | RU End Tone Index |
| 2 | [7] | Disambiguation |
| 3 | [5:0] | Sounding Dialog Token Number |
| 3 | [7:6] | Reserved |

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

Counts per subcarrier are derived from the stream dimensions (both VHT and HE):

```
phi_count = Nc * Nr - Nc * (Nc + 1) / 2
psi_count = phi_count   (one complex psi per real phi rotation pair)
```

**Angle ordering** within a subcarrier (802.11-2020 Table 9-33, 802.11ax):
for l=0..Nc-1, m=l+1..Nr-1: phi_{m,l} is immediately followed by psi_{m,l}
(interleaved). Each Givens rotation has one real angle (phi) and one complex
phase (psi), so the counts are always equal.

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
| `is_mu` | `bool` | True if MU-MIMO feedback (HE Feedback Type = 1) |
| `phy_type` | `wifi_phy_type_t` | VHT or HE |
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
