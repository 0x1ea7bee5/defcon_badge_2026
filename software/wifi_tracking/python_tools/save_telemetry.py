#!/usr/bin/env python3
"""
save_telemetry.py — session CSV logger for ESP32 CBF and CSI telemetry.

Imported by plot_telemetry.py.  Register TelemetrySaver against a
SharedDataStore to write every received packet to CSV.

Two session files are created in output_dir at construction time:
    <YYYYMMDD_HHMMSS>_CSI_INFO.csv
    <YYYYMMDD_HHMMSS>_CBF_INFO.csv

CSI CSV — one row per received frame:
    frame_idx, src_mac, rssi, channel,
    amp_0 … amp_{N-1},       (linear amplitude per subcarrier bin)
    phase_0 … phase_{N-1}    (radians)

CBF CSV — one row per (frame × subcarrier):
    frame_idx, sc_idx, mac,
    nc, nr, num_sc, phy_type, is_mu, bandwidth,
    snr_stream{c}             (raw int8 LSB; 0.25 dB per LSB),
    amp_rx{r}_stream{c}       (|V[r,c]| at this subcarrier),
    phase_rx{r}_stream{c}     (∠V[r,c] in radians),
    az_doa_src{s}             (RARE-L azimuth, degrees; same across sc),
    el_doa_src{s}             (elevation, degrees; '' in ULA mode),
    eigenvalue_{i}            (RARE-L eigenvalues; same across sc)

Missing values are written as empty strings.
"""

import csv
import os
import threading
import time
from datetime import datetime

import numpy as np

# Default output directory: <repo_root>/database/collected_data
_DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'database', 'collected_data')


