#!/usr/bin/env python3
"""
live_plot.py — Interactive live-plot control panel.

Usage:
    python3 live_plot.py /dev/ttyACM0 [--baud 115200]
    python3 live_plot.py /dev/ttyACM0 --baud 115200

Opens a control-panel window with buttons for each plot type.
Each button spawns a new animated figure for the selected MAC.
The "Save Data" button starts logging CSV files.

A MAC selector (slider + label) at the bottom of the control panel
lets the user choose which MAC address the next plot button will
open.  The selector updates automatically as new CBF packets arrive.

CSV output:
    <timestamp>_CSI_INFO.csv
    <timestamp>_CBF_INFO.csv
Saved to ../../database/collected_data/ relative to this file.
"""

import argparse
import csv
import os
import sys
import threading
import time
from datetime import datetime

import yaml

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, Slider

import rx_data
import special_plots as sp

# ------------------------------------------------------------------
# Output directory
# ------------------------------------------------------------------

_HERE       = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.normpath(
    os.path.join(_HERE, '..', '..', 'database', 'collected_data'))
_CONFIG_PATH = os.path.join(_HERE, 'config.yaml')


# ------------------------------------------------------------------
# Config — loaded from config.yaml, overridable at runtime
# ------------------------------------------------------------------

def _load_config(path: str = _CONFIG_PATH) -> dict:
    """Load config.yaml; return defaults if file is missing.

    Args:
        path (str): Path to config.yaml.
    Returns:
        dict: Configuration values.
    """
    defaults = {
        'apply_smooth_filter': True,
        'apply_antialias':     True,
        'plot_window':         200,
        'denom_ratio':         0.01,
    }
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        defaults.update(data)
    except FileNotFoundError:
        print(f'[config] {path} not found, using defaults', flush=True)
    return defaults


_cfg = _load_config()

SMOOTH    = bool(_cfg['apply_smooth_filter'])
ANTIALIAS = bool(_cfg['apply_antialias'])
WINDOW    = int(_cfg['plot_window'])
DENOM_LIM = float(_cfg['denom_ratio'])


# ------------------------------------------------------------------
# CSV saver
# ------------------------------------------------------------------

