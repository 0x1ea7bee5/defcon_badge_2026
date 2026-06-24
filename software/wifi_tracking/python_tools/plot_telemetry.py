#!/usr/bin/env python3
"""
Live plotter for ESP32 telemetry — CBF and CSI modes.

Usage:
    python3 plot_telemetry.py /dev/ttyACM0 [--baud 115200]
    python3 plot_telemetry.py /dev/ttyACM0 --baud 115200
Opens two windows:
  - CBF window: steering vector trajectories and SNR per stream
  - CSI window: amplitude spectrum and rolling waterfall

Wire format — one text line per packet:
    TELEM:<hex>\\n
where <hex> encodes: MAGIC(4B) PAYLOAD_LEN(2B LE) PAYLOAD(N B) XOR_CKSUM(1B)
CKSUM is XOR of all PAYLOAD bytes.

CBF magic  CB F1 FE ED
CSI magic  C5 1D FE ED

CBF payload:
    src_mac          6B
    num_streams (Nc) 1B
    num_rows    (Nr) 1B
    num_subcarriers  2B LE
    phy_type         1B  (0=VHT, 1=HE)
    is_mu            1B
    bandwidth        1B
    snr[0..Nc-1]     Nc x int8   (0.25 dB/LSB)
    Per subcarrier (num_subcarriers times):
        phi[0..phi_count-1]  phi_count x int16 LE
        psi[0..psi_count-1]  psi_count x int16 LE
        v_real[Nr x Nc]      Nr*Nc x float32 LE  row-major
        v_imag[Nr x Nc]      Nr*Nc x float32 LE  row-major

CSI payload:
    src_mac          6B
    dst_mac          6B
    rssi             1B  (int8)
    channel          1B
    num_samples      2B LE  (total int8 values; num_samples/2 I/Q pairs)
    data[num_samples]       int8  (interleaved I,Q,I,Q,...)

CBF window interactivity:
    - Subcarrier slider: which subcarrier to display in the right pane.
    - MAC slider: which MAC to focus in the left trajectory pane.
    - Click a stream legend entry to show/hide that stream across both panes.
    - Zoom is preserved across frames; slider changes reset the affected pane.
"""

import argparse
import struct
import threading
import time

import numpy as np
from scipy.interpolate import interp1d as _interp1d
import matplotlib.pyplot as plt
import rare_est
import matplotlib.gridspec as gridspec
from matplotlib.animation import FuncAnimation
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.widgets import Button, Slider
INTERVAL = 500


class _PauseMixin:
    """Pause/resume mixin: serial reader keeps caching; drawing stops."""

    _paused    = False
    _pause_btn = None

    def _toggle_pause(self, _event):
        self._paused = not self._paused
        if self._pause_btn:
            self._pause_btn.label.set_text(
                'Play' if self._paused else 'Pause')
            self._pause_btn.ax.figure.canvas.draw_idle()

    def _add_pause_button(self, fig):
        """Add a Pause/Play toggle in the top-left figure margin."""
        ax_btn = fig.add_axes([0.01, 0.955, 0.07, 0.030])
        self._pause_btn = Button(ax_btn, 'Pause', color='lightgrey')
        self._pause_btn.on_clicked(self._toggle_pause)


# Set True to apply a moving-average smoothing filter to all CBF plots.
DENOISE     = False
DENOISE_WIN = 5      # smoothing window width (samples)
DENOM_LIM   = 0.01  # discard ratio points where |denominator| < this

# Set True to apply cubic-spline oversampling after denoising.
# Applied after DENOISE so the two filters compose: denoise → oversample.
OVERSAMP_FILT   = True
OVERSAMP_FACTOR = 4   # integer upsampling factor (≥ 2 for effect)

# RARE-L array geometry.  None = Uniform Linear Array (1D, nr elements).
# Set to (M_rows, N_cols) for a Uniform Planar Array where M*N == nr.
#ARRAY_SHAPE = None
ARRAY_SHAPE = (2,2)


def _smooth_1d(arr):
    """Moving-average then cubic oversampling (each step independently gated).

    Args:
        arr (array-like): 1-D real signal.
    Returns:
        np.ndarray: filtered signal, length may grow with OVERSAMP_FACTOR.
    """
    arr = np.asarray(arr, dtype=float)
    if DENOISE and DENOISE_WIN > 1 and len(arr) >= DENOISE_WIN:
        k   = np.ones(DENOISE_WIN) / DENOISE_WIN
        arr = np.convolve(arr, k, mode='same')
    return _oversample_1d(arr)


def _smooth_phase_1d(arr):
    """Phase-aware smooth then oversampling (handles ±π wrap at both steps).

    Args:
        arr (array-like): 1-D phase signal in radians.
    Returns:
        np.ndarray: filtered phase, length may grow with OVERSAMP_FACTOR.
    """
    arr = np.asarray(arr, dtype=float)
    if DENOISE and DENOISE_WIN > 1 and len(arr) >= DENOISE_WIN:
        c   = np.exp(1j * arr)
        k   = np.ones(DENOISE_WIN) / DENOISE_WIN
        arr = np.angle(
            np.convolve(c.real, k, mode='same')
            + 1j * np.convolve(c.imag, k, mode='same'))
    return _oversample_phase_1d(arr)


def _smooth_2d(mat):
    """2-D box filter then 2-D cubic oversampling.

    Args:
        mat (np.ndarray): 2-D real matrix (rows=time, cols=subcarrier).
    Returns:
        np.ndarray: filtered matrix, shape may grow with OVERSAMP_FACTOR.
    """
    if DENOISE and DENOISE_WIN > 1:
        k   = np.ones(DENOISE_WIN) / DENOISE_WIN
        mat = np.apply_along_axis(
            lambda r: np.convolve(r, k, mode='same'), 1, mat)
        mat = np.apply_along_axis(
            lambda c: np.convolve(c, k, mode='same'), 0, mat)
    return _oversample_2d(mat)


def _smooth_phase_2d(mat):
    """Phase-aware 2-D smooth then 2-D oversampling.

    Args:
        mat (np.ndarray): 2-D phase matrix in radians.
    Returns:
        np.ndarray: filtered phase matrix, shape may grow with OVERSAMP_FACTOR.
    """
    if DENOISE and DENOISE_WIN > 1:
        c   = np.exp(1j * mat)
        k   = np.ones(DENOISE_WIN) / DENOISE_WIN
        def _conv(a):
            return np.convolve(a, k, mode='same')
        re  = np.apply_along_axis(_conv, 1,
              np.apply_along_axis(_conv, 0, c.real))
        im  = np.apply_along_axis(_conv, 1,
              np.apply_along_axis(_conv, 0, c.imag))
        mat = np.angle(re + 1j * im)
    return _oversample_phase_2d(mat)


def _oversample_1d(arr):
    """Cubic-spline interpolation by OVERSAMP_FACTOR along a 1-D signal.

    No-op when OVERSAMP_FILT=False, OVERSAMP_FACTOR<=1, or fewer than 2
    samples.

    Args:
        arr (np.ndarray): 1-D real signal, dtype float.
    Returns:
        np.ndarray: upsampled signal of length (n-1)*OVERSAMP_FACTOR + 1.
    """
    if not OVERSAMP_FILT or OVERSAMP_FACTOR <= 1 or len(arr) < 2:
        return arr
    n     = len(arr)
    x     = np.arange(n, dtype=float)
    x_new = np.linspace(0.0, n - 1,
                        (n - 1) * OVERSAMP_FACTOR + 1)
    kind  = ('cubic'    if n >= 4 else
             'quadratic' if n >= 3 else 'linear')
    return _interp1d(x, arr, kind=kind)(x_new)


def _oversample_phase_1d(arr):
    """Phase-aware 1-D oversampling via unit-circle interpolation.

    Interpolates on the complex unit circle to avoid ±π wrap artifacts.

    Args:
        arr (np.ndarray): 1-D phase signal in radians, dtype float.
    Returns:
        np.ndarray: upsampled phase signal.
    """
    if not OVERSAMP_FILT or OVERSAMP_FACTOR <= 1 or len(arr) < 2:
        return arr
    c = np.exp(1j * arr)
    return np.angle(
        _oversample_1d(c.real) + 1j * _oversample_1d(c.imag))


def _oversample_2d(mat):
    """Cubic-spline oversampling along the time axis (axis=0) only.

    Subcarrier axis is left at its original resolution.

    Args:
        mat (np.ndarray): 2-D real matrix (rows=time, cols=subcarrier).
    Returns:
        np.ndarray: upsampled matrix (more rows, same number of cols).
    """
    if not OVERSAMP_FILT or OVERSAMP_FACTOR <= 1:
        return mat
    nr = mat.shape[0]
    if nr < 2:
        return mat
    x   = np.arange(nr, dtype=float)
    xn  = np.linspace(0.0, nr - 1, (nr - 1) * OVERSAMP_FACTOR + 1)
    k   = ('cubic'    if nr >= 4 else
           'quadratic' if nr >= 3 else 'linear')
    return _interp1d(x, mat, axis=0, kind=k)(xn)


def _oversample_phase_2d(mat):
    """Phase-aware 2-D oversampling via unit-circle interpolation.

    Args:
        mat (np.ndarray): 2-D phase matrix in radians.
    Returns:
        np.ndarray: upsampled phase matrix.
    """
    if not OVERSAMP_FILT or OVERSAMP_FACTOR <= 1:
        return mat
    c = np.exp(1j * mat)
    return np.angle(
        _oversample_2d(c.real) + 1j * _oversample_2d(c.imag))


# -----------------------------------------------------------------------
# Protocol constants
# -----------------------------------------------------------------------

_MAGIC_CBF = bytes([0xCB, 0xF1, 0xFE, 0xED])
_MAGIC_CSI = bytes([0xC5, 0x1D, 0xFE, 0xED])
_PREFIX    = b'TELEM:'

_MAX_WATERFALL_ROWS = 60   # rolling waterfall history depth per MAC
_MAX_CPX_TRAIL      = 500  # complex-plane trail depth per MAC

# Row index used as denominator in spatial-stream ratios.
# Supports negative Python indexing (-1 = last row, etc.).


def mac_str(raw_bytes):
    return ':'.join(f'{b:02X}' for b in raw_bytes)


# -----------------------------------------------------------------------
# Packet parsing
# -----------------------------------------------------------------------

def _try_parse_line(line):
    """
    Parse one TELEM: hex line.
    Returns ('cbf', dict), ('csi', dict), or None.
    """
    line = line.strip()
    if not line.startswith(_PREFIX):
        return None
    try:
        data = bytes.fromhex(line[len(_PREFIX):].decode('ascii'))
    except Exception:
        return None

    if len(data) < 7:
        return None

    magic = data[:4]
    if magic not in (_MAGIC_CBF, _MAGIC_CSI):
        return None

    (length,) = struct.unpack_from('<H', data, 4)
    if len(data) < 6 + length + 1:
        return None

    payload  = data[6:6 + length]
    cksum_rx = data[6 + length]

    cksum_calc = 0
    for b in payload:
        cksum_calc ^= b
    if cksum_calc != cksum_rx:
        print('[parser] checksum mismatch — dropped', flush=True)
        return None

    if magic == _MAGIC_CBF:
        pkt = _parse_cbf_payload(payload)
        return ('cbf', pkt) if pkt is not None else None
    else:
        pkt = _parse_csi_payload(payload)
        return ('csi', pkt) if pkt is not None else None


def _parse_cbf_payload(payload):
    try:
        pos = 0
        mac        = mac_str(payload[pos:pos + 6]); pos += 6
        nc         = payload[pos];                  pos += 1
        nr         = payload[pos];                  pos += 1
        (num_sc,)  = struct.unpack_from('<H', payload, pos); pos += 2
        phy_type   = payload[pos]; pos += 1
        is_mu      = bool(payload[pos]); pos += 1
        bandwidth  = payload[pos]; pos += 1
        snr        = list(struct.unpack_from(f'{nc}b', payload, pos))
        pos       += nc

        phi_count = nc * nr - nc * (nc + 1) // 2
        psi_count = phi_count

        subcarriers = []
        for _ in range(num_sc):
            phi = list(struct.unpack_from(f'<{phi_count}h', payload, pos))
            pos += phi_count * 2
            psi = list(struct.unpack_from(f'<{psi_count}h', payload, pos))
            pos += psi_count * 2

            v_r = np.array(
                struct.unpack_from(f'<{nr * nc}f', payload, pos)
            ).reshape(nr, nc)
            pos += nr * nc * 4

            v_i = np.array(
                struct.unpack_from(f'<{nr * nc}f', payload, pos)
            ).reshape(nr, nc)
            pos += nr * nc * 4

            subcarriers.append({
                'phi':    phi,
                'psi':    psi,
                'v_real': v_r,
                'v_imag': v_i,
            })
    except Exception:
        return None

    return {
        'mac':         mac,
        'nc':          nc,
        'nr':          nr,
        'num_sc':      num_sc,
        'phy_type':    phy_type,
        'is_mu':       is_mu,
        'bandwidth':   bandwidth,
        'snr':         snr,
        'subcarriers': subcarriers,
    }


