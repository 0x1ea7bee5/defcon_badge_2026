"""
dsp.py — DSP utilities for CBF/CSI data.

All functions operate on NumPy arrays and are stateless.
Input convention: v_all has shape (n_sc, nr, nc) complex.
"""

import numpy as np
from scipy.interpolate import interp1d as _interp1d

# ------------------------------------------------------------------
# Tunable defaults (override by passing explicit kwargs)
# ------------------------------------------------------------------

_DENOISE_WIN    = 3      # moving-average window width
_OVERSAMP_FACTOR = 4     # cubic-spline upsampling factor
_DENOM_LIM      = 0.01  # ratio: discard if |diagonal| < this


# ------------------------------------------------------------------
# Denoising
# ------------------------------------------------------------------

def denoise(arr: np.ndarray, win: int = _DENOISE_WIN) -> np.ndarray:
    """Apply moving-average box filter along the last axis.

    Phase-aware when arr is complex (filters on unit circle).

    Args:
        arr (np.ndarray): 1-D or 2-D real or complex array.
        win (int): Window width (no-op when <= 1).
    Returns:
        np.ndarray: Smoothed array, same shape.
    """
    if win <= 1 or arr.shape[-1] < win:
        return arr
    k = np.ones(win) / win
    if np.iscomplexobj(arr):
        return (
            np.apply_along_axis(
                lambda r: np.convolve(r, k, 'same'), -1, arr.real)
            + 1j * np.apply_along_axis(
                lambda r: np.convolve(r, k, 'same'), -1, arr.imag)
        )
    return np.apply_along_axis(
        lambda r: np.convolve(r, k, 'same'), -1, arr)


def denoise_phase(
    arr: np.ndarray, win: int = _DENOISE_WIN
) -> np.ndarray:
    """Phase-aware moving-average (avoids ±π wrap artifacts).

    Operates on the unit circle: smooth exp(j·arr), then take angle.

    Args:
        arr (np.ndarray): Phase array in radians (real).
        win (int): Window width.
    Returns:
        np.ndarray: Smoothed phase, same shape.
    """
    c = np.exp(1j * arr)
    c_smooth = denoise(c, win)
    return np.angle(c_smooth)


# ------------------------------------------------------------------
# Antialiasing / oversampling
# ------------------------------------------------------------------

def antialias(
    arr: np.ndarray, factor: int = _OVERSAMP_FACTOR
) -> np.ndarray:
    """Cubic-spline upsampling along axis 0 (time axis).

    Args:
        arr (np.ndarray): 1-D or 2-D real array.
        factor (int): Integer upsampling factor (no-op when <= 1).
    Returns:
        np.ndarray: Upsampled array ((n-1)*factor+1 rows, same cols).
    """
    if factor <= 1:
        return arr
    n = arr.shape[0]
    if n < 2:
        return arr
    x    = np.arange(n, dtype=float)
    x_up = np.linspace(0.0, n - 1, (n - 1) * factor + 1)
    kind = 'cubic' if n >= 4 else ('quadratic' if n >= 3 else 'linear')
    if arr.ndim == 1:
        return _interp1d(x, arr, kind=kind)(x_up)
    return _interp1d(x, arr, axis=0, kind=kind)(x_up)


def antialias_phase(
    arr: np.ndarray, factor: int = _OVERSAMP_FACTOR
) -> np.ndarray:
    """Phase-aware upsampling (handles ±π wrap).

    Args:
        arr (np.ndarray): Phase array in radians, 1-D or 2-D real.
        factor (int): Upsampling factor.
    Returns:
        np.ndarray: Upsampled phase.
    """
    c = np.exp(1j * arr)
    return np.angle(
        antialias(c.real, factor) + 1j * antialias(c.imag, factor))


# ------------------------------------------------------------------
# VV* computations
# ------------------------------------------------------------------

