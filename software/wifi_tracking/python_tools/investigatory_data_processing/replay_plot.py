#!/usr/bin/env python3
"""
replay_plot.py — Interactive replay of saved telemetry CSVs.

Usage:
    python3 replay_plot.py --cbf <CBF_INFO.csv> [--csi <CSI_INFO.csv>]
    python3 replay_plot.py --cbf ../../database/collected_data/20260701_CBF_INFO.csv

Opens a control-panel window with:
  - Frame slider to scrub through recorded data.
  - Play / Pause button to auto-advance frames.
  - MAC selector (slider + label) to choose the address for each plot.
  - Plot buttons matching live_plot.py.

CSV format matches the output of live_plot.py's "Save Data" button.
"""

import argparse
import os
import sys
import threading

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, Slider
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import special_plots as sp

_CONFIG_PATH = os.path.join(_HERE, 'config.yaml')


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------

def _load_config(path: str = _CONFIG_PATH) -> dict:
    """Load config.yaml; return defaults if file is missing.

    Args:
        path (str): Path to config.yaml.
    Returns:
        dict: Configuration values.
    """
    defaults = {
        'apply_smooth_filter':         True,
        'apply_antialias':             True,
        'apply_cubic_spline_upsample': True,
        'plot_window':                 200,
        'denom_ratio':                 0.01,
    }
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        defaults.update(data)
    except FileNotFoundError:
        print(f'[config] {path} not found, using defaults', flush=True)
    return defaults


_cfg      = _load_config()
SMOOTH        = bool(_cfg['apply_smooth_filter'])
ANTIALIAS     = bool(_cfg['apply_antialias'])
CUBIC_SPLINE  = bool(_cfg['apply_cubic_spline_upsample'])
WINDOW        = int(_cfg['plot_window'])
DENOM_LIM     = float(_cfg['denom_ratio'])


# ------------------------------------------------------------------
# CSV loading
# ------------------------------------------------------------------

def _load_csi(path: str) -> tuple:
    """Load a CSI CSV into per-MAC frame lists.

    Args:
        path (str): Path to *_CSI_INFO.csv.
    Returns:
        tuple: (csi_by_mac dict, csi_order list).
    """
    csi_by_mac, csi_order = {}, []
    df         = pd.read_csv(path)
    amp_cols   = [c for c in df.columns if c.startswith('amp_')]
    phase_cols = [c for c in df.columns if c.startswith('phase_')]
    for mac, grp in df.groupby('src_mac'):
        grp = grp.sort_values('frame_idx')
        csi_order.append(mac)
        csi_by_mac[mac] = [
            {'amp':     row[amp_cols].values.astype(float),
             'phase':   row[phase_cols].values.astype(float),
             'rssi':    int(row['rssi']),
             'channel': int(row['channel'])}
            for _, row in grp.iterrows()
        ]
    return csi_by_mac, csi_order


def _reconstruct_v(
    frame_grp: pd.DataFrame, nr: int, nc: int, n_sc: int
) -> np.ndarray:
    """Rebuild a (n_sc, nr, nc) complex V tensor from one CBF frame group.

    Args:
        frame_grp (pd.DataFrame): Rows for one frame_idx, sorted by sc_idx.
        nr (int): Number of receive antennas.
        nc (int): Number of spatial streams.
        n_sc (int): Number of subcarriers.
    Returns:
        np.ndarray: (n_sc, nr, nc) complex.
    """
    v_all = np.zeros((n_sc, nr, nc), dtype=complex)
    for r in range(nr):
        for c in range(nc):
            ac = f'amp_rx{r}_stream{c}'
            pc = f'phase_rx{r}_stream{c}'
            if ac not in frame_grp.columns:
                continue
            amp   = frame_grp[ac].values.astype(float)
            phase = frame_grp[pc].values.astype(float)
            amp[np.isnan(amp)]     = 0.0
            phase[np.isnan(phase)] = 0.0
            for k in range(min(n_sc, len(amp))):
                v_all[k, r, c] = amp[k] * np.exp(1j * phase[k])
    return v_all