def _parse_csi_payload(payload):
    try:
        pos = 0
        src_mac        = mac_str(payload[pos:pos + 6]); pos += 6
        dst_mac        = mac_str(payload[pos:pos + 6]); pos += 6
        (rssi,)        = struct.unpack_from('b', payload, pos); pos += 1
        channel        = payload[pos];                           pos += 1
        (num_samples,) = struct.unpack_from('<H', payload, pos); pos += 2

        if pos + num_samples > len(payload):
            return None

        raw = np.frombuffer(
            payload[pos:pos + num_samples], dtype=np.int8
        ).astype(float)
        n_complex  = len(raw) // 2
        iq         = raw[:n_complex * 2].reshape(-1, 2)
        cplx       = iq[:, 0] + 1j * iq[:, 1]
        amplitudes = np.abs(cplx)
        phases     = np.angle(cplx)
    except Exception:
        return None

    return {
        'src_mac':    src_mac,
        'dst_mac':    dst_mac,
        'rssi':       rssi,
        'channel':    channel,
        'amplitudes': amplitudes,
        'phases':     phases,
    }


# -----------------------------------------------------------------------
# Shared data store + serial reader
# -----------------------------------------------------------------------

class SharedDataStore:
    """Thread-safe store for CBF and CSI packet data."""

    def __init__(self):
        self.lock      = threading.Lock()
        # CBF — latest subcarrier data per MAC
        self.cbf_data  = {}   # mac -> {nr, nc, snr, subcarriers}
        self.cbf_order = []   # insertion-ordered unique MACs
        # CBF — rolling waterfall history per MAC
        # Each frame: {'nr', 'nc', 'pairs': {(r,c): {'amp':1D, 'phase':1D}}}
        self.cbf_wf_data = {}
        # CBF — raw V matrix trail per MAC, per frame
        # Each frame: {'nr', 'nc', 'subcarriers': {sc_idx: (nr,nc) complex}}
        self.cbf_v_trail_data = {}
        # CBF — RARE-L DoA estimation history per MAC.
        # Each frame is the dict returned by rare_est.rare_l_estimate():
        #   {mode, eigenvalues, n_sources,
        #    az: {doa, roots, spectrum_db},
        #    el: {doa, roots, spectrum_db} or None (ULA)}
        self.rare_l_data = {}
        # CSI
        self.csi_data  = {}   # src_mac -> list of amplitude arrays
        self.csi_info  = {}   # src_mac -> {rssi, channel}
        self.csi_order = []   # insertion-ordered unique src MACs
        self._running  = True

    def _ingest_cbf(self, pkt):
        mac = pkt['mac']
        if mac not in self.cbf_data:
            self.cbf_data[mac] = {
                'nr':          pkt['nr'],
                'nc':          pkt['nc'],
                'snr':         pkt['snr'],
                'subcarriers': {},
            }
            self.cbf_order.append(mac)
            self.cbf_wf_data[mac]       = []
        else:
            self.cbf_data[mac]['snr'] = pkt['snr']
        for idx, sc in enumerate(pkt['subcarriers']):
            self.cbf_data[mac]['subcarriers'][idx] = sc

        # Compute per-antenna-pair, per-subcarrier amplitude and phase.
        # For V[r,c] at each subcarrier i:
        #   amp[i]   = |V[r,c]|
        #   phase[i] = angle(V[r,c])
        scs = pkt['subcarriers']
        if scs:
            nr   = pkt['nr']
            nc   = pkt['nc']
            n_sc = len(scs)
            pairs = {}
            for r in range(nr):
                for c in range(nc):
                    amp_row   = np.zeros(n_sc)
                    phase_row = np.zeros(n_sc)
                    for i, sc in enumerate(scs):
                        elem       = sc['v_real'][r, c] + 1j * sc['v_imag'][r, c]
                        amp_row[i]   = abs(elem)
                        phase_row[i] = float(np.angle(elem))
                    pairs[(r, c)] = {'amp': amp_row, 'phase': phase_row}
            self.cbf_wf_data[mac].append(
                {'nr': nr, 'nc': nc, 'pairs': pairs})
            if len(self.cbf_wf_data[mac]) > _MAX_WATERFALL_ROWS:
                self.cbf_wf_data[mac].pop(0)

            # v_all shape: (n_sc, nr, nc) — complex V per subcarrier.
            # V is (nr, nc): rows = Rx antennas, columns = spatial streams.
            v_all = np.array(
                [sc['v_real'] + 1j * sc['v_imag'] for sc in scs])

            # Raw V matrix trail — one frame per packet, all subcarriers
            v_trail_frame = {
                'nr': nr,
                'nc': nc,
                'subcarriers': {
                    i: scs[i]['v_real'] + 1j * scs[i]['v_imag']
                    for i in range(len(scs))
                },
            }
            if mac not in self.cbf_v_trail_data:
                self.cbf_v_trail_data[mac] = []
            self.cbf_v_trail_data[mac].append(v_trail_frame)
            if len(self.cbf_v_trail_data[mac]) > _MAX_CPX_TRAIL:
                self.cbf_v_trail_data[mac].pop(0)

            # RARE-L DoA estimation — run on each received packet.
            n_src = max(1, min(nc, nr - 1))
            try:
                result = rare_est.rare_l_estimate(
                    v_all, n_sources=n_src,
                    array_shape=ARRAY_SHAPE)
            except Exception:
                nan_d = np.full(n_src, np.nan)
                nan_z = np.full(n_src, np.nan + 0j)
                nan_s = np.zeros(len(rare_est._SCAN_ANGLES_DEG))
                result = {
                    'mode': 'ula', 'eigenvalues': np.array([]),
                    'n_sources': n_src,
                    'az': {'doa': nan_d, 'roots': nan_z, 'spectrum_db': nan_s},
                    'el': None,
                }
            if mac not in self.rare_l_data:
                self.rare_l_data[mac] = []
            self.rare_l_data[mac].append(result)
            if len(self.rare_l_data[mac]) > _MAX_CPX_TRAIL:
                self.rare_l_data[mac].pop(0)

    def _ingest_csi(self, pkt):
        mac = pkt['src_mac']
        if mac not in self.csi_data:
            self.csi_data[mac] = []
            self.csi_order.append(mac)
        self.csi_data[mac].append({
            'amp':   pkt['amplitudes'],
            'phase': pkt['phases'],
        })
        if len(self.csi_data[mac]) > _MAX_WATERFALL_ROWS:
            self.csi_data[mac].pop(0)
        self.csi_info[mac] = {
            'rssi':    pkt['rssi'],
            'channel': pkt['channel'],
        }

    def _reader(self, port, baud):
        import serial
        with serial.Serial(port, baud, timeout=1) as ser:
            while self._running:
                try:
                    line = ser.readline()
                    if not line:
                        continue
                    result = _try_parse_line(line)
                    if result is None:
                        continue
                    kind, pkt = result
                    with self.lock:
                        if kind == 'cbf':
                            self._ingest_cbf(pkt)
                        else:
                            self._ingest_csi(pkt)
                    src = pkt.get('mac') or pkt.get('src_mac', '?')
                    print(f'[reader] {kind.upper()} from {src}', flush=True)
                except Exception as exc:
                    print(f'[reader] {exc}', flush=True)
                    time.sleep(0.1)

    def start_reader(self, port, baud):
        t = threading.Thread(
            target=self._reader, args=(port, baud), daemon=True)
        t.start()


# -----------------------------------------------------------------------
# Visual constants
# -----------------------------------------------------------------------

COLORS = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728',
    '#9467bd', '#8c564b', '#e377c2', '#7f7f7f',
    '#bcbd22', '#17becf',
]
LINESTYLES = [
    '-', '--', ':', '-.',
    (0, (5, 1)), (0, (3, 1, 1, 1)), (0, (1, 1)), (0, (5, 5)),
]
MARKERS = ['o', 's', '^', 'D', 'v', 'P', '*', 'X']

_HIDDEN_ALPHA  = 0.15
_VISIBLE_ALPHA = 1.0


# -----------------------------------------------------------------------
# CBF Plotter
# -----------------------------------------------------------------------