def compute_vv_star(v_all: np.ndarray) -> np.ndarray:
    """Compute VV* (outer product) for all subcarriers.

    Full matrix: mixes all spatial streams.

    Args:
        v_all (np.ndarray): (n_sc, nr, nc) complex CBF snapshots.
    Returns:
        np.ndarray: (n_sc, nr, nr) complex Hermitian matrices.
    """
    # R[k] = V[k] @ V[k].H  via einsum for clarity + speed
    return np.einsum('kia,kja->kij', v_all, v_all.conj())


def compute_vv_star_ss(v_all: np.ndarray) -> np.ndarray:
    """Compute per-stream VV* for all subcarriers.

    Separates each column (spatial stream) of V and computes
    v_c @ v_c.H independently.

    Args:
        v_all (np.ndarray): (n_sc, nr, nc) complex CBF snapshots.
    Returns:
        np.ndarray: (nc, n_sc, nr, nr) complex Hermitian matrices.
            R[c, k] = outer(V[k, :, c], V[k, :, c].conj())
    """
    nc = v_all.shape[2]
    return np.stack(
        [np.einsum('ki,kj->kij', v_all[:, :, c], v_all[:, :, c].conj())
         for c in range(nc)],
        axis=0)


# ------------------------------------------------------------------
# VV* ratio computations
# ------------------------------------------------------------------

def _lower_tri_pairs(nr: int):
    """Return list of (i, j) lower-triangular index pairs (i > j).

    Args:
        nr (int): Matrix dimension.
    Returns:
        list[tuple[int, int]]: Index pairs.
    """
    return [(i, j) for i in range(nr) for j in range(i)]


def remove_singularity(
    values:    np.ndarray,
    denom:     np.ndarray,
    denom_lim: float = _DENOM_LIM,
) -> np.ndarray:
    """Replace ratio values with NaN where the denominator is too small.

    Prevents artificially large values caused by near-zero denominators
    from distorting plots.

    Args:
        values (np.ndarray): Ratio numerator/result array (any shape).
        denom (np.ndarray): Denominator array, same shape as values.
        denom_lim (float): Minimum acceptable |denom|.
    Returns:
        np.ndarray: values with NaN wherever |denom| < denom_lim.
            Complex inputs produce complex NaN; real inputs produce real NaN.
    """
    mask = np.abs(denom) < denom_lim
    fill = np.nan + 0j if np.iscomplexobj(values) else np.nan
    return np.where(mask, fill, values)


def vv_star_ratio(
    R_all: np.ndarray, denom_lim: float = _DENOM_LIM
) -> dict:
    """Compute lower-triangular VV* ratios normalised by diagonal.

    For each pair (i, j) with i > j:
        ratio[k] = R[k, i, j] / R[k, j, j].real

    Values where |R[k, j, j].real| < denom_lim are set to NaN via
    remove_singularity().

    Args:
        R_all (np.ndarray): (n_sc, nr, nr) complex, from compute_vv_star.
        denom_lim (float): Minimum diagonal magnitude to include.
    Returns:
        dict: {(i, j): np.ndarray shape (n_sc,) complex}.
    """
    n_sc, nr, _ = R_all.shape
    result = {}
    for i, j in _lower_tri_pairs(nr):
        diag  = R_all[:, j, j].real
        ratio = R_all[:, i, j] / np.where(
            np.abs(diag) < denom_lim, 1.0, diag)
        result[(i, j)] = remove_singularity(ratio, diag, denom_lim)
    return result


def vv_star_ratio_ss(
    R_ss: np.ndarray, denom_lim: float = _DENOM_LIM
) -> dict:
    """Per-stream VV* ratios normalised by diagonal.

    Args:
        R_ss (np.ndarray): (nc, n_sc, nr, nr) from compute_vv_star_ss.
        denom_lim (float): Minimum diagonal magnitude to include.
    Returns:
        dict: {(stream, i, j): np.ndarray shape (n_sc,) complex}.
    """
    nc, n_sc, nr, _ = R_ss.shape
    result = {}
    for c in range(nc):
        for i, j in _lower_tri_pairs(nr):
            diag  = R_ss[c, :, j, j].real
            ratio = R_ss[c, :, i, j] / np.where(
                np.abs(diag) < denom_lim, 1.0, diag)
            result[(c, i, j)] = remove_singularity(
                ratio, diag, denom_lim)
    return result


