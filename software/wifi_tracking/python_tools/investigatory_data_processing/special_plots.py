"""
special_plots.py — Specific animated plot types.

Each public function creates a figure + FuncAnimation and returns
(fig, ani).  Functions accept any store that exposes:
    store.lock              threading.Lock
    store.cbf_order         list[str]
    store.cbf_meta          dict[str, {nr, nc, ...}]
    store.cbf_frames        dict[str, iterable[{v_all, n_sc, nr, nc}]]

This makes them work with both rx_data.DataStore (live) and the
StaticStore in plot_existing_data.ipynb (replay).

Lower-triangular grid layout:
  All VVH and VVH-ratio variants place subplots at position (i, j)
  in a square nr × nr grid, for each pair where i > j.  The grid
  cells on and above the diagonal are left empty, giving a visually
  clean lower-triangular layout.

Public plot functions:
  VVH_waterfall, VVH_cplx,
  VVH_ss_waterfall, VVH_ss_cplx,
  VVH_ratio_waterfall, VVH_ratio_cplx,
  VVH_ratio_ss_waterfall, VVH_ratio_ss_cplx,
  est_array_plot

DSP helpers (also importable):
  get_vvh(store, mac)       -> R_all (n_frames, n_sc, nr, nr)
  get_vvh_ss(store, mac, s) -> R_ss  (n_frames, n_sc, nr, nr)

Ratio computation is delegated to dsp.vv_star_ratio / dsp.vv_star_ratio_ss.
"""

import threading

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Slider
from mpl_toolkits.mplot3d import Axes3D   # noqa: F401

import dsp
import rare_est
from plot_types.waterfalls import WaterfallFigure
from plot_types.complex_plane import ComplexPlaneFigure

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

INTERVAL    = 500     # animation refresh (ms)
WINDOW_LEN  = 200     # rolling window depth (frames)
DENOM_LIM   = 0.01   # ratio: discard if |diagonal| < this
ARRAY_SHAPE = (2, 2)  # default initial geometry for est_array_plot


# ------------------------------------------------------------------
# DSP helpers
# ------------------------------------------------------------------

def _lower_tri(nr: int) -> list:
    """List of (i, j) lower-triangular pairs for an nr × nr matrix.

    Args:
        nr (int): Matrix dimension.
    Returns:
        list[tuple[int, int]]: All (i, j) where i > j.
    """
    return [(i, j) for i in range(nr) for j in range(i)]


def _snapshot_frames(store, mac: str) -> list:
    """Thread-safe snapshot of CBF frame deque for one MAC.

    Args:
        store: DataStore instance.
        mac (str): MAC address.
    Returns:
        list[dict]: Frame dicts, oldest first.
    """
    with store.lock:
        q = store.cbf_frames.get(mac)
        return list(q) if q else []


def _first_mac(store) -> str:
    """Return first MAC in cbf_order, or ''.

    Args:
        store: DataStore instance.
    Returns:
        str: MAC address.
    """
    with store.lock:
        order = list(store.cbf_order)
    return order[0] if order else ''


def _all_macs(store) -> list:
    """Return copy of cbf_order.

    Args:
        store: DataStore instance.
    Returns:
        list[str]: MAC addresses.
    """
    with store.lock:
        return list(store.cbf_order)


def _meta(store, mac: str) -> dict:
    """Return CBF metadata dict for mac, or {}.

    Args:
        store: DataStore instance.
        mac (str): MAC address.
    Returns:
        dict: {nr, nc, snr, ...} or {}.
    """
    with store.lock:
        return dict(store.cbf_meta.get(mac, {}))


def get_vvh(store, mac: str) -> np.ndarray:
    """Compute full VV* matrix history for one MAC.

    Args:
        store: DataStore instance.
        mac (str): MAC address.
    Returns:
        np.ndarray: (n_frames, n_sc, nr, nr) complex, or None.
    """
    frames = _snapshot_frames(store, mac)
    if not frames:
        return None
    v4d = np.stack([f['v_all'] for f in frames], axis=0)
    # R[f,k] = V[f,k] @ V[f,k].H
    return np.einsum('fkia,fkja->fkij', v4d, v4d.conj())


