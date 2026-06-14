#!/usr/bin/env python3
"""
Live plotter for ESP32 compressed beamforming telemetry.

Usage:
    python3 plot_telemetry.py /dev/ttyACM0 [--baud 115200]

Wire format — one text line per packet (immune to CR/LF translation):
    TELEM:<hex>\\n
where <hex> encodes: MAGIC(4B) PAYLOAD_LEN(2B LE) PAYLOAD(N B) XOR_CKSUM(1B)

Payload layout:
    src_mac          6B
    num_streams (Nc) 1B
    num_rows    (Nr) 1B
    num_subcarriers  2B LE
    phy_type         1B
    is_mu            1B
    bandwidth        1B
    snr[0..Nc-1]     Nc x int8
    Per subcarrier (num_subcarriers times):
        phi[0..phi_count-1]  phi_count x int16 LE
        psi[0..psi_count-1]  psi_count x int16 LE
        eigenvals[0..Nr-1]   Nr x float32 LE  (sorted descending)
        evec_real[Nr x Nr]   Nr*Nr x float32 LE  row-major
        evec_imag[Nr x Nr]   Nr*Nr x float32 LE  row-major

Interactivity:
    - Toolbar: zoom / pan / home per axis.
    - Subcarrier slider: selects which subcarrier to show in the right pane.
    - MAC slider: selects which MAC address to show in the left pane.
    - Click any legend entry to hide/show that eigenvector across both panes.
    - Zoom is preserved across animation frames; slider changes reset that
      pane's view so it re-fits the new data automatically.
"""

import argparse
import struct
import threading
import time

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.animation import FuncAnimation
from matplotlib.lines import Line2D
from matplotlib.widgets import Slider

# -----------------------------------------------------------------------
# Protocol
# -----------------------------------------------------------------------

_MAGIC  = bytes([0xCB, 0xF1, 0xFE, 0xED])
_PREFIX = b'TELEM:'


def mac_str(raw_bytes):
    return ':'.join(f'{b:02X}' for b in raw_bytes)


def _try_parse_line(line):
    """Parse one TELEM: hex line. Returns a packet dict or None."""
    line = line.strip()
    if not line.startswith(_PREFIX):
        return None
    try:
        data = bytes.fromhex(line[len(_PREFIX):].decode('ascii'))
    except Exception:
        return None

    if len(data) < 7 or data[:4] != _MAGIC:
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
        print('[parser] checksum mismatch — dropped packet', flush=True)
        return None

    return _parse_payload(payload)


def _parse_payload(payload):
    pos = 0

    mac             = mac_str(payload[pos:pos + 6]); pos += 6
    nc              = payload[pos];                  pos += 1
    nr              = payload[pos];                  pos += 1
    (num_sc,)       = struct.unpack_from('<H', payload, pos); pos += 2
    phy_type        = payload[pos]; pos += 1
    is_mu           = bool(payload[pos]); pos += 1
    bandwidth       = payload[pos]; pos += 1
    snr             = list(struct.unpack_from(f'{nc}b', payload, pos))
    pos            += nc

    phi_count = nc * nr - nc * (nc + 1) // 2
    psi_count = phi_count

    subcarriers = []
    for _ in range(num_sc):
        phi = list(struct.unpack_from(f'<{phi_count}h', payload, pos))
        pos += phi_count * 2
        psi = list(struct.unpack_from(f'<{psi_count}h', payload, pos))
        pos += psi_count * 2

        eigenvals = list(struct.unpack_from(f'<{nr}f', payload, pos))
        pos += nr * 4

        evec_r = np.array(
            struct.unpack_from(f'<{nr * nr}f', payload, pos)
        ).reshape(nr, nr)
        pos += nr * nr * 4

        evec_i = np.array(
            struct.unpack_from(f'<{nr * nr}f', payload, pos)
        ).reshape(nr, nr)
        pos += nr * nr * 4

        subcarriers.append({
            'phi':       phi,
            'psi':       psi,
            'eigenvals': eigenvals,
            'evec_real': evec_r,
            'evec_imag': evec_i,
        })

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

_HIDDEN_ALPHA  = 0.15
_VISIBLE_ALPHA = 1.0


# -----------------------------------------------------------------------
# Plotter
# -----------------------------------------------------------------------