class TelemetrySaver:
    """
    Saves CBF and CSI packets to session CSV files as they arrive.

    Registers itself as a packet listener on the provided store.
    Packets are buffered in memory and flushed to disk every
    flush_interval_s seconds by a background thread.

    Args:
        store: SharedDataStore with add_packet_listener().
        output_dir (str): Directory for output CSV files.
                          Defaults to database/collected_data.
        flush_interval_s (float): Disk-flush period in seconds.
    """

    def __init__(self, store,
                 output_dir=_DEFAULT_OUTPUT_DIR, flush_interval_s=2.0):
        self._interval   = flush_interval_s
        self._lock       = threading.Lock()
        self._csi_queue  = []
        self._cbf_queue  = []
        self._thread     = None
        self._running    = False

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._csi_path = os.path.join(output_dir, f'{ts}_CSI_INFO.csv')
        self._cbf_path = os.path.join(output_dir, f'{ts}_CBF_INFO.csv')

        self._csi_file   = None
        self._csi_writer = None
        self._cbf_file   = None
        self._cbf_writer = None

        # Column-count caches set on first packet of each type.
        self._csi_n      = None        # number of complex I/Q samples
        self._cbf_schema = None        # (nr, nc, n_src, n_eig)

        # Per-MAC cumulative frame counters.
        self._csi_idx = {}
        self._cbf_idx = {}

        store.add_packet_listener(self._on_packet)

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def start(self):
        """Start background flush thread."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name='TelemetrySaver')
        self._thread.start()
        print(f'[saver] CSI  → {self._csi_path}', flush=True)
        print(f'[saver] CBF  → {self._cbf_path}', flush=True)

    def stop(self):
        """Stop the flush thread, do a final flush, and close files."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._flush()
        self._close_files()

    def csi_path(self):
        """Return the CSI CSV file path (str)."""
        return self._csi_path

    def cbf_path(self):
        """Return the CBF CSV file path (str)."""
        return self._cbf_path

    # ----------------------------------------------------------------
    # Listener — called from reader thread while store.lock is held
    # ----------------------------------------------------------------

    def _on_packet(self, kind, frame_data):
        """Queue one packet frame for deferred CSV write.

        Called synchronously from SharedDataStore._ingest_* while the
        store lock is held, so this must return quickly.

        Args:
            kind (str): 'csi' or 'cbf'.
            frame_data (dict): Assembled packet frame from the store.
        """
        with self._lock:
            if kind == 'csi':
                self._csi_queue.append(frame_data)
            else:
                self._cbf_queue.append(frame_data)

    # ----------------------------------------------------------------
    # Background thread
    # ----------------------------------------------------------------

    def _run(self):
        while self._running:
            self._flush()
            time.sleep(self._interval)

    def _flush(self):
        """Drain both queues to their CSV files."""
        with self._lock:
            csi_batch = self._csi_queue[:]
            del self._csi_queue[:]
            cbf_batch = self._cbf_queue[:]
            del self._cbf_queue[:]

        for frame in csi_batch:
            self._write_csi(frame)
        for frame in cbf_batch:
            self._write_cbf(frame)

        if self._csi_file:
            self._csi_file.flush()
        if self._cbf_file:
            self._cbf_file.flush()

    def _close_files(self):
        for attr in ('_csi_file', '_cbf_file'):
            f = getattr(self, attr)
            if f:
                f.close()
                setattr(self, attr, None)

    # ----------------------------------------------------------------
    # CSI CSV helpers
    # ----------------------------------------------------------------

    def _open_csi_csv(self, n_samples):
        """Open CSI CSV and write header.

        Args:
            n_samples (int): Number of complex subcarrier samples.
        """
        self._csi_file   = open(self._csi_path, 'w', newline='')
        self._csi_writer = csv.writer(self._csi_file)
        hdr = ['frame_idx', 'src_mac', 'rssi', 'channel']
        hdr += [f'amp_{i}'   for i in range(n_samples)]
        hdr += [f'phase_{i}' for i in range(n_samples)]
        self._csi_writer.writerow(hdr)
        self._csi_n = n_samples

    def _write_csi(self, frame):
        """Write one CSI frame as a single CSV row.

        Args:
            frame (dict): Keys: mac, rssi, channel, amp, phase.
        """
        amp   = frame['amp']
        phase = frame['phase']
        n     = len(amp)

        if self._csi_n is None:
            self._open_csi_csv(n)

        mac  = frame['mac']
        fidx = self._csi_idx.get(mac, 0)
        n0   = self._csi_n

        row = [fidx, mac, int(frame['rssi']), int(frame['channel'])]
        row += [round(float(amp[i]),   6) if i < n else '' for i in range(n0)]
        row += [round(float(phase[i]), 6) if i < n else '' for i in range(n0)]
        self._csi_writer.writerow(row)
        self._csi_idx[mac] = fidx + 1

    # ----------------------------------------------------------------
    # CBF CSV helpers
    # ----------------------------------------------------------------

    def _open_cbf_csv(self, nr, nc, n_src, n_eig):
        """Open CBF CSV and write header.

        Args:
            nr (int): Number of receive antennas.
            nc (int): Number of spatial streams.
            n_src (int): Number of RARE-L sources.
            n_eig (int): Number of RARE-L eigenvalues.
        """
        self._cbf_file   = open(self._cbf_path, 'w', newline='')
        self._cbf_writer = csv.writer(self._cbf_file)

        hdr = ['frame_idx', 'sc_idx', 'mac',
               'nc', 'nr', 'num_sc', 'phy_type', 'is_mu', 'bandwidth']
        hdr += [f'snr_stream{c}' for c in range(nc)]
        for r in range(nr):
            for c in range(nc):
                hdr.append(f'amp_rx{r}_stream{c}')
        for r in range(nr):
            for c in range(nc):
                hdr.append(f'phase_rx{r}_stream{c}')
        hdr += [f'az_doa_src{s}' for s in range(n_src)]
        hdr += [f'el_doa_src{s}' for s in range(n_src)]
        hdr += [f'eigenvalue_{i}' for i in range(n_eig)]
        self._cbf_writer.writerow(hdr)
        self._cbf_schema = (nr, nc, n_src, n_eig)

    def _write_cbf(self, frame):
        """Write one CBF frame as num_sc subcarrier rows.

        Args:
            frame (dict): Keys: mac, nr, nc, num_sc, phy_type, is_mu,
                          bandwidth, snr, pairs, rare_l.
        """
        mac    = frame['mac']
        nr     = frame['nr']
        nc     = frame['nc']
        n_sc   = frame['num_sc']
        pairs  = frame.get('pairs', {})
        rare_l = frame.get('rare_l', {})

        if self._cbf_schema is None:
            n_src = max(1, min(nc, nr - 1))
            n_eig = len(rare_l.get('eigenvalues', []))
            self._open_cbf_csv(nr, nc, n_src, n_eig)

        nr0, nc0, n_src, n_eig = self._cbf_schema
        fidx = self._cbf_idx.get(mac, 0)

        az_vals  = _extract_doa(rare_l, 'az', n_src)
        el_vals  = _extract_doa(rare_l, 'el', n_src)
        eig_vals = _extract_eigs(rare_l, n_eig)
        snr      = frame.get('snr', [])

        for sc in range(n_sc):
            row = [fidx, sc, mac, nc, nr, n_sc,
                   int(frame['phy_type']),
                   int(frame['is_mu']),
                   int(frame['bandwidth'])]

            for c in range(nc0):
                row.append(int(snr[c]) if c < len(snr) else '')

            for r in range(nr0):
                for c in range(nc0):
                    p = pairs.get((r, c))
                    row.append(
                        round(float(p['amp'][sc]), 6)
                        if p is not None and sc < len(p['amp']) else '')

            for r in range(nr0):
                for c in range(nc0):
                    p = pairs.get((r, c))
                    row.append(
                        round(float(p['phase'][sc]), 6)
                        if p is not None and sc < len(p['phase']) else '')

            row += az_vals
            row += el_vals
            row += eig_vals
            self._cbf_writer.writerow(row)

        self._cbf_idx[mac] = fidx + 1


# -----------------------------------------------------------------------
# Private formatting helpers
# -----------------------------------------------------------------------

def _extract_doa(rare_l, axis_key, n_src):
    """Return n_src DoA values as rounded floats or '' for NaN/missing.

    Args:
        rare_l (dict): RARE-L result dict.
        axis_key (str): 'az' or 'el'.
        n_src (int): Expected number of sources.
    Returns:
        list: n_src scalars (float or '').
    """
    ax  = rare_l.get(axis_key)
    if ax is None:
        return [''] * n_src
    doa = ax.get('doa', [])
    out = []
    for s in range(n_src):
        v = float(doa[s]) if s < len(doa) else float('nan')
        out.append('' if np.isnan(v) else round(v, 4))
    return out


def _extract_eigs(rare_l, n_eig):
    """Return n_eig eigenvalues as formatted strings or '' for missing.

    Args:
        rare_l (dict): RARE-L result dict.
        n_eig (int): Expected number of eigenvalues.
    Returns:
        list: n_eig scalars (str or '').
    """
    eigs = np.real(rare_l.get('eigenvalues', np.array([])))
    out  = []
    for i in range(n_eig):
        v = float(eigs[i]) if i < len(eigs) else float('nan')
        out.append('' if np.isnan(v) else f'{v:.6e}')
    return out