def get_vvh_ss(store, mac: str, stream_idx: int) -> np.ndarray:
    """Per-stream VV* history for one MAC and spatial stream.

    Args:
        store: DataStore instance.
        mac (str): MAC address.
        stream_idx (int): Spatial stream index.
    Returns:
        np.ndarray: (n_frames, n_sc, nr, nr) complex, or None.
    """
    frames = _snapshot_frames(store, mac)
    if not frames:
        return None
    v4d = np.stack([f['v_all'] for f in frames], axis=0)
    nc  = v4d.shape[3]
    s   = min(stream_idx, nc - 1)
    v_c = v4d[:, :, :, s]   # (n_frames, n_sc, nr)
    return np.einsum('fki,fkj->fkij', v_c, v_c.conj())



# ------------------------------------------------------------------
# Layout helpers
# ------------------------------------------------------------------

def _make_wf(nr: int, fig_title: str, window: int) -> WaterfallFigure:
    """Build a WaterfallFigure with lower-triangular layout.

    Args:
        nr (int): V-matrix row count (sets grid dimension).
        fig_title (str): Figure window title.
        window (int): Rolling window depth.
    Returns:
        WaterfallFigure: Configured figure.
    """
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]' for i, j in pairs}
    return WaterfallFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        window=window, fig_title=fig_title)


def _make_cpx(
    nr: int, fig_title: str, window: int,
    series_labels: list = None,
) -> ComplexPlaneFigure:
    """Build a ComplexPlaneFigure with lower-triangular layout.

    Args:
        nr (int): V-matrix row count (sets grid dimension).
        fig_title (str): Figure window title.
        window (int): Rolling window depth.
        series_labels (list[str]): Series label names.
    Returns:
        ComplexPlaneFigure: Configured figure.
    """
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]' for i, j in pairs}
    return ComplexPlaneFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        series_labels=series_labels or ['Value'],
        window=window, fig_title=fig_title)


def _wf_data(
    R_all:    np.ndarray,
    denoise:  bool = True,
    antialias: bool = True,
) -> dict:
    """Build waterfall data_dict from VV* history.

    Args:
        R_all (np.ndarray): (n_frames, n_sc, nr, nr) complex.
        denoise (bool): Apply moving-average along subcarriers.
        antialias (bool): Apply cubic-spline upsampling along frames.
    Returns:
        dict: {(i,j): (mag_db, phase)} waterfall data.
    """
    _, _, nr, _ = R_all.shape
    data = {}
    for i, j in _lower_tri(nr):
        elem   = R_all[:, :, i, j]
        amp_db = 20.0 * np.log10(np.maximum(np.abs(elem), 1e-12))
        phase  = np.angle(elem)
        if denoise:
            amp_db = dsp.denoise(amp_db)
            phase  = dsp.denoise_phase(phase)
        if antialias:
            amp_db = dsp.antialias(amp_db)
            phase  = dsp.antialias_phase(phase)
        data[(i, j)] = (amp_db, phase)
    return data


def _ratio_wf_data(
    ratio:     dict,
    denoise:   bool = True,
    antialias: bool = True,
) -> dict:
    """Build waterfall data_dict from a ratio dict.

    Amplitude is peak-normalised so the brightest valid ratio is at
    0 dB regardless of absolute scale.  Masked (NaN) positions map
    to -240 dB, which falls below the display floor and appears dark.
    This matches the legacy peak-normalised waterfall behaviour.

    Args:
        ratio (dict): {(i,j): (n_frames, n_sc) complex}.
        denoise (bool): Apply moving-average along subcarriers.
        antialias (bool): Apply cubic-spline upsampling along frames.
    Returns:
        dict: {(i,j): (mag_db, phase)} waterfall data.
    """
    data = {}
    for (i, j), r in ratio.items():
        valid  = ~np.isnan(r.real)
        amp    = np.where(valid, np.abs(r), 1e-12)
        amp_db = 20.0 * np.log10(np.maximum(amp, 1e-12))
        ph     = np.where(valid, np.angle(r), 0.0)
        # Shift so peak valid value = 0 dB (relative scale).
        if valid.any():
            peak = float(np.max(amp_db[valid]))
            if np.isfinite(peak):
                amp_db -= peak
        if denoise:
            amp_db = dsp.denoise(amp_db)
            ph     = dsp.denoise_phase(ph)
        if antialias:
            amp_db = dsp.antialias(amp_db)
            ph     = dsp.antialias_phase(ph)
        data[(i, j)] = (amp_db, ph)
    return data