class CBFPlotter:
    def __init__(self, port, baud):
        self.port        = port
        self.baud        = baud
        self.lock        = threading.Lock()
        self.mac_data    = {}   # mac -> {nr, nc, snr, subcarriers:{sc->data}}
        self.mac_order   = []   # insertion-ordered unique MACs
        self.current_sc  = 0
        self.current_mac_idx = 0   # index into mac_order for left pane
        self._running    = True

        # Visibility — eigenvec indices hidden in both panes
        self._hidden_evecs = set()

        # id(legend_proxy_line) -> ('evec', int)  rebuilt each frame
        self._lmap = {}

        # (Line2D, evec_k) — all toggleable plot lines from last frame
        self._track_lines = []

        # Zoom preservation per axis; None = auto-scale (SNR not preserved)
        self._zoom = {'mac_ev': None, 'right': None}
        self._prev_sc      = -1
        self._prev_mac_idx = -1
        self._frame_count  = 0

        self.fig = None

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
        if value in self._hidden_evecs:
            self._hidden_evecs.discard(value)
        else:
            self._hidden_evecs.add(value)
        # Immediately update alpha on all tracked lines for instant feedback
        for line, k in self._track_lines:
            line.set_alpha(
                _HIDDEN_ALPHA if k in self._hidden_evecs else _VISIBLE_ALPHA)
        self.fig.canvas.draw_idle()

    def _register_evec_legend(self, leg, num_evec_entries):
        """
        Register the first num_evec_entries legend proxy lines as evec toggles.
        Remaining proxies (element linestyle entries) are left non-interactive.
        """
        proxies = leg.get_lines()
        for i in range(min(num_evec_entries, len(proxies))):
            proxy = proxies[i]
            proxy.set_picker(5)
            self._lmap[id(proxy)] = ('evec', i)
            proxy.set_alpha(_HIDDEN_ALPHA if i in self._hidden_evecs
                            else _VISIBLE_ALPHA)

    # ----------------------------------------------------------------
    # Background serial reader
    # ----------------------------------------------------------------

    def _reader(self):
        import serial
        with serial.Serial(self.port, self.baud, timeout=1) as ser:
            while self._running:
                try:
                    line = ser.readline()
                    if not line:
                        continue
                    pkt = _try_parse_line(line)
                    if pkt is None:
                        continue
                    with self.lock:
                        mac = pkt['mac']
                        if mac not in self.mac_data:
                            self.mac_data[mac] = {
                                'nr':          pkt['nr'],
                                'nc':          pkt['nc'],
                                'snr':         pkt['snr'],
                                'subcarriers': {},
                            }
                            self.mac_order.append(mac)
                        else:
                            self.mac_data[mac]['snr'] = pkt['snr']
                        for idx, sc in enumerate(pkt['subcarriers']):
                            self.mac_data[mac]['subcarriers'][idx] = sc
                    print(f'[reader] packet from {mac}, '
                          f'{pkt["num_sc"]} subcarriers', flush=True)
                except Exception as exc:
                    print(f'[reader] {exc}', flush=True)
                    time.sleep(0.1)

    def _start_reader(self):
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    # ----------------------------------------------------------------
    # Left-top pane: eigenvector trajectories across subcarriers
    # ----------------------------------------------------------------

    def _draw_mac_panel(self, snap, mac_idx):
        """
        For the selected MAC: plot each eigenvector element's complex value
        across every subcarrier.

        Color:     eigenvector index k  (COLORS[k])
        Linestyle: antenna element j    (LINESTYLES[j])
        Points:    one per subcarrier, connected in subcarrier order.
        """
        ax = self.ax_mac_ev
        ax.cla()

        mac_list = [m for m in self.mac_order if m in snap]
        if not mac_list:
            ax.set_title('Eigenvector trajectories\n(waiting for data…)',
                         fontsize=9)
            return

        mac_idx = min(mac_idx, len(mac_list) - 1)
        mac     = mac_list[mac_idx]
        md      = snap[mac]
        nr      = md['nr']
        scs     = sorted(md['subcarriers'].keys())

        ax.set_title(f'Eigenvector trajectories — {mac}\n'
                     '(click eigvec entries to show/hide)', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imaginary', fontsize=8)
        ax.axhline(0, color='gray', lw=0.5)
        ax.axvline(0, color='gray', lw=0.5)
        ax.set_aspect('equal', adjustable='datalim')
        ax.grid(True, alpha=0.3)

        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(np.cos(th), np.sin(th), 'k--', lw=0.5, alpha=0.2)

        for k in range(nr):
            a     = _HIDDEN_ALPHA if k in self._hidden_evecs else _VISIBLE_ALPHA
            color = COLORS[k % len(COLORS)]

            for j in range(nr):
                ls = LINESTYLES[j % len(LINESTYLES)]
                re_pts = [md['subcarriers'][s]['evec_real'][j, k]
                          for s in scs]
                im_pts = [md['subcarriers'][s]['evec_imag'][j, k]
                          for s in scs]
                (traj,) = ax.plot(re_pts, im_pts,
                                  color=color, linestyle=ls,
                                  linewidth=0.9, alpha=a)
                self._track_lines.append((traj, k))
                if re_pts:
                    (m0,) = ax.plot(re_pts[0], im_pts[0], 'o',
                                    color=color, markersize=3, alpha=a * 0.7)
                    (m1,) = ax.plot(re_pts[-1], im_pts[-1], 's',
                                    color=color, markersize=3, alpha=a * 0.7)
                    self._track_lines.append((m0, k))
                    self._track_lines.append((m1, k))

        # Legend: eigvec colors (interactive) then element linestyles (key only)
        evec_handles = [
            Line2D([0], [0], color=COLORS[k % len(COLORS)],
                   linewidth=2, label=f'eigvec {k}')
            for k in range(nr)
        ]
        elem_handles = [
            Line2D([0], [0], color='black',
                   linestyle=LINESTYLES[j % len(LINESTYLES)],
                   linewidth=1.2, label=f'element {j}')
            for j in range(nr)
        ]
        if evec_handles or elem_handles:
            leg = ax.legend(handles=evec_handles + elem_handles,
                            fontsize=7, loc='upper right')
            self._register_evec_legend(leg, len(evec_handles))

    # ----------------------------------------------------------------
    # Left-bottom pane: SNR bar chart
    # ----------------------------------------------------------------

    def _draw_snr(self, snap):
        ax = self.ax_snr
        ax.cla()
        ax.set_title('SNR per spatial stream', fontsize=9)
        ax.set_ylabel('SNR (0.25 dB / LSB)', fontsize=8)
        ax.grid(True, alpha=0.3, axis='y')

        bar_entries = [
            (mac, snap[mac]['snr'], COLORS[i % len(COLORS)])
            for i, mac in enumerate(self.mac_order)
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
    # Right pane: eigenvectors on the complex plane (per subcarrier)
    # ----------------------------------------------------------------

    def _draw_right(self, snap, sc):
        ax = self.ax_right
        ax.cla()
        ax.set_title(f'Eigenvectors — subcarrier {sc}\n'
                     '(click eigvec entries to show/hide)', fontsize=9)
        ax.set_xlabel('Real', fontsize=8)
        ax.set_ylabel('Imaginary', fontsize=8)
        ax.axhline(0, color='gray', lw=0.5)
        ax.axvline(0, color='gray', lw=0.5)
        ax.set_aspect('equal', adjustable='datalim')
        ax.grid(True, alpha=0.3)

        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(np.cos(th), np.sin(th), 'k--', lw=0.5, alpha=0.2)

        mac_legend   = []
        evec_legend  = []
        max_nr       = 0

        for i, mac in enumerate(self.mac_order):
            if mac not in snap:
                continue
            md      = snap[mac]
            sc_data = md['subcarriers'].get(sc)
            if sc_data is None:
                continue

            color  = COLORS[i % len(COLORS)]
            nr     = md['nr']
            max_nr = max(max_nr, nr)
            evec_r = sc_data['evec_real']
            evec_i = sc_data['evec_imag']
            evals  = sc_data['eigenvals']

            for k in range(nr):
                a  = (_HIDDEN_ALPHA if k in self._hidden_evecs
                      else _VISIBLE_ALPHA)
                ls = LINESTYLES[k % len(LINESTYLES)]
                re_pts = evec_r[:, k]
                im_pts = evec_i[:, k]
                (ln,) = ax.plot(re_pts, im_pts,
                                color=color, linestyle=ls,
                                marker='o', markersize=4,
                                linewidth=1.2, alpha=a)
                self._track_lines.append((ln, k))
                ax.annotate(f'λ{k}={evals[k]:.2f}',
                            xy=(re_pts[-1], im_pts[-1]),
                            fontsize=6, color=color, alpha=a * 0.75)

            mac_legend.append(
                Line2D([0], [0], color=color, linewidth=2, label=mac))

        for k in range(min(max_nr, len(LINESTYLES))):
            evec_legend.append(
                Line2D([0], [0], color='black',
                       linestyle=LINESTYLES[k],
                       linewidth=1.2, label=f'eigvec {k}'))

        all_handles = mac_legend + evec_legend
        if all_handles:
            leg = ax.legend(handles=all_handles,
                            labels=[h.get_label() for h in all_handles],
                            fontsize=7, loc='upper right')
            # Register evec entries (the mac entries are just visual keys here)
            evec_proxies = leg.get_lines()[len(mac_legend):]
            for k, proxy in enumerate(evec_proxies):
                proxy.set_picker(5)
                self._lmap[id(proxy)] = ('evec', k)
                proxy.set_alpha(_HIDDEN_ALPHA if k in self._hidden_evecs
                                else _VISIBLE_ALPHA)

    # ----------------------------------------------------------------
    # Animation callback
    # ----------------------------------------------------------------

    def _animate(self, _frame):
        with self.lock:
            snap = {
                mac: {
                    'nr':          v['nr'],
                    'nc':          v['nc'],
                    'snr':         list(v['snr']),
                    'subcarriers': dict(v['subcarriers']),
                }
                for mac, v in self.mac_data.items()
            }
            sc      = self.current_sc
            mac_idx = self.current_mac_idx

        sc_changed      = (sc      != self._prev_sc)
        mac_idx_changed = (mac_idx != self._prev_mac_idx)
        self._prev_sc      = sc
        self._prev_mac_idx = mac_idx

        # Save current limits before clearing (zoom preservation)
        if self._frame_count > 0:
            # Reset view when the controlling slider changes
            if not mac_idx_changed:
                self._zoom['mac_ev'] = (self.ax_mac_ev.get_xlim(),
                                        self.ax_mac_ev.get_ylim())
            else:
                self._zoom['mac_ev'] = None
            if not sc_changed:
                self._zoom['right'] = (self.ax_right.get_xlim(),
                                       self.ax_right.get_ylim())
            else:
                self._zoom['right'] = None

        # Expand slider ranges to fit incoming data
        if snap:
            all_scs = [idx for v in snap.values()
                       for idx in v['subcarriers']]
            if all_scs and max(all_scs) > self.sc_slider.valmax:
                self.sc_slider.valmax = max(all_scs)
                self.sc_slider.ax.set_xlim(0, max(all_scs))

            n_mac = len(self.mac_order)
            if n_mac > 1 and (n_mac - 1) > self.mac_slider.valmax:
                self.mac_slider.valmax = n_mac - 1
                self.mac_slider.ax.set_xlim(0, n_mac - 1)

        self._lmap        = {}
        self._track_lines = []

        self._draw_mac_panel(snap, mac_idx)
        self._draw_snr(snap)
        self._draw_right(snap, sc)

        # Restore zoom after redraw
        if self._zoom['mac_ev']:
            self.ax_mac_ev.set_xlim(self._zoom['mac_ev'][0])
            self.ax_mac_ev.set_ylim(self._zoom['mac_ev'][1])
        if self._zoom['right']:
            self.ax_right.set_xlim(self._zoom['right'][0])
            self.ax_right.set_ylim(self._zoom['right'][1])

        self._frame_count += 1

    # ----------------------------------------------------------------
    # Entry point
    # ----------------------------------------------------------------

    def run(self):
        fig = plt.figure(figsize=(15, 9))
        self.fig = fig
        fig.suptitle('ESP32 CBF Telemetry — Live', fontsize=12)

        # 3 rows: [plots, subcarrier slider, MAC slider]
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

        self._start_reader()

        _ani = FuncAnimation(
            fig, self._animate,
            interval=500, cache_frame_data=False)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        plt.show()
        self._running = False


# -----------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Live plotter for ESP32 CBF telemetry')
    ap.add_argument('port',
                    help='Serial port, e.g. /dev/ttyACM0 or COM3')
    ap.add_argument('--baud', type=int, default=115200,
                    help='Baud rate (default: 115200)')
    args = ap.parse_args()
    CBFPlotter(args.port, args.baud).run()


if __name__ == '__main__':
    main()