class CBFPlotter(_PauseMixin):
    def __init__(self, store):
        self.store           = store
        self.current_sc      = 0
        self.current_mac_idx = 0
        self._hidden_streams = set()
        self._lmap           = {}
        self._track_lines    = []
        self._zoom           = {'mac_ev': None, 'right': None}
        self._prev_sc        = -1
        self._prev_mac_idx   = -1
        self._frame_count    = 0
        self.fig             = None

    # ----------------------------------------------------------------
    # Legend pick handler
    # ----------------------------------------------------------------

    def _on_pick(self, event):
        key = self._lmap.get(id(event.artist))
        if key is None:
            return
        kind, value = key
        if kind != 'evec':
            return
        if value in self._hidden_streams:
            self._hidden_streams.discard(value)
        else:
            self._hidden_streams.add(value)
        for line, k in self._track_lines:
            line.set_alpha(
                _HIDDEN_ALPHA if k in self._hidden_streams
                else _VISIBLE_ALPHA)
        self.fig.canvas.draw_idle()

    def _register_stream_legend(self, leg, num_stream_entries):
        proxies = leg.get_lines()
        for i in range(min(num_stream_entries, len(proxies))):
            proxy = proxies[i]
            proxy.set_picker(5)
            self._lmap[id(proxy)] = ('evec', i)
            proxy.set_alpha(
                _HIDDEN_ALPHA if i in self._hidden_streams
                else _VISIBLE_ALPHA)

    # ----------------------------------------------------------------
    # Left-top pane: eigenvector trajectories across subcarriers
    # ----------------------------------------------------------------

    def _draw_mac_panel(self, snap, mac_order, mac_idx):
        ax = self.ax_mac_ev
        ax.cla()

        mac_list = [m for m in mac_order if m in snap]
        if not mac_list:
            ax.set_title('CBF vector trajectories\n(waiting for data…)',
                         fontsize=9)
            return

        mac_idx = min(mac_idx, len(mac_list) - 1)
        mac     = mac_list[mac_idx]
        md      = snap[mac]
        nr      = md['nr']
        nc      = md['nc']
        scs     = sorted(md['subcarriers'].keys())

        ax.set_title(f'CBF vector trajectories — {mac}\n'
                     '(click stream entries to show/hide)', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imaginary', fontsize=8)
        ax.axhline(0, color='gray', lw=0.5)
        ax.axvline(0, color='gray', lw=0.5)
        ax.set_aspect('equal', adjustable='box')
        ax.grid(True, alpha=0.3)

        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(np.cos(th), np.sin(th), 'k--', lw=0.5, alpha=0.2)

        for k in range(nc):
            a     = (_HIDDEN_ALPHA if k in self._hidden_streams
                     else _VISIBLE_ALPHA)
            color = COLORS[k % len(COLORS)]
            for j in range(nr):
                ls = LINESTYLES[j % len(LINESTYLES)]
                re_pts = [md['subcarriers'][s]['v_real'][j, k] for s in scs]
                im_pts = [md['subcarriers'][s]['v_imag'][j, k] for s in scs]
                (traj,) = ax.plot(re_pts, im_pts,
                                  color=color, linestyle=ls,
                                  linewidth=0.9, alpha=a)
                self._track_lines.append((traj, k))
                if re_pts:
                    (m0,) = ax.plot(re_pts[0], im_pts[0], 'o',
                                    color=color, markersize=3,
                                    alpha=a * 0.7)
                    (m1,) = ax.plot(re_pts[-1], im_pts[-1], 's',
                                    color=color, markersize=3,
                                    alpha=a * 0.7)
                    self._track_lines.append((m0, k))
                    self._track_lines.append((m1, k))

        stream_handles = [
            Line2D([0], [0], color=COLORS[k % len(COLORS)],
                   linewidth=2, label=f'stream {k}')
            for k in range(nc)
        ]
        ant_handles = [
            Line2D([0], [0], color='black',
                   linestyle=LINESTYLES[j % len(LINESTYLES)],
                   linewidth=1.2, label=f'antenna {j}')
            for j in range(nr)
        ]
        if stream_handles or ant_handles:
            leg = ax.legend(handles=stream_handles + ant_handles,
                            fontsize=7, loc='upper right')
            self._register_stream_legend(leg, len(stream_handles))

    # ----------------------------------------------------------------
    # Left-bottom pane: SNR bar chart
    # ----------------------------------------------------------------

    def _draw_snr(self, snap, mac_order):
        ax = self.ax_snr
        ax.cla()
        ax.set_title('SNR per spatial stream', fontsize=9)
        ax.set_ylabel('SNR (0.25 dB / LSB)', fontsize=8)
        ax.grid(True, alpha=0.3, axis='y')

        bar_entries = [
            (mac, snap[mac]['snr'], COLORS[i % len(COLORS)])
            for i, mac in enumerate(mac_order)
            if mac in snap
        ]
        if not bar_entries:
            return

        n_mac   = len(bar_entries)
        max_str = max(len(e[1]) for e in bar_entries)
        x       = np.arange(max_str)
        width   = 0.7 / max(n_mac, 1)
        offsets = np.linspace(-(n_mac - 1) / 2,
                               (n_mac - 1) / 2,
                               n_mac) * width
        for (mac, snr, color), off in zip(bar_entries, offsets):
            ax.bar(x[:len(snr)] + off, snr,
                   width=width, color=color, alpha=0.8, label=mac)
        ax.set_xticks(x)
        ax.set_xticklabels([f'S{k}' for k in range(max_str)], fontsize=8)
        ax.legend(fontsize=7)

        all_snr = [v for _, snr_list, _ in bar_entries for v in snr_list]
        if all_snr:
            ymax = max(max(all_snr) * 1.3, 60)
            ax.set_ylim(min(0, min(all_snr)) - 5, ymax)

    # ----------------------------------------------------------------
    # Right pane: steering vectors on the complex plane (per subcarrier)
    # ----------------------------------------------------------------

    def _draw_right(self, snap, mac_order, sc):
        ax = self.ax_right
        ax.cla()
        ax.set_title(f'CBF vectors — subcarrier {sc}\n'
                     '(click stream entries to show/hide)', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imaginary', fontsize=8)
        ax.axhline(0, color='gray', lw=0.5)
        ax.axvline(0, color='gray', lw=0.5)
        ax.set_aspect('equal', adjustable='box')
        ax.grid(True, alpha=0.3)

        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(np.cos(th), np.sin(th), 'k--', lw=0.5, alpha=0.2)

        mac_legend    = []
        stream_legend = []
        max_nc        = 0

        for i, mac in enumerate(mac_order):
            if mac not in snap:
                continue
            md      = snap[mac]
            sc_data = md['subcarriers'].get(sc)
            if sc_data is None:
                continue

            mk     = MARKERS[i % len(MARKERS)]
            nc     = md['nc']
            max_nc = max(max_nc, nc)
            v_r    = sc_data['v_real']
            v_i    = sc_data['v_imag']

            for k in range(nc):
                a     = (_HIDDEN_ALPHA if k in self._hidden_streams
                         else _VISIBLE_ALPHA)
                color = COLORS[k % len(COLORS)]
                (ln,) = ax.plot(v_r[:, k], v_i[:, k],
                                color=color, linestyle='none',
                                marker=mk, markersize=5, alpha=a)
                self._track_lines.append((ln, k))

            mac_legend.append(
                Line2D([0], [0], color='gray', linestyle='none',
                       marker=mk, markersize=5, label=mac))

        for k in range(min(max_nc, len(COLORS))):
            stream_legend.append(
                Line2D([0], [0], color=COLORS[k % len(COLORS)],
                       linestyle='none', marker='o', markersize=5,
                       linewidth=1.2, label=f'stream {k}'))

        all_handles = stream_legend + mac_legend
        if all_handles:
            leg = ax.legend(handles=all_handles,
                            labels=[h.get_label() for h in all_handles],
                            fontsize=7, loc='upper right')
            for k, proxy in enumerate(leg.get_lines()[:len(stream_legend)]):
                proxy.set_picker(5)
                self._lmap[id(proxy)] = ('evec', k)
                proxy.set_alpha(
                    _HIDDEN_ALPHA if k in self._hidden_streams
                    else _VISIBLE_ALPHA)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap = {
                mac: {
                    'nr':          v['nr'],
                    'nc':          v['nc'],
                    'snr':         list(v['snr']),
                    'subcarriers': dict(v['subcarriers']),
                }
                for mac, v in self.store.cbf_data.items()
            }
            mac_order = list(self.store.cbf_order)
            sc        = self.current_sc
            mac_idx   = self.current_mac_idx

        sc_changed      = (sc      != self._prev_sc)
        mac_idx_changed = (mac_idx != self._prev_mac_idx)
        self._prev_sc      = sc
        self._prev_mac_idx = mac_idx

        if self._frame_count > 0:
            self._zoom['mac_ev'] = (
                None if mac_idx_changed
                else (self.ax_mac_ev.get_xlim(),
                      self.ax_mac_ev.get_ylim()))
            self._zoom['right'] = (
                None if sc_changed
                else (self.ax_right.get_xlim(),
                      self.ax_right.get_ylim()))

        if snap:
            all_scs = [i for v in snap.values()
                       for i in v['subcarriers']]
            if all_scs and max(all_scs) > self.sc_slider.valmax:
                self.sc_slider.valmax = max(all_scs)
                self.sc_slider.ax.set_xlim(0, max(all_scs))
            n_mac = len(mac_order)
            if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
                self.mac_slider.valmax = n_mac - 1
                self.mac_slider.ax.set_xlim(0, n_mac - 1)

        self._lmap        = {}
        self._track_lines = []

        self._draw_mac_panel(snap, mac_order, mac_idx)
        self._draw_snr(snap, mac_order)
        self._draw_right(snap, mac_order, sc)

        if self._zoom['mac_ev']:
            self.ax_mac_ev.set_xlim(self._zoom['mac_ev'][0])
            self.ax_mac_ev.set_ylim(self._zoom['mac_ev'][1])
        if self._zoom['right']:
            self.ax_right.set_xlim(self._zoom['right'][0])
            self.ax_right.set_ylim(self._zoom['right'][1])

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the CBF figure. Returns the FuncAnimation (keep alive)."""
        fig = plt.figure(figsize=(15, 9))
        self.fig = fig
        fig.suptitle('ESP32 CBF Telemetry — Live', fontsize=12)

        gs = gridspec.GridSpec(
            3, 2, figure=fig,
            height_ratios=[18, 1, 1], hspace=0.45)
        gs_left = gridspec.GridSpecFromSubplotSpec(
            2, 1, subplot_spec=gs[0, 0],
            hspace=0.55, height_ratios=[3, 1])

        self.ax_mac_ev = fig.add_subplot(gs_left[0])
        self.ax_snr    = fig.add_subplot(gs_left[1])
        self.ax_right  = fig.add_subplot(gs[0, 1])

        ax_sc_slider  = fig.add_subplot(gs[1, :])
        ax_mac_slider = fig.add_subplot(gs[2, :])

        self.sc_slider = Slider(
            ax_sc_slider, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.sc_slider.on_changed(
            lambda v: setattr(self, 'current_sc', int(v)))

        self.mac_slider = Slider(
            ax_mac_slider, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        fig.canvas.mpl_connect('pick_event', self._on_pick)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CSI Plotter
# -----------------------------------------------------------------------

class CSIPlotter(_PauseMixin):
    """
    CSI telemetry window — three panes:
      Left   : complex-plane scatter at selected subcarrier (selected MAC)
      Centre : phase waterfall     (cmap='hsv',    ±π,   selected MAC)
      Right  : amplitude waterfall (cmap='viridis', dB,  selected MAC)
    Subcarrier slider and MAC slider at the bottom.
    """

    _AMP_DB_MIN =   0.0   # display floor (dB)
    _AMP_DB_MAX =  50.0   # display ceiling (dB); int8 I/Q → max ~45 dB

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.current_sc_idx  = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self.sc_slider       = None
        self._phase_cbar     = None
        self._amp_cbar       = None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _to_db(amp):
        return 20.0 * np.log10(np.maximum(amp, 1e-9))

    @staticmethod
    def _build_matrix(history, key):
        max_len = max(len(r[key]) for r in history)
        mat = np.zeros((len(history), max_len))
        for i, row in enumerate(history):
            arr = row[key]
            mat[i, :len(arr)] = arr
        return mat

    # ----------------------------------------------------------------
    # Left pane: complex-plane scatter at selected subcarrier
    # ----------------------------------------------------------------

    def _draw_cpx(self, history, mac, sc_idx):
        ax = self.ax_cpx
        ax.cla()
        ax.set_title(
            f'CSI Complex Plane — {mac}  SC {sc_idx}', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imag', fontsize=8)
        ax.axhline(0, color='grey', lw=0.4, ls='--')
        ax.axvline(0, color='grey', lw=0.4, ls='--')

        reals, imags = [], []
        for f in history:
            amp, phase = f['amp'], f['phase']
            if sc_idx < len(amp):
                reals.append(
                    float(amp[sc_idx]) * np.cos(float(phase[sc_idx])))
                imags.append(
                    float(amp[sc_idx]) * np.sin(float(phase[sc_idx])))

        if not reals:
            return

        n      = len(reals)
        colors = np.linspace(0.0, 1.0, n)

        pts  = np.array([reals, imags]).T.reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        lc   = LineCollection(
            segs, cmap='plasma',
            norm=plt.Normalize(0.0, 1.0),
            linewidth=0.9, linestyle=':', alpha=0.65, zorder=2)
        lc.set_array(colors[:-1])
        ax.add_collection(lc)

        ax.scatter(reals, imags,
                   c=colors, cmap='plasma',
                   s=12, alpha=0.75, linewidths=0, zorder=3)
        ax.scatter([reals[-1]], [imags[-1]],
                   c='red', s=25, zorder=4, linewidths=0)
        ax.autoscale_view()

    # ----------------------------------------------------------------
    # Centre pane: phase waterfall
    # ----------------------------------------------------------------

    def _draw_phase_wf(self, history, mac):
        ax = self.ax_phase_wf
        ax.cla()
        ax.set_title(f'CSI Phase — {mac}\n(newest top)', fontsize=9)
        ax.set_xlabel('Subcarrier index', fontsize=8)
        ax.set_ylabel('Frame', fontsize=8)

        mat = self._build_matrix(history, 'phase')
        im  = ax.imshow(
            mat[::-1], aspect='auto', cmap='hsv',
            vmin=-np.pi, vmax=np.pi,
            extent=[0, mat.shape[1], 0, mat.shape[0]],
            interpolation='nearest')

        if self._phase_cbar is None:
            self._phase_cbar = self.fig.colorbar(im, cax=self.ax_phase_cb)
            self._phase_cbar.set_label('Phase (rad)', fontsize=7)
            self._phase_cbar.set_ticks(
                [-np.pi, -np.pi / 2, 0, np.pi / 2, np.pi])
            self._phase_cbar.set_ticklabels(
                ['-π', '-π/2', '0', 'π/2', 'π'], fontsize=7)
        else:
            self._phase_cbar.update_normal(im)

    # ----------------------------------------------------------------
    # Right pane: amplitude waterfall in dB
    # ----------------------------------------------------------------

    def _draw_amp_wf(self, history, mac):
        ax = self.ax_amp_wf
        ax.cla()
        ax.set_title(f'CSI Amplitude — {mac}\n(newest top)', fontsize=9)
        ax.set_xlabel('Subcarrier index', fontsize=8)
        ax.set_ylabel('Frame', fontsize=8)

        mat = self._to_db(self._build_matrix(history, 'amp'))
        im  = ax.imshow(
            mat[::-1], aspect='auto', cmap='viridis',
            vmin=self._AMP_DB_MIN, vmax=self._AMP_DB_MAX,
            extent=[0, mat.shape[1], 0, mat.shape[0]],
            interpolation='nearest')

        if self._amp_cbar is None:
            self._amp_cbar = self.fig.colorbar(im, cax=self.ax_amp_cb)
            self._amp_cbar.set_label('Amplitude (dB)', fontsize=7)
        else:
            self._amp_cbar.update_normal(im)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap_csi   = {mac: list(hist)
                          for mac, hist in self.store.csi_data.items()}
            snap_order = list(self.store.csi_order)
            mac_idx    = self.current_mac_idx
            sc_idx     = self.current_sc_idx

        n_mac = len(snap_order)
        if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        macs = [m for m in snap_order if snap_csi.get(m)]
        if macs:
            mac_idx = min(mac_idx, len(macs) - 1)
            mac     = macs[mac_idx]
            history = snap_csi[mac]

            # Update subcarrier slider range from latest frame
            n_sc = len(history[-1]['amp'])
            if (n_sc - 1) > self.sc_slider.valmax:
                self.sc_slider.valmax = n_sc - 1
                self.sc_slider.ax.set_xlim(0, n_sc - 1)
            sc_idx = min(sc_idx, n_sc - 1)

            self._draw_cpx(history, mac, sc_idx)
            self._draw_phase_wf(history, mac)
            self._draw_amp_wf(history, mac)
        else:
            for ax in (self.ax_cpx, self.ax_phase_wf, self.ax_amp_wf):
                ax.cla()
                ax.set_title('(waiting for CSI data…)', fontsize=9)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the CSI figure. Returns the FuncAnimation (keep alive)."""
        fig = plt.figure(figsize=(18, 7))
        self.fig = fig
        fig.suptitle('ESP32 CSI Telemetry — Live', fontsize=12)

        # cols: cpx scatter | phase wf | phase cb | amp wf | amp cb
        gs = gridspec.GridSpec(
            3, 5, figure=fig,
            height_ratios=[14, 0.6, 0.6],
            width_ratios=[8, 8, 0.4, 8, 0.4],
            hspace=0.6, wspace=0.35)

        self.ax_cpx      = fig.add_subplot(gs[0, 0])
        self.ax_phase_wf = fig.add_subplot(gs[0, 1])
        self.ax_phase_cb = fig.add_subplot(gs[0, 2])
        self.ax_amp_wf   = fig.add_subplot(gs[0, 3])
        self.ax_amp_cb   = fig.add_subplot(gs[0, 4])

        ax_sc_slider = fig.add_subplot(gs[1, :])
        self.sc_slider = Slider(
            ax_sc_slider, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.sc_slider.on_changed(
            lambda v: setattr(self, 'current_sc_idx', int(v)))

        ax_mac_slider = fig.add_subplot(gs[2, :])
        self.mac_slider = Slider(
            ax_mac_slider, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CBF Waterfall Plotter
# -----------------------------------------------------------------------

class CBFWaterfallPlotter(_PauseMixin):
    """
    CBF waterfall window — one row of [phase | amp] per V-matrix antenna
    pair (r, c).  Axes are built lazily on first data arrival and rebuilt
    if the antenna dimensions change.

    Each pane has subcarriers on the X axis and frame index on the Y axis
    (newest frame at top).

    Phase  : cmap='hsv',    vmin/vmax = ±π
    Amplitude : cmap='viridis', vmin/vmax = -40/0 dB
    """

    _AMP_DB_MIN = -40.0
    _AMP_DB_MAX =   0.0

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self._ax_grid        = {}  # (r,c,'phase'|'phase_cb'|'amp'|'amp_cb')
        self._phase_cbars    = {}  # (r,c) -> Colorbar
        self._amp_cbars      = {}  # (r,c) -> Colorbar
        self._built_nr       = None
        self._built_nc       = None

    # ----------------------------------------------------------------
    # Lazy axes construction
    # ----------------------------------------------------------------

    def _setup_axes(self, nr, nc):
        """Clear figure and build a row of subplots per antenna pair."""
        self.fig.clf()
        self._ax_grid     = {}
        self._phase_cbars = {}
        self._amp_cbars   = {}

        n_pairs = nr * nc
        # Rows: one per pair + slider; cols: phase | cb | amp | cb
        gs = gridspec.GridSpec(
            n_pairs + 1, 4,
            figure=self.fig,
            height_ratios=[10] * n_pairs + [0.6],
            width_ratios=[10, 0.4, 10, 0.4],
            hspace=0.4, wspace=0.3)

        for idx, (r, c) in enumerate(
                (r, c) for r in range(nr) for c in range(nc)):
            self._ax_grid[(r, c, 'phase')]    = self.fig.add_subplot(gs[idx, 0])
            self._ax_grid[(r, c, 'phase_cb')] = self.fig.add_subplot(gs[idx, 1])
            self._ax_grid[(r, c, 'amp')]      = self.fig.add_subplot(gs[idx, 2])
            self._ax_grid[(r, c, 'amp_cb')]   = self.fig.add_subplot(gs[idx, 3])

        ax_s = self.fig.add_subplot(gs[n_pairs, :])
        self.mac_slider = Slider(
            ax_s, 'MAC index', 0, 1,
            valinit=min(self.current_mac_idx, 0),
            valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self.fig.suptitle(
            'ESP32 CBF Telemetry — Waterfall per Antenna Pair', fontsize=9)
        self.fig.set_size_inches(14, 2.2 * n_pairs + 1.0)
        self._built_nr = nr
        self._built_nc = nc

    # ----------------------------------------------------------------
    # Per-pair draw
    # ----------------------------------------------------------------

    @staticmethod
    def _build_pair_matrix(history, r, c, key):
        rows = [f['pairs'].get((r, c), {}).get(key, np.array([0.0]))
                for f in history]
        max_len = max(len(a) for a in rows)
        mat = np.zeros((len(rows), max_len))
        for i, a in enumerate(rows):
            mat[i, :len(a)] = a
        return mat

    def _draw_pair(self, history, mac, r, c):
        label = f'Rx{r} Tx{c}'

        # ---- phase ----
        ax = self._ax_grid[(r, c, 'phase')]
        ax.cla()
        ax.tick_params(labelsize=5)
        ax.set_title(f'{mac}  {label} — Phase', fontsize=6)
        ax.set_xlabel('Subcarrier', fontsize=5)
        ax.set_ylabel('Frame', fontsize=5)

        mat_ph = self._build_pair_matrix(history, r, c, 'phase')
        im_ph  = ax.imshow(
            mat_ph[::-1], aspect='auto', cmap='hsv',
            vmin=-np.pi, vmax=np.pi,
            extent=[0, mat_ph.shape[1], 0, mat_ph.shape[0]],
            interpolation='nearest')

        if (r, c) not in self._phase_cbars:
            cb = self.fig.colorbar(
                im_ph, cax=self._ax_grid[(r, c, 'phase_cb')])
            cb.set_label('rad', fontsize=5)
            cb.set_ticks([-np.pi, 0, np.pi])
            cb.set_ticklabels(['-π', '0', 'π'], fontsize=5)
            self._phase_cbars[(r, c)] = cb
        else:
            self._phase_cbars[(r, c)].update_normal(im_ph)

        # ---- amplitude ----
        ax = self._ax_grid[(r, c, 'amp')]
        ax.cla()
        ax.tick_params(labelsize=5)
        ax.set_title(f'{mac}  {label} — Amplitude (dB)', fontsize=6)
        ax.set_xlabel('Subcarrier', fontsize=5)
        ax.set_ylabel('Frame', fontsize=5)

        mat_db = 20.0 * np.log10(
            np.maximum(
                self._build_pair_matrix(history, r, c, 'amp'), 1e-12))
        im_am  = ax.imshow(
            mat_db[::-1], aspect='auto', cmap='viridis',
            vmin=self._AMP_DB_MIN, vmax=self._AMP_DB_MAX,
            extent=[0, mat_db.shape[1], 0, mat_db.shape[0]],
            interpolation='nearest')

        if (r, c) not in self._amp_cbars:
            cb = self.fig.colorbar(
                im_am, cax=self._ax_grid[(r, c, 'amp_cb')])
            cb.set_label('dB', fontsize=5)
            cb.ax.tick_params(labelsize=5)
            self._amp_cbars[(r, c)] = cb
        else:
            self._amp_cbars[(r, c)].update_normal(im_am)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap_wf    = {mac: list(hist)
                          for mac, hist in self.store.cbf_wf_data.items()}
            snap_order = list(self.store.cbf_order)
            mac_idx    = self.current_mac_idx

        macs = [m for m in snap_order if snap_wf.get(m)]

        if not macs:
            self._frame_count += 1
            return

        n_mac = len(macs)
        if self.mac_slider and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        mac_idx = min(mac_idx, n_mac - 1)
        mac     = macs[mac_idx]
        history = snap_wf[mac]

        latest  = history[-1]
        nr, nc  = latest['nr'], latest['nc']
        if nr != self._built_nr or nc != self._built_nc:
            self._setup_axes(nr, nc)

        for r in range(nr):
            for c in range(nc):
                self._draw_pair(history, mac, r, c)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the CBF waterfall figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(14, 7))
        self.fig = fig
        fig.text(0.5, 0.5, '(waiting for CBF data…)',
                 ha='center', va='center', fontsize=12,
                 transform=fig.transFigure)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CBF V-matrix vector complex-plane plotter  (ratio plotters removed)
# -----------------------------------------------------------------------

class CBFVectorComplexPlotter(_PauseMixin):
    """
    One subplot per spatial-stream column of the V matrix (nc total).

    For a chosen subcarrier and MAC, each subplot k shows the nc-vector
    V[:,k] plotted on the complex plane.  Each element V[r,k] (Rx antenna
    r) accumulates over time as a trail of dots.

    Time encoding: plasma colourmap (dark = oldest, bright = newest).
    Elements distinguished by marker shape (MARKERS list).
    Newest point in each trail highlighted in red.
    Subcarrier slider + MAC slider at the bottom.
    """

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.current_sc_idx  = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self.sc_slider       = None
        self._ax_vec         = {}   # k -> Axes
        self._suptitle       = None
        self._built_nr       = None
        self._built_nc       = None
        self._hidden_r       = set()   # hidden Rx antenna indices
        self._user_zoom      = {}      # k -> (xlim, ylim) set by user
        self._in_draw        = False
        self._lmap           = {}      # id(artist) -> r
        self._prev_mac_idx   = -1

    # ----------------------------------------------------------------
    # Legend pick handler
    # ----------------------------------------------------------------

    def _on_pick(self, event):
        r = self._lmap.get(id(event.artist))
        if r is None:
            return
        if r in self._hidden_r:
            self._hidden_r.discard(r)
        else:
            self._hidden_r.add(r)
        self.fig.canvas.draw_idle()

    # ----------------------------------------------------------------
    # Zoom via toolbar: captured by lim-changed callbacks
    # ----------------------------------------------------------------

    def _on_lim_changed(self, k):
        if self._in_draw:
            return
        ax = self._ax_vec.get(k)
        if ax:
            self._user_zoom[k] = (ax.get_xlim(), ax.get_ylim())

    def _connect_lim_callbacks(self):
        for k, ax in self._ax_vec.items():
            ax.callbacks.connect(
                'xlim_changed',
                lambda _, _k=k: self._on_lim_changed(_k))
            ax.callbacks.connect(
                'ylim_changed',
                lambda _, _k=k: self._on_lim_changed(_k))

    # ----------------------------------------------------------------
    # Lazy axes construction
    # ----------------------------------------------------------------

    def _setup_axes(self, nr, nc):
        """Build grid: 1 data row of nc columns + 2 slider rows."""
        self.fig.clf()
        self._ax_vec    = {}
        self._lmap      = {}
        self._user_zoom = {}

        gs = gridspec.GridSpec(
            3, nc,
            figure=self.fig,
            height_ratios=[10, 0.5, 0.5],
            hspace=0.55, wspace=0.4)

        for k in range(nc):
            self._ax_vec[k] = self.fig.add_subplot(gs[0, k])

        ax_sc = self.fig.add_subplot(gs[1, :])
        self.sc_slider = Slider(
            ax_sc, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.sc_slider.on_changed(
            lambda v: setattr(self, 'current_sc_idx', int(v)))

        ax_mac = self.fig.add_subplot(gs[2, :])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=min(self.current_mac_idx, 0),
            valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._suptitle = self.fig.suptitle(
            'CBF V-Matrix Vectors — Complex Plane', fontsize=9)

        self.fig.canvas.mpl_connect('pick_event', self._on_pick)
        self._connect_lim_callbacks()
        self.fig.set_size_inches(max(6, 4 * nc), 5.5)
        self._built_nr = nr
        self._built_nc = nc

    # ----------------------------------------------------------------
    # Per-vector draw
    # ----------------------------------------------------------------

    def _draw_vector(self, history, k, sc_idx):
        """Plot element trails for vector k (column k of V)."""
        ax    = self._ax_vec[k]
        saved = self._user_zoom.get(k)
        self._in_draw = True
        ax.cla()
        ax.set_title(f'Vector k={k}  (stream {k})', fontsize=7)
        ax.set_xlabel('Real', fontsize=6)
        ax.set_ylabel('Imag', fontsize=6)
        ax.tick_params(labelsize=5)
        ax.axhline(0, color='grey', lw=0.4, ls='--')
        ax.axvline(0, color='grey', lw=0.4, ls='--')
        ax.set_aspect('equal', adjustable='datalim')

        # collect per-element trails: trails[r] = list of complex values
        nr = history[0]['nr']
        trails = {r: [] for r in range(nr)}
        for frame in history:
            v_mat = frame['subcarriers'].get(sc_idx)
            if v_mat is None:
                continue
            for r in range(nr):
                trails[r].append(v_mat[r, k])

        any_data = False
        for r, pts in trails.items():
            if not pts:
                continue
            hidden = r in self._hidden_r
            any_data = True
            reals  = _smooth_1d([p.real for p in pts])
            imags  = _smooth_1d([p.imag for p in pts])
            n      = len(reals)
            t_norm = np.linspace(0.0, 1.0, n)
            color  = COLORS[r % len(COLORS)]
            mk     = MARKERS[r % len(MARKERS)]
            alpha  = 0.12 if hidden else 0.55
            salpha = 0.12 if hidden else 0.8

            # dotted connecting line, coloured by time via plasma
            if n > 1:
                xy   = np.array([reals, imags]).T.reshape(-1, 1, 2)
                segs = np.concatenate([xy[:-1], xy[1:]], axis=1)
                lc   = LineCollection(
                    segs, cmap='plasma',
                    norm=plt.Normalize(0.0, 1.0),
                    linewidth=0.8, linestyle=':', alpha=alpha, zorder=2)
                lc.set_array(t_norm[:-1])
                ax.add_collection(lc)

            ax.scatter(reals, imags,
                       c=t_norm, cmap='plasma',
                       s=14, alpha=salpha, linewidths=0,
                       marker=mk, zorder=3)
            if not hidden:
                ax.scatter([reals[-1]], [imags[-1]],
                           c='red', s=30, zorder=4,
                           linewidths=0, marker=mk)

        if any_data:
            ax.autoscale_view()
            if saved:
                ax.set_xlim(saved[0])
                ax.set_ylim(saved[1])
        self._in_draw = False

        # legend: one entry per Rx antenna — click to hide/show
        handles = []
        for r in range(nr):
            a = 0.25 if r in self._hidden_r else 1.0
            h = Line2D([0], [0],
                       color=COLORS[r % len(COLORS)],
                       marker=MARKERS[r % len(MARKERS)],
                       linestyle='none', markersize=5,
                       alpha=a, label=f'Rx{r}')
            handles.append(h)
        leg = ax.legend(handles=handles, fontsize=5,
                        loc='upper right', framealpha=0.4)
        for i, proxy in enumerate(leg.get_lines()):
            proxy.set_picker(5)
            self._lmap[id(proxy)] = i

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist
                        in self.store.cbf_v_trail_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx
            sc_idx   = self.current_sc_idx

        macs = [m for m in snap_ord if snap.get(m)]
        if not macs:
            self._frame_count += 1
            return

        n_mac = len(macs)
        if self.mac_slider and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        mac_idx = min(mac_idx, n_mac - 1)
        if mac_idx != self._prev_mac_idx:
            self._user_zoom.clear()
            self._prev_mac_idx = mac_idx
        mac     = macs[mac_idx]
        history = snap[mac]

        latest = history[-1]
        nr, nc = latest['nr'], latest['nc']
        if nr != self._built_nr or nc != self._built_nc:
            self._setup_axes(nr, nc)

        # Update subcarrier slider range
        n_sc = len(latest['subcarriers'])
        if self.sc_slider and (n_sc - 1) > self.sc_slider.valmax:
            self.sc_slider.valmax = n_sc - 1
            self.sc_slider.ax.set_xlim(0, n_sc - 1)
        sc_idx = min(sc_idx, n_sc - 1)

        if self._suptitle:
            self._suptitle.set_text(
                f'CBF V-Matrix Vectors — Complex Plane'
                f'  —  {mac}  SC {sc_idx}')

        for k in range(nc):
            self._draw_vector(history, k, sc_idx)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the V-matrix vector complex-plane figure.

        Returns FuncAnimation.
        """
        fig = plt.figure(figsize=(10, 5))
        self.fig = fig
        fig.text(0.5, 0.5, '(waiting for CBF data…)',
                 ha='center', va='center', fontsize=12,
                 transform=fig.transFigure)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CBF V-matrix magnitude + phase over time plotter
# -----------------------------------------------------------------------

class CBFVMatrixTimePlotter(_PauseMixin):
    """
    One subplot-pair per element of the V matrix (nr rows × nc cols grid).

    Each cell (r, c) shows two connected line plots stacked vertically:
      - Top:    |V[r,c]| vs frame index  (magnitude over time)
      - Bottom: ∠V[r,c] vs frame index  (phase over time, radians)

    Subcarrier slider and MAC slider at the bottom.
    Grid is rebuilt lazily when nr/nc changes.
    """

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.current_sc_idx  = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self.sc_slider       = None
        # ax_mag[(r,c)] and ax_phase[(r,c)]
        self._ax_mag         = {}
        self._ax_phase       = {}
        self._suptitle       = None
        self._built_nr       = None
        self._built_nc       = None

    # ----------------------------------------------------------------
    # Lazy axes construction
    # ----------------------------------------------------------------

    def _setup_axes(self, nr, nc):
        """
        Build grid of 2*nr subplot rows × nc cols plus 2 slider rows.
        Row pairs (2r, 2r+1) → magnitude and phase for Rx antenna r.
        """
        self.fig.clf()
        self._ax_mag   = {}
        self._ax_phase = {}

        # height ratios: alternating [mag, phase] pairs + 2 slider rows
        pair_heights  = [3, 2] * nr
        gs = gridspec.GridSpec(
            2 * nr + 2, nc,
            figure=self.fig,
            height_ratios=pair_heights + [0.5, 0.5],
            hspace=0.55, wspace=0.4)

        for r in range(nr):
            for c in range(nc):
                self._ax_mag[(r, c)]   = self.fig.add_subplot(
                    gs[2 * r, c])
                self._ax_phase[(r, c)] = self.fig.add_subplot(
                    gs[2 * r + 1, c], sharex=self._ax_mag[(r, c)])

        ax_sc = self.fig.add_subplot(gs[2 * nr, :])
        self.sc_slider = Slider(
            ax_sc, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.sc_slider.on_changed(
            lambda v: setattr(self, 'current_sc_idx', int(v)))

        ax_mac = self.fig.add_subplot(gs[2 * nr + 1, :])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=min(self.current_mac_idx, 0),
            valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._suptitle = self.fig.suptitle(
            'CBF V-Matrix — Magnitude & Phase vs Time', fontsize=9)

        fig_w = max(5, 3.2 * nc)
        fig_h = max(4, 2.2 * nr * 2 + 1.2)
        self.fig.set_size_inches(fig_w, fig_h)
        self._built_nr = nr
        self._built_nc = nc

    # ----------------------------------------------------------------
    # Per-cell draw
    # ----------------------------------------------------------------

    def _draw_cell(self, history, r, c, sc_idx, nr, nc):
        ax_m = self._ax_mag[(r, c)]
        ax_p = self._ax_phase[(r, c)]
        ax_m.cla()
        ax_p.cla()

        color = COLORS[(r * nc + c) % len(COLORS)]
        label = f'Rx{r}·Tx{c}'

        # column header on top row only
        if r == 0:
            ax_m.set_title(f'Tx {c}', fontsize=7)
        # row label on leftmost column only
        if c == 0:
            ax_m.set_ylabel(f'Rx{r}\n|V|', fontsize=6)
            ax_p.set_ylabel('∠V (rad)', fontsize=6)
        else:
            ax_m.set_ylabel('|V|', fontsize=6)
            ax_p.set_ylabel('∠V', fontsize=6)

        ax_m.tick_params(labelsize=5)
        ax_p.tick_params(labelsize=5)
        ax_m.grid(True, alpha=0.25)
        ax_p.grid(True, alpha=0.25)
        ax_p.set_xlabel('Frame', fontsize=5)

        # extract time series for this (r, c) at sc_idx
        mags, phases, frame_idxs = [], [], []
        for fi, frame in enumerate(history):
            v_mat = frame['subcarriers'].get(sc_idx)
            if v_mat is None or r >= v_mat.shape[0] or c >= v_mat.shape[1]:
                continue
            val = v_mat[r, c]
            mags.append(abs(val))
            phases.append(float(np.angle(val)))
            frame_idxs.append(fi)

        if not frame_idxs:
            return

        mags       = _smooth_1d(mags)
        phases     = _smooth_phase_1d(phases)
        frame_idxs = np.linspace(
            frame_idxs[0], frame_idxs[-1], len(mags))

        ax_m.plot(frame_idxs, mags,
                  color=color, lw=1.2, marker='o',
                  markersize=2.5, label=label)
        ax_p.plot(frame_idxs, phases,
                  color=color, lw=1.2, marker='o',
                  markersize=2.5)
        ax_p.set_ylim(-np.pi - 0.2, np.pi + 0.2)
        ax_p.set_yticks([-np.pi, 0, np.pi])
        ax_p.set_yticklabels(['-π', '0', 'π'], fontsize=5)

        # mark newest point
        ax_m.plot(frame_idxs[-1], mags[-1],
                  'o', color='red', ms=4, zorder=5)
        ax_p.plot(frame_idxs[-1], phases[-1],
                  'o', color='red', ms=4, zorder=5)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist
                        in self.store.cbf_v_trail_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx
            sc_idx   = self.current_sc_idx

        macs = [m for m in snap_ord if snap.get(m)]
        if not macs:
            self._frame_count += 1
            return

        n_mac = len(macs)
        if self.mac_slider and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        mac_idx = min(mac_idx, n_mac - 1)
        mac     = macs[mac_idx]
        history = snap[mac]

        latest = history[-1]
        nr, nc = latest['nr'], latest['nc']
        if nr != self._built_nr or nc != self._built_nc:
            self._setup_axes(nr, nc)

        n_sc = len(latest['subcarriers'])
        if self.sc_slider and (n_sc - 1) > self.sc_slider.valmax:
            self.sc_slider.valmax = n_sc - 1
            self.sc_slider.ax.set_xlim(0, n_sc - 1)
        sc_idx = min(sc_idx, n_sc - 1)

        if self._suptitle:
            self._suptitle.set_text(
                f'CBF V-Matrix — Magnitude & Phase vs Time'
                f'  —  {mac}  SC {sc_idx}')

        for r in range(nr):
            for c in range(nc):
                self._draw_cell(history, r, c, sc_idx, nr, nc)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the V-matrix time-series figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(10, 6))
        self.fig = fig
        fig.text(0.5, 0.5, '(waiting for CBF data…)',
                 ha='center', va='center', fontsize=12,
                 transform=fig.transFigure)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CBF Gram matrix plotter
# -----------------------------------------------------------------------

_GRAM_AVG_WINDOW = 100   # number of frames to average


class CBFGramMatrixPlotter(_PauseMixin):
    """
    Two-subplot window showing the spatial covariance (Gram) matrix
    M = V · V^H for the selected subcarrier and MAC.

    Left : magnitude of the most-recent M
    Right: magnitude of the element-wise average of M over the last
           _GRAM_AVG_WINDOW frames

    M is nr × nr.  Each entry |M[i,j]| encodes the spatial correlation
    between RX antennas i and j.

    Sliders: subcarrier, MAC index.
    """

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.current_sc_idx  = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self.sc_slider       = None
        self._ax_new         = None
        self._ax_avg         = None
        self._cb_new         = None
        self._cb_avg         = None
        self._suptitle       = None

    # ----------------------------------------------------------------
    # Gram matrix helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _gram(v):
        """Compute V · V^H → (nr, nr) complex matrix."""
        return v @ v.conj().T

    def _build_grams(self, history, sc_idx):
        """
        Return list of (nr, nr) Gram matrices, one per history frame
        that contains sc_idx.
        """
        grams = []
        for frame in history:
            v = frame['subcarriers'].get(sc_idx)
            if v is not None:
                grams.append(self._gram(v))
        return grams

    # ----------------------------------------------------------------
    # Draw
    # ----------------------------------------------------------------

    def _draw(self, grams, mac, sc_idx):
        nr = grams[0].shape[0]

        # Newest matrix
        newest_mag = np.abs(grams[-1])

        # Average magnitude over last _GRAM_AVG_WINDOW frames
        window = grams[-_GRAM_AVG_WINDOW:]
        avg_mag = np.mean([np.abs(g) for g in window], axis=0)

        vmax = max(newest_mag.max(), avg_mag.max(), 1e-9)
        ticks = list(range(nr))

        for ax, mat, title, cb_attr in [
            (self._ax_new, newest_mag,
             f'Gram |M| — newest  ({mac}  SC {sc_idx})',
             '_cb_new'),
            (self._ax_avg, avg_mag,
             f'Gram |M| — avg last {min(len(grams), _GRAM_AVG_WINDOW)}',
             '_cb_avg'),
        ]:
            ax.cla()
            im = ax.imshow(
                mat, aspect='equal',
                cmap='viridis', vmin=0.0, vmax=vmax,
                interpolation='nearest', origin='upper')

            ax.set_title(title, fontsize=8)
            ax.set_xlabel('RX antenna j', fontsize=7)
            ax.set_ylabel('RX antenna i', fontsize=7)
            ax.set_xticks(ticks)
            ax.set_yticks(ticks)
            ax.set_xticklabels([f'Rx{j}' for j in ticks], fontsize=6)
            ax.set_yticklabels([f'Rx{i}' for i in ticks], fontsize=6)
            ax.tick_params(length=2)

            # annotate each cell with its value
            for i in range(nr):
                for j in range(nr):
                    ax.text(j, i, f'{mat[i, j]:.2f}',
                            ha='center', va='center',
                            fontsize=max(4, 7 - nr),
                            color='white' if mat[i, j] < vmax * 0.6
                            else 'black')

            cb = getattr(self, cb_attr)
            if cb is None:
                cb = self.fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                cb.set_label('|M[i,j]|', fontsize=7)
                cb.ax.tick_params(labelsize=6)
                setattr(self, cb_attr, cb)
            else:
                cb.update_normal(im)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist
                        in self.store.cbf_v_trail_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx
            sc_idx   = self.current_sc_idx

        macs = [m for m in snap_ord if snap.get(m)]
        if not macs:
            self._frame_count += 1
            return

        n_mac = len(macs)
        if self.mac_slider and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        mac_idx = min(mac_idx, n_mac - 1)
        mac     = macs[mac_idx]
        history = snap[mac]

        latest = history[-1]
        n_sc   = len(latest['subcarriers'])
        if self.sc_slider and (n_sc - 1) > self.sc_slider.valmax:
            self.sc_slider.valmax = n_sc - 1
            self.sc_slider.ax.set_xlim(0, n_sc - 1)
        sc_idx = min(sc_idx, n_sc - 1)

        grams = self._build_grams(history, sc_idx)
        if not grams:
            self._frame_count += 1
            return

        self._draw(grams, mac, sc_idx)
        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the Gram matrix figure. Returns FuncAnimation."""
        fig, axes = plt.subplots(
            1, 2, figsize=(10, 5),
            gridspec_kw={'wspace': 0.45})
        self.fig    = fig
        self._ax_new, self._ax_avg = axes

        fig.suptitle('CBF Gram Matrix  M = V·Vᴴ', fontsize=10)

        # sliders below the plots
        fig.subplots_adjust(bottom=0.20)

        ax_sc = fig.add_axes([0.10, 0.10, 0.80, 0.03])
        self.sc_slider = Slider(
            ax_sc, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.sc_slider.on_changed(
            lambda v: setattr(self, 'current_sc_idx', int(v)))
        self.sc_slider.label.set_fontsize(7)

        ax_mac = fig.add_axes([0.10, 0.05, 0.80, 0.03])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))
        self.mac_slider.label.set_fontsize(7)

        fig.text(0.5, 0.01,
                 f'(waiting for CBF data…)',
                 ha='center', fontsize=9)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CBF per-spatial-stream waterfall plotter
# -----------------------------------------------------------------------

class CBFStreamWaterfallPlotter(_PauseMixin):
    """
    Waterfall window split by both spatial stream and Rx antenna.

    One row per (r, k) pair — i.e. nr * nc rows total — showing
    |V[r,k]| and ∠V[r,k] directly for that matrix element.

    Layout: (nr*nc) rows × 4 cols  [amp | amp_cb | phase | phase_cb]
    MAC slider at bottom. Grid rebuilds lazily when nr or nc changes.
    """

    _AMP_DB_MIN = -40.0
    _AMP_DB_MAX =   0.0

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self._frame_count    = 0
        self.fig             = None
        self.mac_slider      = None
        self._ax_grid        = {}   # (r, k, 'amp'|'amp_cb'|'phase'|'phase_cb')
        self._amp_cbars      = {}   # (r, k) -> Colorbar
        self._phase_cbars    = {}   # (r, k) -> Colorbar
        self._built_nr       = None
        self._built_nc       = None
        self._suptitle       = None

    # ----------------------------------------------------------------
    # Lazy axes construction
    # ----------------------------------------------------------------

    def _setup_axes(self, nr, nc):
        self.fig.clf()
        self._ax_grid     = {}
        self._amp_cbars   = {}
        self._phase_cbars = {}

        n_rows = nr * nc
        gs = gridspec.GridSpec(
            n_rows + 1, 4,
            figure=self.fig,
            height_ratios=[10] * n_rows + [0.6],
            width_ratios=[10, 0.4, 10, 0.4],
            hspace=0.4, wspace=0.3)

        for idx, (r, k) in enumerate(
                (r, k) for k in range(nc) for r in range(nr)):
            self._ax_grid[(r, k, 'amp')]      = self.fig.add_subplot(
                gs[idx, 0])
            self._ax_grid[(r, k, 'amp_cb')]   = self.fig.add_subplot(
                gs[idx, 1])
            self._ax_grid[(r, k, 'phase')]    = self.fig.add_subplot(
                gs[idx, 2])
            self._ax_grid[(r, k, 'phase_cb')] = self.fig.add_subplot(
                gs[idx, 3])

        ax_s = self.fig.add_subplot(gs[n_rows, :])
        self.mac_slider = Slider(
            ax_s, 'MAC index', 0, 1,
            valinit=min(self.current_mac_idx, 0),
            valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._suptitle = self.fig.suptitle(
            'CBF V-Matrix Element Waterfall', fontsize=9)
        self.fig.set_size_inches(14, 2.2 * n_rows + 1.0)
        self._built_nr = nr
        self._built_nc = nc

    # ----------------------------------------------------------------
    # Matrix builder
    # ----------------------------------------------------------------

    def _build_element_matrices(self, history, r, k):
        """
        Return (amp_db, phase_mat) each shape (n_frames, n_sc).
        amp_db   : 20*log10(|V[r,k]|) per (frame, subcarrier)
        phase_mat: ∠V[r,k]            per (frame, subcarrier)
        """
        n_frames = len(history)
        n_sc = max(
            max(frame['subcarriers'].keys()) + 1
            for frame in history if frame['subcarriers'])

        amp_mat   = np.zeros((n_frames, n_sc))
        phase_mat = np.zeros((n_frames, n_sc))

        for fi, frame in enumerate(history):
            for sc_idx, v_mat in frame['subcarriers'].items():
                if r < v_mat.shape[0] and k < v_mat.shape[1]:
                    val = v_mat[r, k]
                    amp_mat[fi, sc_idx]   = float(abs(val))
                    phase_mat[fi, sc_idx] = float(np.angle(val))

        amp_db = 20.0 * np.log10(
            np.maximum(_smooth_2d(amp_mat), 1e-12))
        return amp_db, _smooth_phase_2d(phase_mat)

    # ----------------------------------------------------------------
    # Per-element draw
    # ----------------------------------------------------------------

    def _draw_element(self, history, mac, r, k):
        amp_db, phase_mat = self._build_element_matrices(history, r, k)
        label = f'Rx{r} · Stream{k}'

        # ---- amplitude ----
        ax = self._ax_grid[(r, k, 'amp')]
        ax.cla()
        ax.tick_params(labelsize=5)
        ax.set_title(f'{label} — |V[{r},{k}]| (dB)', fontsize=6)
        ax.set_xlabel('Subcarrier', fontsize=5)
        ax.set_ylabel('Frame', fontsize=5)

        im_am = ax.imshow(
            amp_db[::-1], aspect='auto', cmap='viridis',
            vmin=self._AMP_DB_MIN, vmax=self._AMP_DB_MAX,
            extent=[0, amp_db.shape[1], 0, amp_db.shape[0]],
            interpolation='nearest')

        key = (r, k)
        if key not in self._amp_cbars:
            cb = self.fig.colorbar(
                im_am, cax=self._ax_grid[(r, k, 'amp_cb')])
            cb.set_label('dB', fontsize=5)
            cb.ax.tick_params(labelsize=5)
            self._amp_cbars[key] = cb
        else:
            self._amp_cbars[key].update_normal(im_am)

        # ---- phase ----
        ax = self._ax_grid[(r, k, 'phase')]
        ax.cla()
        ax.tick_params(labelsize=5)
        ax.set_title(f'{label} — ∠V[{r},{k}]', fontsize=6)
        ax.set_xlabel('Subcarrier', fontsize=5)
        ax.set_ylabel('Frame', fontsize=5)

        im_ph = ax.imshow(
            phase_mat[::-1], aspect='auto', cmap='hsv',
            vmin=-np.pi, vmax=np.pi,
            extent=[0, phase_mat.shape[1], 0, phase_mat.shape[0]],
            interpolation='nearest')

        if key not in self._phase_cbars:
            cb = self.fig.colorbar(
                im_ph, cax=self._ax_grid[(r, k, 'phase_cb')])
            cb.set_label('rad', fontsize=5)
            cb.set_ticks([-np.pi, 0, np.pi])
            cb.set_ticklabels(['-π', '0', 'π'], fontsize=5)
            self._phase_cbars[key] = cb
        else:
            self._phase_cbars[key].update_normal(im_ph)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist
                        in self.store.cbf_v_trail_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx

        macs = [m for m in snap_ord if snap.get(m)]
        if not macs:
            self._frame_count += 1
            return

        n_mac = len(macs)
        if self.mac_slider and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        mac_idx = min(mac_idx, n_mac - 1)
        mac     = macs[mac_idx]
        history = snap[mac]

        latest = history[-1]
        nr, nc = latest['nr'], latest['nc']
        if nr != self._built_nr or nc != self._built_nc:
            self._setup_axes(nr, nc)

        if self._suptitle:
            self._suptitle.set_text(
                f'CBF V-Matrix Element Waterfall — {mac}')

        for k in range(nc):
            for r in range(nr):
                self._draw_element(history, mac, r, k)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the element waterfall figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(14, 7))
        self.fig = fig
        fig.text(0.5, 0.5, '(waiting for CBF data…)',
                 ha='center', va='center', fontsize=12,
                 transform=fig.transFigure)

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CSI channel impulse response (IFFT) plotter
# -----------------------------------------------------------------------

class CSIIFFTPlotter(_PauseMixin):
    """
    CSI IFFT waterfall — channel impulse response (CIR) derived from CSI.

    Reconstructs the complex channel H[k] = amp[k]*exp(j*phase[k]) per
    frame, then computes CIR = IFFT(H).

    Left   : CIR complex-plane scatter at selected delay bin (trail)
    Centre : CIR phase waterfall (cmap='hsv', ±π)
    Right  : CIR amplitude waterfall (cmap='viridis', dB rel. per-frame peak)

    Delay-bin slider and MAC slider at the bottom.
    """

    _AMP_DB_MIN = -40.0
    _AMP_DB_MAX =   0.0   # relative dB, normalised to per-frame peak

    def __init__(self, store):
        self.store             = store
        self.current_mac_idx   = 0
        self.current_delay_idx = 0
        self._frame_count      = 0
        self.fig               = None
        self.mac_slider        = None
        self.delay_slider      = None
        self._phase_cbar       = None
        self._amp_cbar         = None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _to_db(amp):
        return 20.0 * np.log10(np.maximum(amp, 1e-9))

    @staticmethod
    def _compute_cirs(history):
        """Return list of complex CIR arrays, one per history frame."""
        cirs = []
        for f in history:
            H = f['amp'] * np.exp(1j * f['phase'])
            cirs.append(np.fft.ifft(H))
        return cirs

    @staticmethod
    def _build_cir_matrices(cirs):
        """Stack CIR magnitudes and phases into (n_frames, n_delay) matrices.

        Magnitude is normalised to the global peak across all frames so the
        waterfall colour scale is relative (0 dB = strongest tap seen).
        """
        max_len = max(len(c) for c in cirs)
        mag_mat = np.zeros((len(cirs), max_len))
        ph_mat  = np.zeros((len(cirs), max_len))
        for i, cir in enumerate(cirs):
            n = len(cir)
            mag_mat[i, :n] = np.abs(cir)
            ph_mat[i,  :n] = np.angle(cir)
        peak = mag_mat.max()
        if peak > 1e-9:
            mag_mat /= peak
        return mag_mat, ph_mat

    # ----------------------------------------------------------------
    # Left pane: complex-plane trail at selected delay bin
    # ----------------------------------------------------------------

    def _draw_cpx(self, cirs, mac, delay_idx):
        ax = self.ax_cpx
        ax.cla()
        ax.set_title(
            f'CIR Complex Plane — {mac}  delay {delay_idx}', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imag', fontsize=8)
        ax.axhline(0, color='grey', lw=0.4, ls='--')
        ax.axvline(0, color='grey', lw=0.4, ls='--')

        reals, imags = [], []
        for cir in cirs:
            if delay_idx < len(cir):
                reals.append(float(cir[delay_idx].real))
                imags.append(float(cir[delay_idx].imag))

        if not reals:
            return

        n      = len(reals)
        colors = np.linspace(0.0, 1.0, n)

        pts  = np.array([reals, imags]).T.reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        lc   = LineCollection(
            segs, cmap='plasma',
            norm=plt.Normalize(0.0, 1.0),
            linewidth=0.9, linestyle=':', alpha=0.65, zorder=2)
        lc.set_array(colors[:-1])
        ax.add_collection(lc)

        ax.scatter(reals, imags,
                   c=colors, cmap='plasma',
                   s=12, alpha=0.75, linewidths=0, zorder=3)
        ax.scatter([reals[-1]], [imags[-1]],
                   c='red', s=25, zorder=4, linewidths=0)
        ax.autoscale_view()

    # ----------------------------------------------------------------
    # Centre pane: CIR phase waterfall
    # ----------------------------------------------------------------

    def _draw_phase_wf(self, ph_mat, mac):
        ax = self.ax_phase_wf
        ax.cla()
        ax.set_title(f'CIR Phase — {mac}\n(newest top)', fontsize=9)
        ax.set_xlabel('Delay bin', fontsize=8)
        ax.set_ylabel('Frame', fontsize=8)

        im = ax.imshow(
            ph_mat[::-1], aspect='auto', cmap='hsv',
            vmin=-np.pi, vmax=np.pi,
            extent=[0, ph_mat.shape[1], 0, ph_mat.shape[0]],
            interpolation='nearest')

        if self._phase_cbar is None:
            self._phase_cbar = self.fig.colorbar(im, cax=self.ax_phase_cb)
            self._phase_cbar.set_label('Phase (rad)', fontsize=7)
            self._phase_cbar.set_ticks(
                [-np.pi, -np.pi / 2, 0, np.pi / 2, np.pi])
            self._phase_cbar.set_ticklabels(
                ['-π', '-π/2', '0', 'π/2', 'π'], fontsize=7)
        else:
            self._phase_cbar.update_normal(im)

    # ----------------------------------------------------------------
    # Right pane: CIR amplitude waterfall (dB, normalised)
    # ----------------------------------------------------------------

    def _draw_amp_wf(self, mag_mat, mac):
        ax = self.ax_amp_wf
        ax.cla()
        ax.set_title(
            f'CIR Amplitude — {mac}\n(newest top, dB rel. peak)',
            fontsize=9)
        ax.set_xlabel('Delay bin', fontsize=8)
        ax.set_ylabel('Frame', fontsize=8)

        db_mat = self._to_db(mag_mat)
        im = ax.imshow(
            db_mat[::-1], aspect='auto', cmap='viridis',
            vmin=self._AMP_DB_MIN, vmax=self._AMP_DB_MAX,
            extent=[0, db_mat.shape[1], 0, db_mat.shape[0]],
            interpolation='nearest')

        if self._amp_cbar is None:
            self._amp_cbar = self.fig.colorbar(im, cax=self.ax_amp_cb)
            self._amp_cbar.set_label('Amplitude (dB rel. peak)', fontsize=7)
        else:
            self._amp_cbar.update_normal(im)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap_csi   = {mac: list(hist)
                          for mac, hist in self.store.csi_data.items()}
            snap_order = list(self.store.csi_order)
            mac_idx    = self.current_mac_idx
            delay_idx  = self.current_delay_idx

        n_mac = len(snap_order)
        if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        macs = [m for m in snap_order if snap_csi.get(m)]
        if macs:
            mac_idx = min(mac_idx, len(macs) - 1)
            mac     = macs[mac_idx]
            history = snap_csi[mac]

            cirs              = self._compute_cirs(history)
            mag_mat, ph_mat   = self._build_cir_matrices(cirs)

            n_delay = mag_mat.shape[1]
            if (n_delay - 1) > self.delay_slider.valmax:
                self.delay_slider.valmax = n_delay - 1
                self.delay_slider.ax.set_xlim(0, n_delay - 1)
            delay_idx = min(delay_idx, n_delay - 1)

            self._draw_cpx(cirs, mac, delay_idx)
            self._draw_phase_wf(ph_mat, mac)
            self._draw_amp_wf(mag_mat, mac)
        else:
            for ax in (self.ax_cpx, self.ax_phase_wf, self.ax_amp_wf):
                ax.cla()
                ax.set_title('(waiting for CSI data…)', fontsize=9)

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create the CSI IFFT waterfall figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(18, 7))
        self.fig = fig
        fig.suptitle(
            'ESP32 CSI — Channel Impulse Response (IFFT)', fontsize=12)

        # cols: cpx scatter | phase wf | phase cb | amp wf | amp cb
        gs = gridspec.GridSpec(
            3, 5, figure=fig,
            height_ratios=[14, 0.6, 0.6],
            width_ratios=[8, 8, 0.4, 8, 0.4],
            hspace=0.6, wspace=0.35)

        self.ax_cpx      = fig.add_subplot(gs[0, 0])
        self.ax_phase_wf = fig.add_subplot(gs[0, 1])
        self.ax_phase_cb = fig.add_subplot(gs[0, 2])
        self.ax_amp_wf   = fig.add_subplot(gs[0, 3])
        self.ax_amp_cb   = fig.add_subplot(gs[0, 4])

        ax_delay_slider = fig.add_subplot(gs[1, :])
        self.delay_slider = Slider(
            ax_delay_slider, 'Delay bin', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self.delay_slider.on_changed(
            lambda v: setattr(self, 'current_delay_idx', int(v)))

        ax_mac_slider = fig.add_subplot(gs[2, :])
        self.mac_slider = Slider(
            ax_mac_slider, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# RARE-L complex-plane root plotter
# -----------------------------------------------------------------------

class RARELComplexPlotter(_PauseMixin):
    """
    Complex-plane trail of RARE-L roots and MUSIC spectrum per MAC.

    ULA mode (ARRAY_SHAPE=None):
        Left  : azimuth roots in complex plane (unit-circle ref.)
        Right : azimuth MUSIC pseudospectrum (dB)

    UPA mode (ARRAY_SHAPE=(M,N)):
        Top-left     : azimuth roots   Top-right     : azimuth MUSIC spectrum
        Bottom-left  : elevation roots Bottom-right  : elevation MUSIC spectrum

    The unit circle is drawn as reference. The plasma trail runs
    oldest→newest; red dots mark the most recent frame's roots.

    Sliders: MAC index, n_sources.
    """

    def __init__(self, store):
        self.store             = store
        self.current_mac_idx   = 0
        self.current_n_sources = 1
        self.fig               = None
        self.mac_slider        = None
        self.nsrc_slider       = None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _draw_root_pane(ax, history, axis_key, label):
        """Draw root trail in complex plane for one axis ('az' or 'el')."""
        ax.cla()
        ax.set_title(label, fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imag', fontsize=8)
        ax.set_aspect('equal')
        ax.axhline(0, color='grey', lw=0.4, ls='--')
        ax.axvline(0, color='grey', lw=0.4, ls='--')

        circ = np.linspace(0, 2 * np.pi, 300)
        ax.plot(np.cos(circ), np.sin(circ),
                color='steelblue', lw=0.8, ls='--', alpha=0.5, zorder=1)

        all_r, all_i, all_t = [], [], []
        for t, frame in enumerate(history):
            ax_data = frame.get(axis_key)
            if ax_data is None:
                continue
            for z in ax_data['roots']:
                all_r.append(float(z.real))
                all_i.append(float(z.imag))
                all_t.append(t)

        if all_r:
            colors = np.array(all_t, dtype=float)
            colors = (colors - colors.min()) / max(colors.max()
                                                   - colors.min(), 1e-9)
            ax.scatter(all_r, all_i, c=colors, cmap='plasma',
                       s=8, alpha=0.5, linewidths=0, zorder=2)

        if history:
            latest = history[-1].get(axis_key)
            if latest is not None:
                for doa, z in zip(latest['doa'], latest['roots']):
                    ax.scatter([z.real], [z.imag],
                               c='red', s=28, zorder=4, linewidths=0)
                    if not np.isnan(doa):
                        ax.annotate(
                            f'{doa:.1f}°',
                            xy=(z.real, z.imag),
                            xytext=(5, 5), textcoords='offset points',
                            fontsize=7, color='red')
            else:
                ax.text(0.5, 0.5, 'ULA mode\n(no elevation)',
                        ha='center', va='center', fontsize=9,
                        transform=ax.transAxes, color='grey')
        ax.autoscale_view()

    @staticmethod
    def _draw_spectrum_pane(ax, history, axis_key, label):
        """Draw MUSIC spectrum for one axis ('az' or 'el')."""
        ax.cla()
        ax.set_title(label, fontsize=9)
        ax.set_xlabel('Angle (deg)', fontsize=8)
        ax.set_ylabel('dB rel. peak', fontsize=8)

        if not history:
            return
        frame    = history[-1]
        ax_data  = frame.get(axis_key)
        if ax_data is None:
            ax.text(0.5, 0.5, 'ULA mode\n(no elevation)',
                    ha='center', va='center', fontsize=9,
                    transform=ax.transAxes, color='grey')
            return

        angles  = rare_est._SCAN_ANGLES_DEG
        spec_db = ax_data['spectrum_db']
        ax.plot(angles, spec_db, color='steelblue', lw=1.0)
        ax.set_xlim(-90, 90)
        ax.set_ylim(-40, 2)
        ax.axhline(-3, color='grey', lw=0.5, ls=':', alpha=0.6)
        ax.grid(True, lw=0.3, alpha=0.4)

        for doa in ax_data['doa']:
            if not np.isnan(doa):
                ax.axvline(doa, color='red', lw=1.0, ls='--', alpha=0.7)
                ax.text(doa + 1, -38, f'{doa:.1f}°', fontsize=7, color='red')

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist in self.store.rare_l_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx

        n_mac = len(snap_ord)
        if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        macs = [m for m in snap_ord if snap.get(m)]
        if macs:
            mac_idx = min(mac_idx, len(macs) - 1)
            mac     = macs[mac_idx]
            history = snap[mac]
            self._draw_root_pane(
                self.ax_az_cpx, history, 'az',
                f'Az Roots — {mac}')
            self._draw_root_pane(
                self.ax_el_cpx, history, 'el',
                f'El Roots — {mac}')
            self._draw_spectrum_pane(
                self.ax_az_spec, history, 'az',
                f'Az MUSIC — {mac}')
            self._draw_spectrum_pane(
                self.ax_el_spec, history, 'el',
                f'El MUSIC — {mac}')
        else:
            for ax in (self.ax_az_cpx, self.ax_el_cpx,
                       self.ax_az_spec, self.ax_el_spec):
                ax.cla()
                ax.set_title('(waiting for CBF data…)', fontsize=9)

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create RARE-L complex-plane figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(18, 8))
        self.fig = fig
        fig.suptitle('RARE-L DoA — Complex Plane & MUSIC Spectrum', fontsize=12)

        # 2×2 plot grid + two slider rows
        gs = gridspec.GridSpec(
            4, 2, figure=fig,
            height_ratios=[7, 7, 0.55, 0.55],
            hspace=0.55, wspace=0.4)

        self.ax_az_cpx  = fig.add_subplot(gs[0, 0])
        self.ax_az_spec = fig.add_subplot(gs[0, 1])
        self.ax_el_cpx  = fig.add_subplot(gs[1, 0])
        self.ax_el_spec = fig.add_subplot(gs[1, 1])

        ax_mac = fig.add_subplot(gs[2, :])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        ax_nsrc = fig.add_subplot(gs[3, :])
        self.nsrc_slider = Slider(
            ax_nsrc, 'n_sources', 1, 4,
            valinit=1, valstep=1, color='steelblue')
        self.nsrc_slider.on_changed(
            lambda v: setattr(self, 'current_n_sources', int(v)))

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# RARE-L MUSIC pseudospectrum waterfall plotter
# -----------------------------------------------------------------------

class RARELWaterfallPlotter(_PauseMixin):
    """
    Rolling MUSIC pseudospectrum waterfall with eigenvalue display.

    ULA mode (ARRAY_SHAPE=None):
        Left  : azimuth MUSIC waterfall (newest row at top, inferno)
        Right : eigenvalue bar chart for the latest frame

    UPA mode (ARRAY_SHAPE=(M,N)):
        Top-left    : azimuth MUSIC waterfall
        Bottom-left : elevation MUSIC waterfall
        Right       : eigenvalue bar chart (shared, one column)

    Cyan dots overlay the RARE-L DoA estimates on each waterfall row.

    Slider: MAC index.
    """

    _DB_MIN = -30.0
    _DB_MAX =   0.0

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.fig             = None
        self.mac_slider      = None
        self._az_cbar        = None
        self._el_cbar        = None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _draw_wf_pane(self, ax, cbar_ax, history, axis_key,
                      label, cbar_ref):
        """Draw one MUSIC waterfall pane. Returns updated colorbar ref."""
        ax.cla()
        ax.set_title(label, fontsize=9)
        ax.set_xlabel('Angle (deg)', fontsize=8)
        ax.set_ylabel('Frame', fontsize=8)

        valid = [f for f in history if f.get(axis_key) is not None]
        if not valid:
            ax.text(0.5, 0.5, 'ULA mode\n(no elevation)',
                    ha='center', va='center', fontsize=9,
                    transform=ax.transAxes, color='grey')
            return cbar_ref

        mat = np.array([f[axis_key]['spectrum_db'] for f in valid])
        im  = ax.imshow(
            mat[::-1], aspect='auto', cmap='inferno',
            vmin=self._DB_MIN, vmax=self._DB_MAX,
            extent=[-90, 90, 0, len(valid)],
            interpolation='nearest')

        if cbar_ref is None:
            cb = self.fig.colorbar(im, cax=cbar_ax)
            cb.set_label('dB rel. peak', fontsize=7)
            cbar_ref = cb
        else:
            cbar_ref.update_normal(im)

        for row_idx, frame in enumerate(valid):
            y = len(valid) - 1 - row_idx
            for doa in frame[axis_key]['doa']:
                if not np.isnan(doa):
                    ax.scatter([doa], [y + 0.5],
                               c='cyan', s=14, linewidths=0,
                               alpha=0.75, zorder=3)

        ax.set_xlim(-90, 90)
        ax.set_ylim(0, len(valid))
        return cbar_ref

    def _draw_eigenvalues(self, history, mac):
        ax = self.ax_eig
        ax.cla()
        ax.set_title(f'Eigenvalues — {mac}', fontsize=9)
        ax.set_xlabel('Index', fontsize=8)
        ax.set_ylabel('Power (linear)', fontsize=8)

        if not history:
            return
        eigs = history[-1].get('eigenvalues', np.array([]))
        if len(eigs) == 0:
            return

        idxs = np.arange(1, len(eigs) + 1)
        ax.bar(idxs, np.real(eigs), color='steelblue', alpha=0.75)
        ax.set_xticks(idxs)
        ax.grid(True, axis='y', lw=0.3, alpha=0.4)

        mid = len(eigs) // 2
        if mid > 0:
            noise_floor = np.median(np.real(eigs[mid:]))
            ax.axhline(noise_floor, color='red', lw=0.8, ls='--',
                       label=f'noise ~ {noise_floor:.2e}')
            ax.legend(fontsize=7)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist in self.store.rare_l_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx

        n_mac = len(snap_ord)
        if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        macs = [m for m in snap_ord if snap.get(m)]
        if macs:
            mac_idx = min(mac_idx, len(macs) - 1)
            mac     = macs[mac_idx]
            history = snap[mac]
            self._az_cbar = self._draw_wf_pane(
                self.ax_az, self.ax_az_cb, history, 'az',
                f'Az MUSIC Waterfall — {mac}', self._az_cbar)
            self._el_cbar = self._draw_wf_pane(
                self.ax_el, self.ax_el_cb, history, 'el',
                f'El MUSIC Waterfall — {mac}', self._el_cbar)
            self._draw_eigenvalues(history, mac)
        else:
            for ax in (self.ax_az, self.ax_el, self.ax_eig):
                ax.cla()
                ax.set_title('(waiting for CBF data…)', fontsize=9)

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create RARE-L waterfall figure. Returns FuncAnimation."""
        fig = plt.figure(figsize=(18, 9))
        self.fig = fig
        fig.suptitle(
            'RARE-L DoA — MUSIC Pseudospectrum Waterfall', fontsize=12)

        # rows: az waterfall | el waterfall | slider
        # cols: waterfall | colorbar | eigenvalues
        gs = gridspec.GridSpec(
            3, 3, figure=fig,
            height_ratios=[7, 7, 0.6],
            width_ratios=[10, 0.4, 4],
            hspace=0.55, wspace=0.4)

        self.ax_az    = fig.add_subplot(gs[0, 0])
        self.ax_az_cb = fig.add_subplot(gs[0, 1])
        self.ax_el    = fig.add_subplot(gs[1, 0])
        self.ax_el_cb = fig.add_subplot(gs[1, 1])
        self.ax_eig   = fig.add_subplot(gs[:2, 2])

        ax_mac = fig.add_subplot(gs[2, :])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# RARE-L azimuth / elevation angle-over-time plotter
# -----------------------------------------------------------------------

class RARELAnglePlotter(_PauseMixin):
    """
    Azimuth and elevation DoA angles plotted against frame index.

    Top    : Azimuth estimates (degrees) for all sources over time.
    Bottom : Elevation estimates (degrees) for all sources over time.
             Shows 'ULA mode (no elevation)' when ARRAY_SHAPE is None.

    Each source is drawn as a coloured dot trail (plasma) connected by
    thin lines. The most recent estimate is a larger red dot. Multiple
    sources per frame are each plotted separately.

    Slider: MAC index.
    """

    _Y_PAD = 5.0   # degrees of padding beyond ±90 on angle axes

    def __init__(self, store):
        self.store           = store
        self.current_mac_idx = 0
        self.fig             = None
        self.mac_slider      = None

    # ----------------------------------------------------------------
    # Helper — draw one angle-vs-time pane
    # ----------------------------------------------------------------

    @staticmethod
    def _draw_angle_pane(ax, history, axis_key, label, color):
        ax.cla()
        ax.set_title(label, fontsize=9)
        ax.set_xlabel('Frame index', fontsize=8)
        ax.set_ylabel('Angle (deg)', fontsize=8)
        ax.set_ylim(-95, 95)
        ax.axhline(0, color='grey', lw=0.4, ls='--', alpha=0.5)
        ax.grid(True, lw=0.3, alpha=0.4)

        # Check first valid frame to see if el data exists
        sample = next(
            (f.get(axis_key) for f in history if f.get(axis_key) is not None),
            None)
        if sample is None:
            ax.text(0.5, 0.5, 'ULA mode\n(no elevation)',
                    ha='center', va='center', fontsize=10,
                    transform=ax.transAxes, color='grey')
            return

        n_frames = len(history)
        # Collect all (frame_idx, source_idx, doa) triples
        by_src = {}   # source_idx → list of (frame_idx, doa)
        for fi, frame in enumerate(history):
            ax_data = frame.get(axis_key)
            if ax_data is None:
                continue
            for si, doa in enumerate(ax_data['doa']):
                if not np.isnan(doa):
                    by_src.setdefault(si, []).append((fi, doa))

        n_src = max((k for k in by_src), default=-1) + 1
        cmaps = ['plasma', 'viridis', 'cool', 'autumn']

        for si, pts in by_src.items():
            if not pts:
                continue
            xs = np.array([p[0] for p in pts])
            ys = np.array([p[1] for p in pts])
            # Colour by normalised time within this source's trail
            colors = (xs - xs.min()) / max(xs.max() - xs.min(), 1e-9)
            cmap = cmaps[si % len(cmaps)]
            ax.scatter(xs, ys, c=colors, cmap=cmap,
                       s=10, alpha=0.6, linewidths=0, zorder=2)
            ax.plot(xs, ys, color=color, lw=0.6, alpha=0.35, zorder=1)
            # Latest estimate
            ax.scatter([xs[-1]], [ys[-1]],
                       c='red', s=30, zorder=4, linewidths=0)

        ax.set_xlim(-0.5, max(n_frames - 1, 1) + 0.5)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        if self._paused:
            return
        with self.store.lock:
            snap     = {mac: list(hist)
                        for mac, hist in self.store.rare_l_data.items()}
            snap_ord = list(self.store.cbf_order)
            mac_idx  = self.current_mac_idx

        n_mac = len(snap_ord)
        if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
            self.mac_slider.valmax = n_mac - 1
            self.mac_slider.ax.set_xlim(0, n_mac - 1)

        macs = [m for m in snap_ord if snap.get(m)]
        if macs:
            mac_idx = min(mac_idx, len(macs) - 1)
            mac     = macs[mac_idx]
            history = snap[mac]
            self._draw_angle_pane(
                self.ax_az, history, 'az',
                f'Azimuth — {mac}', 'steelblue')
            self._draw_angle_pane(
                self.ax_el, history, 'el',
                f'Elevation — {mac}', 'darkorange')
        else:
            for ax in (self.ax_az, self.ax_el):
                ax.cla()
                ax.set_title('(waiting for CBF data…)', fontsize=9)

    # ----------------------------------------------------------------
    # Setup
    # ----------------------------------------------------------------

    def run(self):
        """Create angle-over-time figure. Returns FuncAnimation."""
        fig, (self.ax_az, self.ax_el) = plt.subplots(
            2, 1, figsize=(14, 7), sharex=False)
        self.fig = fig
        fig.suptitle('RARE-L DoA Angles Over Time', fontsize=12)
        fig.subplots_adjust(hspace=0.45, top=0.90, bottom=0.14)

        ax_mac = fig.add_axes([0.12, 0.04, 0.76, 0.030])
        self.mac_slider = Slider(
            ax_mac, 'MAC index', 0, 1,
            valinit=0, valstep=1, color='darkorange')
        self.mac_slider.on_changed(
            lambda v: setattr(self, 'current_mac_idx', int(v)))

        self._add_pause_button(fig)
        return FuncAnimation(
            fig, self._animate,
            interval=INTERVAL, cache_frame_data=False)


# -----------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Live plotter for ESP32 CBF and CSI telemetry')
    ap.add_argument('port', help='Serial port, e.g. /dev/ttyACM0 or COM3')
    ap.add_argument('--baud', type=int, default=115200,
                    help='Baud rate (default: 115200)')
    args = ap.parse_args()

    store = SharedDataStore()
    store.start_reader(args.port, args.baud)

    # Figures 1, 2, 4, 5, 6 hidden — data still ingested by the store.
    cbf_wf_plotter      = CBFStreamWaterfallPlotter(store)  # fig 1
    rarel_cpx_plotter   = RARELComplexPlotter(store)        # fig 2
    rarel_wf_plotter    = RARELWaterfallPlotter(store)      # fig 3
    rarel_ang_plotter   = RARELAnglePlotter(store)          # fig 4

    # Keep animation objects alive — assigning to _ would GC them.
    _ani_cbf_wf     = cbf_wf_plotter.run()
    _ani_rarel_cpx  = rarel_cpx_plotter.run()
    _ani_rarel_wf   = rarel_wf_plotter.run()
    _ani_rarel_ang  = rarel_ang_plotter.run()

    plt.show()
    store._running = False


if __name__ == '__main__':
    main()