def _cpx_data(
    R_all:     np.ndarray,
    denoise:   bool = True,
    antialias: bool = True,
) -> dict:
    """Build complex-plane data_dict from VV* history.

    Args:
        R_all (np.ndarray): (n_frames, n_sc, nr, nr) complex.
        denoise (bool): Apply moving-average along subcarriers.
        antialias (bool): Apply cubic-spline upsampling along frames.
    Returns:
        dict: {(i,j): [series_array]} complex-plane data.
    """
    _, _, nr, _ = R_all.shape
    result = {}
    for i, j in _lower_tri(nr):
        elem = R_all[:, :, i, j]
        if denoise:
            elem = dsp.denoise(elem)
        if antialias:
            elem = (dsp.antialias(elem.real)
                    + 1j * dsp.antialias(elem.imag))
        result[(i, j)] = [elem]
    return result


def _ratio_cpx_data(
    ratio:     dict,
    denoise:   bool = True,
    antialias: bool = True,
) -> dict:
    """Build complex-plane data_dict from a ratio dict.

    NaN entries (denominator too small) are filled with 0+0j before
    filtering so scipy.interpolate never sees NaN inputs.  Filled
    entries cluster at the origin, distinct from valid ratio values.

    Args:
        ratio (dict): {(i,j): (n_frames, n_sc) complex}.
        denoise (bool): Apply moving-average along subcarriers.
        antialias (bool): Apply cubic-spline upsampling along frames.
    Returns:
        dict: {(i,j): [series_array]} complex-plane data.
    """
    result = {}
    for k, v in ratio.items():
        v = np.where(np.isnan(v), 0j, v)
        if denoise:
            v = dsp.denoise(v)
        if antialias:
            v = dsp.antialias(v.real) + 1j * dsp.antialias(v.imag)
        result[k] = [v]
    return result


# ------------------------------------------------------------------
# VVH_waterfall
# ------------------------------------------------------------------

