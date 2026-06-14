# telemetry

Serialization and transmission of CBF data over USB serial for live plotting.

---

## Wire format

Every packet follows this layout:

```
[MAGIC 4B: CB F1 FE ED]
[PAYLOAD_LEN 2B little-endian]
[PAYLOAD N bytes]
[XOR_CHECKSUM 1B]  (XOR of all payload bytes)
```

Magic and length are **not** covered by the checksum.  The Python parser
scans the byte stream for the magic sequence and re-syncs automatically if
it detects a checksum mismatch, making the format robust to interleaved
`printf`/`ESP_LOGI` output on the same USB serial port.

---

## Payload layout

| Field | Size | Notes |
|---|---|---|
| `src_mac` | 6 B | Transmitter MAC address |
| `num_streams` (Nc) | 1 B | Number of spatial streams |
| `num_rows` (Nr) | 1 B | Number of receive antennas |
| `num_subcarriers` | 2 B LE | Total subcarriers in this packet |
| `phy_type` | 1 B | 0 = VHT, 1 = HE |
| `is_mu` | 1 B | 0 = SU, 1 = MU |
| `bandwidth` | 1 B | `wifi_bw_t` enum value |
| `snr[0..Nc-1]` | Nc × int8 | SNR in units of 0.25 dB |
| *Per subcarrier (×num_subcarriers):* | | |
| `phi[0..phi_count-1]` | phi_count × int16 LE | Raw phi angle indices |
| `psi[0..psi_count-1]` | psi_count × int16 LE | Raw psi angle indices |
| `v_real[Nr × Nc]` | Nr·Nc × float32 LE | Real part of steering matrix V, row-major |
| `v_imag[Nr × Nc]` | Nr·Nc × float32 LE | Imaginary part of V, same layout |

Column `k` of V is the beamforming vector for spatial stream `k`.

`phi_count = psi_count = Nc·Nr − Nc·(Nc+1)/2`

---

## `telemetry_send`

```c
bool telemetry_send(const uint8_t *data, size_t len);
```

Writes raw bytes to the USB serial JTAG peripheral via
`usb_serial_jtag_write_bytes()`.  Returns `false` on NULL or zero-length
input.

---

## `telemetry_send_cbf`

```c
bool telemetry_send_cbf(const wifi_cbf_result_t *cbf);
```

For every subcarrier in `cbf`:
1. Calls `reconstruct_v()` to build the Nr × Nc steering matrix V.
2. Calls `get_eigendecomp()` to compute eigenvalues and eigenvectors of V·V^H.
3. Streams the framed binary packet over USB serial.

On `reconstruct_v` or `get_eigendecomp` failure for a single subcarrier,
that subcarrier's eigenvalue and eigenvector fields are zero-filled so the
total packet length remains consistent.

---

## Python live plotter

`python_tools/plot_telemetry.py` reads the telemetry stream and displays a
live two-pane plot that updates every 500 ms.

### Wire format note

The firmware hex-encodes every packet as a single `TELEM:<hex>\n` text line,
making it immune to ESP-IDF's CR/LF translation of raw 0x0A bytes.  The Python
parser reads lines with `ser.readline()` and decodes hex before parsing.

### Left pane — CBF vector trajectory panel (top)

Plots the complex value of each steering vector element across every received
subcarrier for the **selected MAC address** (chosen with the MAC slider).

- **Color**: unique per spatial stream index k (column of V).
- **Linestyle**: unique per receive antenna element j (row of V).
- Each trajectory is one connected line; the first subcarrier is marked with a
  circle and the last with a square.
- A unit-circle reference ring is drawn for orientation.
- Clicking a stream color entry in the legend toggles that stream
  hidden/visible in **both** left and right panes simultaneously.

### Left pane — SNR bar chart (bottom)

Grouped bar chart showing SNR per spatial stream for every tracked MAC.

### Right pane — per-subcarrier CBF vector complex plane

Columns of the steering matrix V for the **selected subcarrier** (chosen with
the subcarrier slider).

- Color encodes MAC address; linestyle encodes spatial stream index k.
- Each receive antenna element is a point; elements within one stream vector
  are connected as a line.
- Clicking a stream linestyle entry hides/shows that stream index across both
  panes.

### Interactivity

| Control | Effect |
|---|---|
| MAC slider | Selects which MAC to show in the left top pane |
| Subcarrier slider | Selects which subcarrier to show in the right pane |
| Toolbar zoom/pan | Preserved across animation frames; resets when slider changes |
| Click legend entry | Toggles eigenvector visibility in both panes |

### Usage

```bash
cd python_tools
source venv/bin/activate          # if using a venv
pip install pyserial numpy matplotlib
python3 plot_telemetry.py /dev/ttyACM0 --baud 115200
```