def _load_cbf(path: str) -> tuple:
    """Load a CBF CSV into per-MAC v_all frame lists.

    Args:
        path (str): Path to *_CBF_INFO.csv.
    Returns:
        tuple: (cbf_by_mac dict, cbf_meta dict, cbf_order list).
    """
    cbf_by_mac, cbf_meta, cbf_order = {}, {}, []
    df = pd.read_csv(path)
    for mac, mac_grp in df.groupby('mac'):
        cbf_order.append(mac)
        nr = int(mac_grp['nr'].iloc[0])
        nc = int(mac_grp['nc'].iloc[0])
        snr_cols = sorted(
            c for c in mac_grp.columns if c.startswith('snr_stream'))
        snr = [int(mac_grp[c].iloc[-1]) for c in snr_cols]
        cbf_meta[mac] = {'nr': nr, 'nc': nc, 'snr': snr}
        frames = []
        for _fidx, fg in mac_grp.groupby('frame_idx'):
            fg   = fg.sort_values('sc_idx')
            n_sc = len(fg)
            frames.append(_reconstruct_v(fg, nr, nc, n_sc))
        cbf_by_mac[mac] = frames
    return cbf_by_mac, cbf_meta, cbf_order


# ------------------------------------------------------------------
# Static data store
# ------------------------------------------------------------------

class StaticStore:
    """Read-only store backed by loaded CSV data.

    Exposes the same interface as rx_data.DataStore so special_plots
    functions work without modification.  time_idx controls which
    prefix of each frame list is visible, simulating replay over time.

    Args:
        cbf_by_mac (dict): mac -> list of (n_sc, nr, nc) v_all arrays.
        cbf_meta (dict): mac -> {nr, nc, snr}.
        cbf_order (list): Ordered MAC addresses.
        csi_by_mac (dict): mac -> list of CSI frame dicts.
        csi_order (list): Ordered MAC addresses for CSI.
        window (int): Rolling window depth (frames).
    """

    def __init__(
        self,
        cbf_by_mac: dict,
        cbf_meta:   dict,
        cbf_order:  list,
        csi_by_mac: dict,
        csi_order:  list,
        window:     int = WINDOW,
    ):
        self.lock       = threading.Lock()
        self._cbf       = cbf_by_mac
        self._cbf_meta  = cbf_meta
        self._cbf_order = cbf_order
        self._csi       = csi_by_mac
        self._csi_order = csi_order
        self._window    = window
        self.time_idx   = max(
            (len(v) for v in cbf_by_mac.values()), default=1)

    @property
    def cbf_order(self) -> list:
        return self._cbf_order

    @property
    def cbf_meta(self) -> dict:
        return self._cbf_meta

    @property
    def cbf_frames(self) -> dict:
        """Window of frames up to time_idx for each MAC."""
        result = {}
        for mac, frames in self._cbf.items():
            end   = min(self.time_idx, len(frames))
            start = max(0, end - self._window)
            slc   = frames[start:end]
            result[mac] = [
                {'v_all': v, 'n_sc': v.shape[0],
                 'nr':    v.shape[1], 'nc': v.shape[2]}
                for v in slc
            ]
        return result

    def add_listener(self, fn):
        pass

    def stop(self):
        pass


# ------------------------------------------------------------------
# Plot button definitions (must match live_plot.py)
# ------------------------------------------------------------------

_BTN_DEFS = [
    ('VVH Waterfall',          'VVH_waterfall'),
    ('VVH Complex Plane',      'VVH_cplx'),
    ('VVH SS Waterfall',       'VVH_ss_waterfall'),
    ('VVH SS Complex Plane',   'VVH_ss_cplx'),
    ('VVH Ratio Waterfall',    'VVH_ratio_waterfall'),
    ('VVH Ratio Complex Plane','VVH_ratio_cplx'),
    ('VVH Ratio SS Waterfall', 'VVH_ratio_ss_waterfall'),
    ('VVH Ratio SS Cplx',      'VVH_ratio_ss_cplx'),
    ('Array + AoA',            'est_array_plot'),
    ('Array Cal Accuracy',     'array_cal_accuracy'),
]

_N_COLS = 3


# ------------------------------------------------------------------
# Control panel
# ------------------------------------------------------------------