def VVH_waterfall(
    store,
    interval:  int  = INTERVAL,
    window:    int  = WINDOW_LEN,
    denoise:   bool = True,
    antialias: bool = True,
    mac:       str  = '',
    **_kw,
):
    """VV* lower-triangular magnitude + phase waterfall.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac = mac or _first_mac(store)
    nr  = _meta(store, mac).get('nr', 2)
    wf  = _make_wf(nr, f'VVH Waterfall  [{mac}]', window)

    def _update(_f):
        R_all = get_vvh(store, mac)
        if R_all is None:
            return
        wf.draw(_wf_data(R_all, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(wf.fig, _update, interval=interval, blit=False)
    wf.fig.show()
    return wf.fig, ani


# ------------------------------------------------------------------
# VVH_cplx
# ------------------------------------------------------------------

def VVH_cplx(
    store,
    interval:  int  = INTERVAL,
    window:    int  = WINDOW_LEN,
    denoise:   bool = True,
    antialias: bool = True,
    mac:       str  = '',
    **_kw,
):
    """VV* lower-triangular complex-plane plot.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac = mac or _first_mac(store)
    nr  = _meta(store, mac).get('nr', 2)
    cpx = _make_cpx(nr, f'VVH Complex Plane  [{mac}]', window)

    def _update(_f):
        R_all = get_vvh(store, mac)
        if R_all is None:
            return
        cpx.set_n_sc(R_all.shape[1])
        cpx.draw(_cpx_data(
            R_all, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(cpx.fig, _update, interval=interval, blit=False)
    cpx.fig.show()
    return cpx.fig, ani


# ------------------------------------------------------------------
# VVH_ss_waterfall
# ------------------------------------------------------------------

def VVH_ss_waterfall(
    store,
    interval:  int  = INTERVAL,
    window:    int  = WINDOW_LEN,
    denoise:   bool = True,
    antialias: bool = True,
    mac:       str  = '',
    **_kw,
):
    """Per-stream VV* waterfall with spatial-stream slider.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    m      = _meta(store, mac)
    nr, nc = m.get('nr', 2), m.get('nc', 1)
    wf     = _make_wf(nr, f'VVH SS Waterfall  [{mac}]', window)

    ax_ss = wf.fig.add_axes([0.1, 0.005, 0.8, 0.018])
    ss_sl = Slider(ax_ss, 'Stream', 0, max(nc - 1, 1),
                   valinit=0, valstep=1, color='mediumseagreen')

    def _update(_f):
        s    = int(round(ss_sl.val))
        R_ss = get_vvh_ss(store, mac, s)
        if R_ss is None:
            return
        nc_ = _meta(store, mac).get('nc', 1)
        ss_sl.valmax = max(nc_ - 1, 1)
        wf.draw(_wf_data(R_ss, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(wf.fig, _update, interval=interval, blit=False)
    wf.fig.show()
    return wf.fig, ani


# ------------------------------------------------------------------
# VVH_ss_cplx
# ------------------------------------------------------------------

def VVH_ss_cplx(
    store,
    interval:  int  = INTERVAL,
    window:    int  = WINDOW_LEN,
    denoise:   bool = True,
    antialias: bool = True,
    mac:       str  = '',
    **_kw,
):
    """Per-stream VV* complex-plane plot with stream slider.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    m      = _meta(store, mac)
    nr, nc = m.get('nr', 2), m.get('nc', 1)
    cpx    = _make_cpx(nr, f'VVH SS Complex Plane  [{mac}]', window)

    ax_ss = cpx.fig.add_axes([0.1, 0.005, 0.8, 0.018])
    ss_sl = Slider(ax_ss, 'Stream', 0, max(nc - 1, 1),
                   valinit=0, valstep=1, color='mediumseagreen')

    def _update(_f):
        s    = int(round(ss_sl.val))
        R_ss = get_vvh_ss(store, mac, s)
        if R_ss is None:
            return
        nc_  = _meta(store, mac).get('nc', 1)
        ss_sl.valmax = max(nc_ - 1, 1)
        cpx.set_n_sc(R_ss.shape[1])
        cpx.draw(_cpx_data(
            R_ss, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(cpx.fig, _update, interval=interval, blit=False)
    cpx.fig.show()
    return cpx.fig, ani


# ------------------------------------------------------------------
# VVH_ratio_waterfall
# ------------------------------------------------------------------

def VVH_ratio_waterfall(
    store,
    interval:  int   = INTERVAL,
    window:    int   = WINDOW_LEN,
    denom_lim: float = DENOM_LIM,
    denoise:   bool  = True,
    antialias: bool  = True,
    mac:       str   = '',
):
    """VV* ratio waterfall (lower-tri / diagonal).

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denom_lim (float): Minimum diagonal magnitude.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    nr     = _meta(store, mac).get('nr', 2)
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]/[{j},{j}]' for i, j in pairs}
    wf = WaterfallFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        window=window,
        fig_title=f'VVH Ratio Waterfall  [{mac}]')

    def _update(_f):
        R_all = get_vvh(store, mac)
        if R_all is None:
            return
        ratio = dsp.vv_star_ratio(R_all, denom_lim)
        wf.draw(_ratio_wf_data(
            ratio, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(wf.fig, _update, interval=interval, blit=False)
    wf.fig.show()
    return wf.fig, ani


# ------------------------------------------------------------------
# VVH_ratio_cplx
# ------------------------------------------------------------------

def VVH_ratio_cplx(
    store,
    interval:  int   = INTERVAL,
    window:    int   = WINDOW_LEN,
    denom_lim: float = DENOM_LIM,
    denoise:   bool  = True,
    antialias: bool  = True,
    mac:       str   = '',
):
    """VV* ratio complex-plane plot.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denom_lim (float): Minimum diagonal magnitude.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    nr     = _meta(store, mac).get('nr', 2)
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]/[{j},{j}]' for i, j in pairs}
    cpx = ComplexPlaneFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        series_labels=['Ratio'],
        window=window,
        fig_title=f'VVH Ratio Complex Plane  [{mac}]')

    def _update(_f):
        R_all = get_vvh(store, mac)
        if R_all is None:
            return
        cpx.set_n_sc(R_all.shape[1])
        ratio = dsp.vv_star_ratio(R_all, denom_lim)
        cpx.draw(_ratio_cpx_data(
            ratio, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(cpx.fig, _update, interval=interval, blit=False)
    cpx.fig.show()
    return cpx.fig, ani


# ------------------------------------------------------------------
# VVH_ratio_ss_waterfall
# ------------------------------------------------------------------

def VVH_ratio_ss_waterfall(
    store,
    interval:  int   = INTERVAL,
    window:    int   = WINDOW_LEN,
    denom_lim: float = DENOM_LIM,
    denoise:   bool  = True,
    antialias: bool  = True,
    mac:       str   = '',
):
    """Per-stream VV* ratio waterfall with stream slider.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denom_lim (float): Minimum diagonal magnitude.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    m      = _meta(store, mac)
    nr, nc = m.get('nr', 2), m.get('nc', 1)
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]/[{j},{j}]' for i, j in pairs}
    wf = WaterfallFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        window=window,
        fig_title=f'VVH Ratio SS Waterfall  [{mac}]')

    ax_ss = wf.fig.add_axes([0.1, 0.005, 0.8, 0.018])
    ss_sl = Slider(ax_ss, 'Stream', 0, max(nc - 1, 1),
                   valinit=0, valstep=1, color='mediumseagreen')

    def _update(_f):
        s    = int(round(ss_sl.val))
        R_ss = get_vvh_ss(store, mac, s)
        if R_ss is None:
            return
        nc_  = _meta(store, mac).get('nc', 1)
        ss_sl.valmax = max(nc_ - 1, 1)
        ratio = dsp.vv_star_ratio(R_ss, denom_lim)
        wf.draw(_ratio_wf_data(
            ratio, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(wf.fig, _update, interval=interval, blit=False)
    wf.fig.show()
    return wf.fig, ani


# ------------------------------------------------------------------
# VVH_ratio_ss_cplx
# ------------------------------------------------------------------

def VVH_ratio_ss_cplx(
    store,
    interval:  int   = INTERVAL,
    window:    int   = WINDOW_LEN,
    denom_lim: float = DENOM_LIM,
    denoise:   bool  = True,
    antialias: bool  = True,
    mac:       str   = '',
):
    """Per-stream VV* ratio complex-plane plot with stream slider.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        denom_lim (float): Minimum diagonal magnitude.
        denoise (bool): Apply subcarrier denoising.
        antialias (bool): Apply frame-axis antialiasing.
        mac (str): MAC address to display; defaults to first seen.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    mac    = mac or _first_mac(store)
    m      = _meta(store, mac)
    nr, nc = m.get('nr', 2), m.get('nc', 1)
    pairs  = _lower_tri(nr)
    titles = {(i, j): f'[{i},{j}]/[{j},{j}]' for i, j in pairs}
    cpx = ComplexPlaneFigure(
        n_rows_grid=nr, n_cols_grid=nr,
        active_positions=pairs, titles=titles,
        series_labels=['Ratio'],
        window=window,
        fig_title=f'VVH Ratio SS Complex Plane  [{mac}]')

    ax_ss = cpx.fig.add_axes([0.1, 0.005, 0.8, 0.018])
    ss_sl = Slider(ax_ss, 'Stream', 0, max(nc - 1, 1),
                   valinit=0, valstep=1, color='mediumseagreen')

    def _update(_f):
        s    = int(round(ss_sl.val))
        R_ss = get_vvh_ss(store, mac, s)
        if R_ss is None:
            return
        nc_  = _meta(store, mac).get('nc', 1)
        ss_sl.valmax = max(nc_ - 1, 1)
        cpx.set_n_sc(R_ss.shape[1])
        ratio = dsp.vv_star_ratio(R_ss, denom_lim)
        cpx.draw(_ratio_cpx_data(
            ratio, denoise=denoise, antialias=antialias))

    ani = FuncAnimation(cpx.fig, _update, interval=interval, blit=False)
    cpx.fig.show()
    return cpx.fig, ani


# ------------------------------------------------------------------
# est_array_plot
# ------------------------------------------------------------------

def est_array_plot(
    store,
    interval:    int   = INTERVAL,
    window:      int   = WINDOW_LEN,
    array_shape: tuple = ARRAY_SHAPE,
    cal_every:   int   = 1,
    **_kw,
):
    """Calibrated array geometry + AoA over time.

    Left:         3-D scatter of calibrated element positions.
    Top-right:    Azimuth angle over time.
    Bottom-right: Elevation angle over time.

    Geometry is re-calibrated every cal_every animation frames.

    Args:
        store: DataStore instance.
        interval (int): Animation refresh in ms.
        window (int): Rolling window depth.
        array_shape (tuple): (rows, cols) for initial geometry.
        cal_every (int): Re-calibrate every N frames.
    Returns:
        tuple: (fig, FuncAnimation).
    """
    fig = plt.figure('Array Geometry + AoA', figsize=(12, 5))
    fig.suptitle('Estimated Array Geometry + AoA', fontsize=10)

    gs = gridspec.GridSpec(
        2, 2, figure=fig,
        width_ratios=[1, 1.5], wspace=0.35, hspace=0.5)
    ax_geo = fig.add_subplot(gs[:, 0], projection='3d')
    ax_az  = fig.add_subplot(gs[0, 1])
    ax_el  = fig.add_subplot(gs[1, 1])

    for ax, ylabel in [(ax_az, 'Az (°)'), (ax_el, 'El (°)')]:
        ax.set_xlabel('Frame', fontsize=7)
        ax.set_ylabel(ylabel, fontsize=7)
        ax.set_ylim(-90, 90)
        ax.grid(True, lw=0.3, alpha=0.3)

    state = {
        'geo':         None,
        'az':          [],
        'el':          [],
        'frame':       0,
        'cal_running': False,
    }

    def _nominal_geo(n_r, n_c):
        return rare_est.build_geometry(n_rows=n_r, n_cols=n_c)

    def _run_calibration(v_mean, init_g):
        """Background calibration — updates state['geo'] when done."""
        try:
            state['geo'] = rare_est.calibrate_array_geometry(
                v_mean, n_sources=1, init_geo=init_g)
        except Exception:
            pass
        finally:
            state['cal_running'] = False

    def _update(_f):
        mac  = _first_mac(store)
        frames = _snapshot_frames(store, mac)
        if not frames:
            return

        state['frame'] += 1
        v4d = np.stack([f['v_all'] for f in frames], axis=0)
        nf, n_sc, nr, nc = v4d.shape

        # Build a geometry consistent with the actual array size.
        # array_shape is a hint; if its element count != nr, fall back
        # to a 1×nr ULA so geometry always matches the data.
        n_r, n_c = array_shape
        if n_r * n_c != nr:
            n_r, n_c = 1, nr

        # First calibration: synchronous so we have a geometry ready.
        # Subsequent calibrations: background thread, starting from the
        # previously calibrated geometry so positions evolve over time.
        if state['geo'] is None:
            try:
                init_g = _nominal_geo(n_r, n_c)
                v_mean = v4d.mean(axis=0)
                state['geo'] = rare_est.calibrate_array_geometry(
                    v_mean, n_sources=1, init_geo=init_g)
            except Exception:
                state['geo'] = _nominal_geo(n_r, n_c)

        elif (state['frame'] % cal_every == 0
              and not state['cal_running']):
            state['cal_running'] = True
            # Use current calibration as starting point so geometry
            # can improve incrementally rather than resetting each time.
            init_g = state['geo']
            v_mean = v4d.mean(axis=0)
            t = threading.Thread(
                target=_run_calibration,
                args=(v_mean, init_g),
                daemon=True)
            t.start()

        geo = state['geo']

        # RARE-L on latest frame with calibrated geometry.
        # v4d[-1] → (n_sc, nr, nc) as required by rare_l_estimate.
        try:
            v_lat  = v4d[-1]
            result = rare_est.rare_l_estimate(
                v_lat, n_sources=1, geometry=geo)
            az = float(result['az']['doa'][0])
            el = (float(result['el']['doa'][0])
                  if result['el'] else np.nan)
        except Exception:
            az, el = np.nan, np.nan

        state['az'].append(az)
        state['el'].append(el)
        if len(state['az']) > window:
            state['az'].pop(0)
            state['el'].pop(0)

        # Array geometry subplot
        ax_geo.cla()
        ax_geo.set_title('Calibrated Array', fontsize=8)
        ax_geo.set_xlabel('x (λ)', fontsize=7)
        ax_geo.set_ylabel('y (λ)', fontsize=7)
        ax_geo.set_zlabel('z (λ)', fontsize=7)
        pos = geo.pos
        ax_geo.scatter(pos[:, 0], pos[:, 1], np.zeros(len(pos)),
                       c=range(len(pos)), cmap='viridis',
                       s=60, depthshade=False)
        for idx, (x, y) in enumerate(pos):
            ax_geo.text(x, y, 0, f' {idx}', fontsize=7)

        # AoA time-series subplots
        x = np.arange(len(state['az']))
        ax_az.cla()
        ax_az.set_title('Azimuth DoA', fontsize=8)
        ax_az.set_xlabel('Frame', fontsize=7)
        ax_az.set_ylabel('Az (°)', fontsize=7)
        ax_az.set_ylim(-90, 90)
        ax_az.grid(True, lw=0.3, alpha=0.3)
        ax_az.plot(x, state['az'], color='steelblue', lw=1.2)

        ax_el.cla()
        ax_el.set_title('Elevation DoA', fontsize=8)
        ax_el.set_xlabel('Frame', fontsize=7)
        ax_el.set_ylabel('El (°)', fontsize=7)
        ax_el.set_ylim(-90, 90)
        ax_el.grid(True, lw=0.3, alpha=0.3)
        ax_el.plot(x, state['el'], color='darkorange', lw=1.2)

        fig.canvas.draw_idle()

    ani = FuncAnimation(fig, _update, interval=interval, blit=False)
    fig.show()
    return fig, ani
