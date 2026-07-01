#!/usr/bin/env python3
"""
plot_saved_telemetry.py — replay saved telemetry CSVs as interactive plots.

Reads the CSV files produced by save_telemetry.py (via plot_telemetry.py)
and generates the same interactive plot windows.

Usage:
    python3 plot_saved_telemetry.py \\
        [--csi <YYYYMMDD_HHMMSS>_CSI_INFO.csv] \\
        [--cbf <YYYYMMDD_HHMMSS>_CBF_INFO.csv]

At least one of --csi or --cbf must be provided.

CBF loading recomputes RARE-L estimates from the saved V-matrix columns
using the ARRAY_SHAPE constant from plot_telemetry.py, so you can re-run
analysis with a different array geometry without re-capturing.

Plots produced:
    CBF data  →  CBF V-matrix waterfall, RARE-L complex-plane roots,
                 RARE-L MUSIC waterfall, RARE-L angle-over-time
    CSI data  →  CSI channel impulse response (IFFT) waterfall
"""

import argparse
import sys
import threading

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import rare_est
from plot_telemetry import (
    CBFStreamWaterfallPlotter,
    RARELComplexPlotter,
    RARELWaterfallPlotter,
    RARELAnglePlotter,
    VVHWaterfallPlotter,
    VVHRatioWaterfallPlotter,
    VVHRatioComplexPlotter,
    VVHComplexPlotter,
    CSIIFFTPlotter,
    ARRAY_SHAPE,
)


# -----------------------------------------------------------------------
# Static data store
# -----------------------------------------------------------------------

class StaticDataStore:
    """
    Read-only data store populated from saved CSVs.

    Exposes the same attributes as SharedDataStore so the live plotters
    can be reused without modification.
    """

    def __init__(self):
        self.lock             = threading.Lock()
        self.csi_data         = {}   # mac -> [{'amp', 'phase'}]
        self.csi_info         = {}   # mac -> {rssi, channel}
        self.csi_order        = []
        self.cbf_data         = {}   # mac -> {nr, nc, snr, subcarriers}
        self.cbf_order        = []
        self.cbf_wf_data      = {}   # mac -> [wf_frame]
        self.cbf_v_trail_data = {}   # mac -> [trail_frame]
        self.rare_l_data      = {}   # mac -> [result]
        self._running         = False


# -----------------------------------------------------------------------
# CSV loaders
# -----------------------------------------------------------------------

def load_csi(store, path):
    """Load a CSI CSV into store.csi_data and store.csi_info.

    Args:
        store (StaticDataStore): Target store.
        path (str): Path to *_CSI_INFO.csv.
    """
    df = pd.read_csv(path)
    amp_cols   = [c for c in df.columns if c.startswith('amp_')]
    phase_cols = [c for c in df.columns if c.startswith('phase_')]

    for mac, grp in df.groupby('src_mac'):
        grp = grp.sort_values('frame_idx')
        store.csi_order.append(mac)

        frames = []
        for _, row in grp.iterrows():
            frames.append({
                'amp':   row[amp_cols].values.astype(np.float64),
                'phase': row[phase_cols].values.astype(np.float64),
            })

        store.csi_data[mac] = frames
        store.csi_info[mac] = {
            'rssi':    int(grp['rssi'].iloc[-1]),
            'channel': int(grp['channel'].iloc[-1]),
        }

    n_frames = sum(len(v) for v in store.csi_data.values())
    print(f'[loader] CSI: {len(store.csi_order)} MAC(s), '
          f'{n_frames} frames', flush=True)


def load_cbf(store, path, array_shape=None):
    """Load a CBF CSV into store CBF fields with recomputed RARE-L.

    Reconstructs V matrices from amp/phase columns, recomputes RARE-L
    estimates, and populates cbf_wf_data, cbf_v_trail_data, and
    rare_l_data.

    Args:
        store (StaticDataStore): Target store.
        path (str): Path to *_CBF_INFO.csv.
        array_shape (tuple or None): (M_rows, N_cols) for UPA, None=ULA.
    """
    df = pd.read_csv(path)

    for mac, mac_grp in df.groupby('mac'):
        store.cbf_order.append(mac)
        nr = int(mac_grp['nr'].iloc[0])
        nc = int(mac_grp['nc'].iloc[0])

        wf_frames     = []
        trail_frames  = []
        rare_l_frames = []

        for _, frame_grp in mac_grp.groupby('frame_idx'):
            frame_grp = frame_grp.sort_values('sc_idx')
            n_sc      = len(frame_grp)

            pairs, subcarriers = _reconstruct_pairs(frame_grp, nr, nc, n_sc)

            wf_frames.append({'nr': nr, 'nc': nc, 'pairs': pairs})
            trail_frames.append(
                {'nr': nr, 'nc': nc, 'subcarriers': subcarriers})
            rare_l_frames.append(
                _recompute_rare_l(subcarriers, nr, nc, n_sc, array_shape))

        store.cbf_wf_data[mac]      = wf_frames
        store.cbf_v_trail_data[mac] = trail_frames
        store.rare_l_data[mac]      = rare_l_frames

        snr_cols = sorted(c for c in mac_grp.columns
                          if c.startswith('snr_stream'))
        snr = [int(mac_grp[c].iloc[-1]) for c in snr_cols]
        store.cbf_data[mac] = {
            'nr': nr, 'nc': nc, 'snr': snr, 'subcarriers': {}}

    n_frames = sum(len(v) for v in store.cbf_wf_data.values())
    print(f'[loader] CBF: {len(store.cbf_order)} MAC(s), '
          f'{n_frames} frames', flush=True)