def _build_control_panel(store: StaticStore, max_frames: int) -> plt.Figure:
    """Build the replay control-panel figure.

    Layout (bottom → top in normalised y):
      [0.00, 0.30]  frame slider, play/pause, MAC selector
      [0.30, 0.92]  plot buttons

    Args:
        store (StaticStore): Loaded data store.
        max_frames (int): Total recorded frames across all MACs.
    Returns:
        plt.Figure: Control panel figure.
    """
    n_btns = len(_BTN_DEFS)
    n_rows = (n_btns + _N_COLS - 1) // _N_COLS

    fig = plt.figure('Replay Control',
                     figsize=(_N_COLS * 2.8, n_rows * 1.0 + 1.9))
    fig.suptitle('Replay Control', fontsize=11)

    pad   = 0.04
    w_btn = (1.0 - (_N_COLS + 1) * pad) / _N_COLS
    h_btn = (0.62 - n_rows * pad) / n_rows   # buttons span [0.30, 0.92]

    # ------------------------------------------------------------------
    # Frame controls (y ∈ [0.00, 0.14])
    # ------------------------------------------------------------------

    play_state = {'playing': False}

    ax_frame_lbl = fig.add_axes([0.02, 0.005, 0.96, 0.04])
    ax_frame_lbl.set_axis_off()
    frame_text = ax_frame_lbl.text(
        0.5, 0.5, f'Frame: {max_frames} / {max_frames}',
        transform=ax_frame_lbl.transAxes,
        fontsize=8, va='center', ha='center')

    ax_frame_sl = fig.add_axes([0.22, 0.055, 0.68, 0.025])
    frame_sl = Slider(
        ax_frame_sl, 'Frame', 1, max(max_frames, 2),
        valinit=max_frames, valstep=1, color='steelblue')

    ax_play = fig.add_axes([0.02, 0.05, 0.16, 0.04])
    play_btn = Button(ax_play, '▶ Play', color='#d9ead3', hovercolor='white')

    def _update_frame_text(idx: int):
        frame_text.set_text(f'Frame: {idx} / {max_frames}')

    def _on_frame_sl(val):
        idx = int(round(val))
        store.time_idx = idx
        _update_frame_text(idx)

    frame_sl.on_changed(_on_frame_sl)

    def _on_play(_event):
        if play_state['playing']:
            play_state['playing'] = False
            play_btn.label.set_text('▶ Play')
        else:
            if store.time_idx >= max_frames:
                store.time_idx = 1
                frame_sl.set_val(1)
            play_state['playing'] = True
            play_btn.label.set_text('⏸ Pause')
        fig.canvas.draw_idle()

    play_btn.on_clicked(_on_play)

    # ------------------------------------------------------------------
    # MAC selector (y ∈ [0.14, 0.28])
    # ------------------------------------------------------------------

    mac_state = {'list': [], 'idx': 0}

    ax_mac_lbl = fig.add_axes([0.02, 0.145, 0.96, 0.04])
    ax_mac_lbl.set_axis_off()
    mac_text = ax_mac_lbl.text(
        0.5, 0.5, 'MAC: (no data)',
        transform=ax_mac_lbl.transAxes,
        fontsize=8, va='center', ha='center')

    ax_mac_sl = fig.add_axes([0.12, 0.20, 0.76, 0.025])
    mac_sl = Slider(
        ax_mac_sl, 'MAC', 0, 1,
        valinit=0, valstep=1, color='darkorange')

    def _update_mac_label():
        macs = mac_state['list']
        if not macs:
            mac_text.set_text('MAC: (no data)')
        else:
            idx = min(mac_state['idx'], len(macs) - 1)
            mac_text.set_text(f'MAC: {macs[idx]}')

    def _on_mac_sl(val):
        mac_state['idx'] = int(round(val))
        _update_mac_label()
        fig.canvas.draw_idle()

    mac_sl.on_changed(_on_mac_sl)

    def _get_selected_mac() -> str:
        """Return the currently selected MAC address.

        Returns:
            str: MAC address, or '' if none loaded.
        """
        macs = mac_state['list']
        if not macs:
            return ''
        return macs[min(mac_state['idx'], len(macs) - 1)]

    # Populate MAC list immediately (static data, no polling needed).
    with store.lock:
        macs = list(store.cbf_order)
    mac_state['list'] = macs
    n = max(len(macs) - 1, 1)
    mac_sl.valmax = n
    mac_sl.ax.set_xlim(0, n)
    _update_mac_label()

    # ------------------------------------------------------------------
    # Control animation — drives play-back and keeps frame label fresh
    # ------------------------------------------------------------------

    def _tick(_frame):
        if play_state['playing']:
            new_idx = store.time_idx + 1
            if new_idx >= max_frames:
                new_idx = max_frames
                play_state['playing'] = False
                play_btn.label.set_text('▶ Play')
            store.time_idx = new_idx
            frame_sl.set_val(new_idx)   # triggers _on_frame_sl
        _update_frame_text(store.time_idx)

    ctrl_ani = FuncAnimation(fig, _tick, interval=300, blit=False)

    # ------------------------------------------------------------------
    # Plot buttons (y ∈ [0.30, 0.92])
    # ------------------------------------------------------------------

    open_figs = []
    open_anis = []

    def _make_plot_cb(fn_name: str):
        def _cb(_event):
            fn = getattr(sp, fn_name, None)
            if fn is None:
                print(f'[replay] unknown plot: {fn_name}', flush=True)
                return
            try:
                fig_p, ani_p = fn(
                    store,
                    window=WINDOW,
                    denoise=SMOOTH,
                    antialias=ANTIALIAS,
                    cubic_spline_upsample=CUBIC_SPLINE,
                    denom_lim=DENOM_LIM,
                    mac=_get_selected_mac(),
                )
                open_figs.append(fig_p)
                open_anis.append(ani_p)
            except Exception as exc:
                print(f'[replay] {fn_name}: {exc}', flush=True)
        return _cb

    btns = []
    for idx, (label, fn_name) in enumerate(_BTN_DEFS):
        row = idx // _N_COLS
        col = idx %  _N_COLS
        x   = pad + col * (w_btn + pad)
        y   = 0.92 - (row + 1) * (h_btn + pad)
        ax  = fig.add_axes([x, y, w_btn, h_btn])
        b   = Button(ax, label, color='lightgrey', hovercolor='white')
        b.on_clicked(_make_plot_cb(fn_name))
        btns.append(b)

    # Keep all references alive to prevent garbage collection.
    fig._ctrl_ani  = ctrl_ani
    fig._mac_sl    = mac_sl
    fig._frame_sl  = frame_sl
    fig._play_btn  = play_btn
    fig._btns      = btns
    fig._open_figs = open_figs
    fig._open_anis = open_anis

    return fig


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Replay saved ESP32 telemetry CSVs')
    ap.add_argument('--cbf', required=True,
                    help='Path to *_CBF_INFO.csv')
    ap.add_argument('--csi', default='',
                    help='Path to *_CSI_INFO.csv (optional)')
    args = ap.parse_args()

    print(f'[replay] loading CBF: {args.cbf}', flush=True)
    cbf_by_mac, cbf_meta, cbf_order = _load_cbf(args.cbf)
    n_total = sum(len(v) for v in cbf_by_mac.values())
    print(f'[replay] {len(cbf_order)} MAC(s), {n_total} frames',
          flush=True)

    csi_by_mac, csi_order = {}, []
    if args.csi:
        print(f'[replay] loading CSI: {args.csi}', flush=True)
        csi_by_mac, csi_order = _load_csi(args.csi)

    store = StaticStore(
        cbf_by_mac, cbf_meta, cbf_order,
        csi_by_mac, csi_order)

    max_frames = max(
        (len(v) for v in cbf_by_mac.values()), default=1)
    print(f'[replay] max_frames={max_frames}', flush=True)

    plt.ion()
    fig_ctrl = _build_control_panel(store, max_frames)
    fig_ctrl.show()

    try:
        plt.show(block=True)
    except KeyboardInterrupt:
        pass

    print('[replay] exiting', flush=True)


if __name__ == '__main__':
    main()