class _CsvSaver:
    """Saves CBF and CSI frames to session CSV files.

    Registers as a DataStore listener and flushes to disk
    every flush_interval seconds.

    Args:
        store (rx_data.DataStore): Source data store.
        output_dir (str): Directory for output CSVs.
        flush_interval (float): Seconds between disk flushes.
    """

    def __init__(
        self,
        store: rx_data.DataStore,
        output_dir: str = _OUTPUT_DIR,
        flush_interval: float = 2.0,
    ):
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        self._csi_path = os.path.join(output_dir, f'{ts}_CSI_INFO.csv')
        self._cbf_path = os.path.join(output_dir, f'{ts}_CBF_INFO.csv')

        self._lock      = threading.Lock()
        self._csi_q     = []
        self._cbf_q     = []
        self._csi_file  = None
        self._cbf_file  = None
        self._csi_writer = None
        self._cbf_writer = None
        self._cbf_schema = None   # (nr, nc)
        self._csi_n_sc   = None
        self._csi_idx    = {}
        self._cbf_idx    = {}
        self._interval   = flush_interval
        self._running    = True

        store.add_listener(self._on_packet)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='CsvSaver')
        self._thread.start()
        print(f'[saver] CSI → {self._csi_path}', flush=True)
        print(f'[saver] CBF → {self._cbf_path}', flush=True)

    def stop(self):
        """Flush remaining data and close files."""
        self._running = False
        self._thread.join(timeout=10)
        self._flush()
        for f in (self._csi_file, self._cbf_file):
            if f:
                f.close()

    def _on_packet(self, kind: str, frame: dict):
        with self._lock:
            if kind == 'csi':
                self._csi_q.append(frame)
            else:
                self._cbf_q.append(frame)

    def _run(self):
        while self._running:
            self._flush()
            time.sleep(self._interval)

    def _flush(self):
        with self._lock:
            csi_batch = self._csi_q[:]
            del self._csi_q[:]
            cbf_batch = self._cbf_q[:]
            del self._cbf_q[:]
        for f in csi_batch:
            self._write_csi(f)
        for f in cbf_batch:
            self._write_cbf(f)
        for fh in (self._csi_file, self._cbf_file):
            if fh:
                fh.flush()

    # -- CSI helpers --

    def _open_csi(self, n_sc: int):
        self._csi_file   = open(self._csi_path, 'w', newline='')
        self._csi_writer = csv.writer(self._csi_file)
        hdr = ['frame_idx', 'src_mac', 'rssi', 'channel']
        hdr += [f'amp_{i}'   for i in range(n_sc)]
        hdr += [f'phase_{i}' for i in range(n_sc)]
        self._csi_writer.writerow(hdr)
        self._csi_n_sc = n_sc

    def _write_csi(self, frame: dict):
        amp   = frame['amp']
        phase = frame['phase']
        n     = len(amp)
        if self._csi_n_sc is None:
            self._open_csi(n)
        mac  = frame['mac']
        fidx = self._csi_idx.get(mac, 0)
        n0   = self._csi_n_sc
        row  = [fidx, mac, int(frame['rssi']), int(frame['channel'])]
        row += [round(float(amp[i]),   6) if i < n else '' for i in range(n0)]
        row += [round(float(phase[i]), 6) if i < n else '' for i in range(n0)]
        self._csi_writer.writerow(row)
        self._csi_idx[mac] = fidx + 1

    # -- CBF helpers --

    def _open_cbf(self, nr: int, nc: int):
        self._cbf_file   = open(self._cbf_path, 'w', newline='')
        self._cbf_writer = csv.writer(self._cbf_file)
        hdr = ['frame_idx', 'sc_idx', 'mac',
               'nc', 'nr', 'n_sc', 'phy_type', 'is_mu', 'bandwidth']
        hdr += [f'snr_stream{c}' for c in range(nc)]
        for r in range(nr):
            for c in range(nc):
                hdr.append(f'amp_rx{r}_stream{c}')
        for r in range(nr):
            for c in range(nc):
                hdr.append(f'phase_rx{r}_stream{c}')
        self._cbf_writer.writerow(hdr)
        self._cbf_schema = (nr, nc)

    def _write_cbf(self, frame: dict):
        v_all = frame['v_all']   # (n_sc, nr, nc)
        nr    = frame['nr']
        nc    = frame['nc']
        n_sc  = frame['n_sc']
        mac   = frame['mac']
        if self._cbf_schema is None:
            self._open_cbf(nr, nc)
        nr0, nc0 = self._cbf_schema
        fidx = self._cbf_idx.get(mac, 0)
        snr  = frame.get('snr', [])

        for sc in range(n_sc):
            row = [fidx, sc, mac, nc, nr, n_sc,
                   int(frame.get('phy_type', 0)),
                   int(frame.get('is_mu', False)),
                   int(frame.get('bandwidth', 0))]
            for c in range(nc0):
                row.append(int(snr[c]) if c < len(snr) else '')
            for r in range(nr0):
                for c in range(nc0):
                    if r < nr and c < nc:
                        v = v_all[sc, r, c]
                        row.append(round(float(np.abs(v)), 6))
                    else:
                        row.append('')
            for r in range(nr0):
                for c in range(nc0):
                    if r < nr and c < nc:
                        v = v_all[sc, r, c]
                        row.append(round(float(np.angle(v)), 6))
                    else:
                        row.append('')
            self._cbf_writer.writerow(row)

        self._cbf_idx[mac] = fidx + 1


# ------------------------------------------------------------------
# Control panel
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
]

_N_COLS = 3