def _reconstruct_pairs(frame_grp, nr, nc, n_sc):
    """Rebuild pairs dict and subcarrier V-matrices from a subcarrier group.

    Args:
        frame_grp (DataFrame): One CBF frame's rows, sorted by sc_idx.
        nr (int): Number of receive antennas.
        nc (int): Number of spatial streams.
        n_sc (int): Number of subcarriers in this frame.
    Returns:
        tuple: (pairs, subcarriers)
            pairs:       {(r,c): {'amp': 1D ndarray, 'phase': 1D ndarray}}
            subcarriers: {sc_idx: (nr, nc) complex ndarray}
    """
    pairs = {}
    for r in range(nr):
        for c in range(nc):
            ac = f'amp_rx{r}_stream{c}'
            pc = f'phase_rx{r}_stream{c}'
            if ac not in frame_grp.columns:
                continue
            amp   = frame_grp[ac].values.astype(np.float64)
            phase = frame_grp[pc].values.astype(np.float64)
            amp[np.isnan(amp)]     = 0.0
            phase[np.isnan(phase)] = 0.0
            pairs[(r, c)] = {'amp': amp, 'phase': phase}

    subcarriers = {}
    for sci in range(n_sc):
        V = np.zeros((nr, nc), dtype=complex)
        for r in range(nr):
            for c in range(nc):
                p = pairs.get((r, c))
                if p is not None:
                    V[r, c] = p['amp'][sci] * np.exp(1j * p['phase'][sci])
        subcarriers[sci] = V

    return pairs, subcarriers


def _recompute_rare_l(subcarriers, nr, nc, n_sc, array_shape):
    """Recompute RARE-L DoA estimate from a set of per-subcarrier V matrices.

    Args:
        subcarriers (dict): {sc_idx: (nr, nc) complex ndarray}
        nr (int): Number of receive antennas.
        nc (int): Number of spatial streams.
        n_sc (int): Number of subcarriers.
        array_shape (tuple or None): UPA shape or None for ULA.
    Returns:
        dict: RARE-L result dict from rare_est.rare_l_estimate().
    """
    v_all = np.array([subcarriers[i] for i in range(n_sc)])
    n_src = max(1, min(nc, nr - 1))
    try:
        return rare_est.rare_l_estimate(
            v_all, n_sources=n_src, array_shape=array_shape)
    except Exception:
        nan_d   = np.full(n_src, np.nan)
        az_scan = rare_est._SCAN_ANGLES_DEG
        az_nan  = np.zeros(len(az_scan))
        if array_shape is None:
            nan_z = np.full(n_src, np.nan + 0j)
            return {
                'mode': 'ula', 'eigenvalues': np.array([]),
                'n_sources': n_src,
                'az': {'doa': nan_d, 'roots': nan_z,
                       'spectrum_db': az_nan,
                       'scan_deg': az_scan},
                'el': None,
            }
        el_scan = np.linspace(-90.0, 90.0, 91)
        return {
            'mode': 'upa', 'eigenvalues': np.array([]),
            'n_sources': n_src,
            'az': {'doa': nan_d,
                   'spectrum_db': az_nan,
                   'scan_deg': az_scan},
            'el': {'doa': nan_d,
                   'spectrum_db': np.zeros(91),
                   'scan_deg': el_scan},
        }


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Replay saved ESP32 telemetry CSVs as interactive plots')
    ap.add_argument('--csi', metavar='CSV',
                    help='Path to *_CSI_INFO.csv')
    ap.add_argument('--cbf', metavar='CSV',
                    help='Path to *_CBF_INFO.csv')
    args = ap.parse_args()

    if not args.csi and not args.cbf:
        ap.print_help()
        sys.exit(1)

    store = StaticDataStore()

    if args.csi:
        load_csi(store, args.csi)
    if args.cbf:
        load_cbf(store, args.cbf, array_shape=ARRAY_SHAPE)

    anis = []

    if args.cbf and store.cbf_order:
        cbf_wf        = CBFStreamWaterfallPlotter(store)
        # rarel_cpx   = RARELComplexPlotter(store)    # hidden
        # rarel_wf    = RARELWaterfallPlotter(store)  # hidden
        # rarel_ang   = RARELAnglePlotter(store)      # hidden
        vvh_wf        = VVHWaterfallPlotter(store)
        vvh_ratio_wf  = VVHRatioWaterfallPlotter(store)
        vvh_ratio_cpx = VVHRatioComplexPlotter(store)
        vvh_cpx       = VVHComplexPlotter(store)
        anis += [cbf_wf.run(),
                 # rarel_cpx.run(), rarel_wf.run(), rarel_ang.run(),
                 vvh_wf.run(), vvh_ratio_wf.run(),
                 vvh_ratio_cpx.run(), vvh_cpx.run()]

    if args.csi and store.csi_order:
        csi_ifft = CSIIFFTPlotter(store)
        anis.append(csi_ifft.run())

    if not anis:
        print('[loader] No data to plot — exiting.')
        sys.exit(1)

    plt.show()


if __name__ == '__main__':
    main()