# ------------------------------------------------------------------
# Smoothing pipeline (denoise → antialias)
# ------------------------------------------------------------------

def smooth_real(
    arr: np.ndarray,
    win:    int = _DENOISE_WIN,
    factor: int = _OVERSAMP_FACTOR,
) -> np.ndarray:
    """Denoise then antialias a real-valued 1-D or 2-D array.

    Args:
        arr (np.ndarray): Input real array.
        win (int): Moving-average window.
        factor (int): Upsampling factor.
    Returns:
        np.ndarray: Filtered array.
    """
    return antialias(denoise(arr, win), factor)


def smooth_phase(
    arr: np.ndarray,
    win:    int = _DENOISE_WIN,
    factor: int = _OVERSAMP_FACTOR,
) -> np.ndarray:
    """Denoise then antialias a phase array (handles ±π wrap).

    Args:
        arr (np.ndarray): Phase array in radians.
        win (int): Moving-average window.
        factor (int): Upsampling factor.
    Returns:
        np.ndarray: Filtered phase array.
    """
    return antialias_phase(denoise_phase(arr, win), factor)


def smooth_complex(
    arr: np.ndarray,
    win:    int = _DENOISE_WIN,
    factor: int = _OVERSAMP_FACTOR,
) -> np.ndarray:
    """Denoise then antialias a complex array.

    Denoises real and imaginary parts jointly via unit-circle
    averaging (phase-aware), then upsamples along axis 0.

    Args:
        arr (np.ndarray): Complex array, shape (..., n_sc) or (n, n_sc).
        win (int): Moving-average window (applied along last axis).
        factor (int): Upsampling factor (applied along axis 0).
    Returns:
        np.ndarray: Smoothed complex array.
    """
    denoised = denoise(arr, win)
    return (
        antialias(denoised.real, factor)
        + 1j * antialias(denoised.imag, factor)
    )


# ------------------------------------------------------------------
# Utility: amp/phase waterfall matrices from v_all history
# ------------------------------------------------------------------

def vv_element_waterfall(
    v_history: list, i: int, j: int
) -> tuple:
    """Extract per-frame amp (dB) and phase waterfall for V[i, j].

    Args:
        v_history (list[dict]): List of CBF frames (each has 'v_all').
        i (int): Row index into V matrix.
        j (int): Column index into V matrix.
    Returns:
        tuple: (amp_db, phase) both shape (n_frames, n_sc).
    """
    amps, phases = [], []
    for frame in v_history:
        v_all = frame['v_all']         # (n_sc, nr, nc)
        elem  = v_all[:, i, j]         # (n_sc,) complex
        amps.append(np.abs(elem))
        phases.append(np.angle(elem))
    amp_mat   = np.stack(amps, axis=0)
    phase_mat = np.stack(phases, axis=0)
    amp_db    = 20.0 * np.log10(np.maximum(amp_mat, 1e-12))
    return amp_db, phase_mat


def R_element_waterfall(
    R_history: np.ndarray, i: int, j: int
) -> tuple:
    """Extract per-frame amp (dB) and phase from stacked R matrices.

    Args:
        R_history (np.ndarray): (n_frames, n_sc, nr, nr) complex.
        i (int): Row index.
        j (int): Column index.
    Returns:
        tuple: (amp_db, phase) both shape (n_frames, n_sc).
    """
    elem      = R_history[:, :, i, j]   # (n_frames, n_sc)
    amp_db    = 20.0 * np.log10(np.maximum(np.abs(elem), 1e-12))
    return amp_db, np.angle(elem)