def _build_control_panel(store: rx_data.DataStore) -> plt.Figure:
    """Build and return the control-panel matplotlib figure.

    Buttons open new animated plot windows for the selected MAC.
    The MAC selector (slider + label) at the bottom lets the user
    choose which MAC address the next button click will display.
    The "Save Data" button starts CSV logging.

    Args:
        store (rx_data.DataStore): Live data source.
    Returns:
        plt.Figure: Control panel figure.
    """
    n_btns   = len(_BTN_DEFS) + 1   # +1 for Save Data
    n_rows   = (n_btns + _N_COLS - 1) // _N_COLS

    # Extra height for the MAC selector area below the buttons.
    fig_ctrl = plt.figure('Live Plot Control',
                           figsize=(_N_COLS * 2.8, n_rows * 1.0 + 1.2))
    fig_ctrl.suptitle('Live Plot Control', fontsize=11)

    pad   = 0.04

    # Buttons occupy normalized y range [0.20, 0.92].
    # This leaves [0.00, 0.19] for the MAC selector.
    w_btn = (1.0 - (_N_COLS + 1) * pad) / _N_COLS
    h_btn = (0.72 - n_rows * pad) / n_rows

    # ------------------------------------------------------------------
    # MAC selector state and helpers
    # ------------------------------------------------------------------

    mac_state = {'list': [], 'idx': 0}

    # Label showing the currently selected MAC string
    ax_mac_lbl = fig_ctrl.add_axes([0.02, 0.005, 0.96, 0.05])
    ax_mac_lbl.set_axis_off()
    mac_text = ax_mac_lbl.text(
        0.5, 0.5, 'MAC: (waiting for data...)',
        transform=ax_mac_lbl.transAxes,
        fontsize=8, va='center', ha='center')

    # Slider to select MAC index
    ax_mac_sl = fig_ctrl.add_axes([0.12, 0.085, 0.76, 0.025])
    mac_sl = Slider(
        ax_mac_sl, 'MAC', 0, 1,
        valinit=0, valstep=1, color='darkorange')

    def _update_mac_label():
        macs = mac_state['list']
        if not macs:
            mac_text.set_text('MAC: (waiting for data...)')
        else:
            idx = min(mac_state['idx'], len(macs) - 1)
            mac_text.set_text(f'MAC: {macs[idx]}')
        fig_ctrl.canvas.draw_idle()

    def _on_mac_sl(val):
        mac_state['idx'] = int(round(val))
        _update_mac_label()

    mac_sl.on_changed(_on_mac_sl)

    def _get_selected_mac() -> str:
        """Return the MAC address currently selected in the control panel.

        Returns:
            str: MAC address, or '' if no data has arrived yet.
        """
        macs = mac_state['list']
        if not macs:
            return ''
        return macs[min(mac_state['idx'], len(macs) - 1)]

    def _refresh_mac_list(_f=None):
        with store.lock:
            macs = list(store.cbf_order)
        if macs == mac_state['list']:
            return
        mac_state['list'] = macs
        n = len(macs) - 1
        mac_sl.valmax = max(n, 1)
        mac_sl.ax.set_xlim(0, max(n, 1))
        mac_sl.set_val(min(mac_state['idx'], max(n, 0)))
        _update_mac_label()

    # Animate MAC list refresh every 1 s
    mac_ani = FuncAnimation(
        fig_ctrl, _refresh_mac_list, interval=1000, blit=False)

    # ------------------------------------------------------------------
    # Plot buttons
    # ------------------------------------------------------------------

    open_anis = []   # keep references to prevent GC
    open_figs = []

    saver_state = {'saver': None}

    def _make_plot_cb(fn_name):
        def _cb(_event):
            fn = getattr(sp, fn_name, None)
            if fn is None:
                print(f'[ctrl] unknown plot: {fn_name}', flush=True)
                return
            try:
                fig, ani = fn(
                    store,
                    window=WINDOW,
                    denoise=SMOOTH,
                    antialias=ANTIALIAS,
                    denom_lim=DENOM_LIM,
                    mac=_get_selected_mac(),
                )
                open_figs.append(fig)
                open_anis.append(ani)
            except Exception as exc:
                print(f'[ctrl] {fn_name}: {exc}', flush=True)
        return _cb

    def _save_cb(_event):
        if saver_state['saver'] is not None:
            saver_state['saver'].stop()
            saver_state['saver'] = None
            save_btn.label.set_text('Save Data')
            print('[ctrl] saving stopped', flush=True)
        else:
            saver_state['saver'] = _CsvSaver(store)
            save_btn.label.set_text('Stop Saving')
        fig_ctrl.canvas.draw_idle()

    all_btns = _BTN_DEFS + [('Save Data', None)]

    btn_axes = []
    for idx in range(n_btns):
        row = idx // _N_COLS
        col = idx %  _N_COLS
        x   = pad + col * (w_btn + pad)
        y   = 0.92 - (row + 1) * (h_btn + pad)
        ax  = fig_ctrl.add_axes([x, y, w_btn, h_btn])
        btn_axes.append(ax)

    btns = []
    for idx, (label, fn_name) in enumerate(all_btns):
        color = '#d9ead3' if fn_name is None else 'lightgrey'
        b = Button(btn_axes[idx], label, color=color, hovercolor='white')
        if fn_name is None:
            save_btn = b
            b.on_clicked(_save_cb)
        else:
            b.on_clicked(_make_plot_cb(fn_name))
        btns.append(b)

    # Keep references alive to prevent garbage collection
    fig_ctrl._btns     = btns
    fig_ctrl._anis_ref = open_anis
    fig_ctrl._figs_ref = open_figs
    fig_ctrl._mac_ani  = mac_ani
    fig_ctrl._mac_sl   = mac_sl

    return fig_ctrl


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Live ESP32 telemetry plot control panel')
    ap.add_argument('port', help='Serial port (e.g. /dev/ttyACM0)')
    ap.add_argument('--baud', type=int, default=115200)
    args = ap.parse_args()

    store  = rx_data.DataStore()
    reader = rx_data.SerialReader(store, args.port, args.baud)
    reader.start()

    plt.ion()
    fig_ctrl = _build_control_panel(store)
    fig_ctrl.show()

    try:
        plt.show(block=True)
    except KeyboardInterrupt:
        pass

    store.stop()
    print('[main] exiting', flush=True)


if __name__ == '__main__':
    main()
