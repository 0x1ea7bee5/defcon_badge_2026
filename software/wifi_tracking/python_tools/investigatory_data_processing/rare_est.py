"""
RARE-L: Root Array Response Estimation with L Subcarrier Snapshots.

Supports ULA, rectangular UPA, and arbitrary array geometries via
elevation-compensated decoupled MUSIC.

Public symbols:
  ArrayGeometry, build_geometry, build_hexagonal_geometry,
  build_l_shaped_geometry, covariance_from_cbf, noise_subspace,
  noise_projector, rare_l_polynomial, select_doa_roots,
  roots_to_doa, music_spectrum, music_spectrum_db,
  music_spectrum_arb, decoupled_doa_estimate, rare_l_estimate,
  calibrate_array_geometry
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

DEFAULT_D_LAMBDA = 0.5   # half-wavelength default spacing
_SCAN_ANGLES_DEG = np.linspace(-90.0, 90.0, 361)
_SCAN_ANGLES_RAD = np.deg2rad(_SCAN_ANGLES_DEG)


# -----------------------------------------------------------------------
# Geometry dataclass
# -----------------------------------------------------------------------

@dataclass
class ArrayGeometry:
    """Describes the physical layout of an antenna array.

    Attributes:
        pos (np.ndarray): shape (Nr, 2).  pos[i] = [x_i, y_i] in wavelengths.
        d_x (float): nominal x-axis spacing (wavelengths) — used as ULA
            spacing when running 1D Root-MUSIC on a rectangular array.
            For arbitrary arrays it sets the Nyquist band of the azimuth scan.
        d_y (float): nominal y-axis spacing (wavelengths),
            same role for elevation.
    """
    pos: np.ndarray    # (Nr, 2), wavelengths
    d_x: float = DEFAULT_D_LAMBDA
    d_y: float = DEFAULT_D_LAMBDA


def build_geometry(
    pos:    Optional[np.ndarray] = None,
    *,
    n_rows: int   = 1,
    n_cols: int   = 1,
    d_x:    float = DEFAULT_D_LAMBDA,
    d_y:    float = DEFAULT_D_LAMBDA,
) -> ArrayGeometry:
    """Construct an ArrayGeometry for rectangular or arbitrary layouts.

    Rectangular shortcut: pass n_rows, n_cols, d_x, d_y (no pos).
    Arbitrary layout:     pass pos (Nr×2 array of [x, y] in wavelengths).

    Args:
        pos:    (Nr, 2) real array of element positions in wavelengths.
                If None, a row-major rectangular grid is generated.
        n_rows: number of rows    (ignored when pos is supplied).
        n_cols: number of columns (ignored when pos is supplied).
        d_x:    column (x) spacing in wavelengths.
        d_y:    row    (y) spacing in wavelengths.

    Returns:
        ArrayGeometry
    """
    if pos is None:
        xs = np.arange(n_cols) * d_x
        ys = np.arange(n_rows) * d_y
        xg, yg = np.meshgrid(xs, ys)
        pos = np.column_stack([xg.ravel(), yg.ravel()])   # (Nr, 2), row-major
    else:
        pos = np.asarray(pos, dtype=float)
        if pos.ndim != 2 or pos.shape[1] != 2:
            raise ValueError("pos must have shape (Nr, 2).")
        if not np.all(np.isfinite(pos)):
            raise ValueError("pos contains non-finite values.")

    return ArrayGeometry(pos=pos, d_x=d_x, d_y=d_y)


def build_hexagonal_geometry(
    n_rings: int   = 2,
    d_lam:   float = DEFAULT_D_LAMBDA,
) -> ArrayGeometry:
    """Build a hexagonal close-packed array geometry.

    Uses axial coordinates (q, r). Total elements: 1 + 3·n_rings·(n_rings+1).
    Element positions in wavelengths:

        x = d_lam · (q + r/2)
        y = d_lam · (r · √3/2)

    Args:
        n_rings: number of rings around the central element.
        d_lam:   centre-to-centre element spacing in wavelengths.

    Returns:
        ArrayGeometry
    """
    positions = []
    for q in range(-n_rings, n_rings + 1):
        for r in range(-n_rings, n_rings + 1):
            if max(abs(q), abs(r), abs(-q - r)) <= n_rings:
                positions.append([d_lam * (q + r / 2.0),
                                   d_lam * (r * np.sqrt(3) / 2.0)])
    pos = np.array(sorted(positions, key=lambda p: (round(p[1], 9),
                                                     round(p[0], 9))))
    return ArrayGeometry(pos=pos, d_x=d_lam, d_y=d_lam)


def build_l_shaped_geometry(
    n_arm: int   = 4,
    d_x:   float = DEFAULT_D_LAMBDA,
    d_y:   float = DEFAULT_D_LAMBDA,
) -> ArrayGeometry:
    """Build an L-shaped array (two ULAs sharing a corner element).

    Places n_arm elements along x and n_arm elements along y, sharing
    the element at the origin.  Total elements: 2·n_arm − 1.

    Args:
        n_arm: number of elements per arm (including the shared corner).
        d_x:   x-arm element spacing in wavelengths.
        d_y:   y-arm element spacing in wavelengths.

    Returns:
        ArrayGeometry
    """
    xs  = [(i * d_x, 0.0) for i in range(n_arm)]
    ys  = [(0.0, i * d_y) for i in range(1, n_arm)]
    return ArrayGeometry(pos=np.array(xs + ys), d_x=d_x, d_y=d_y)


# -----------------------------------------------------------------------
# Shared covariance / subspace helpers (unchanged from original)
# -----------------------------------------------------------------------

def covariance_from_cbf(v_all: np.ndarray) -> np.ndarray:
    """Estimate full spatial covariance R from CBF V snapshots.

    Args:
        v_all: complex, shape (n_sc, nr, nc).

    Returns:
        np.ndarray: complex Hermitian, shape (nr, nr).
    """
    n_sc, nr, nc = v_all.shape
    R = np.zeros((nr, nr), dtype=complex)
    for k in range(n_sc):
        Vk = v_all[k]
        R += Vk @ Vk.conj().T
    return R / float(n_sc * nc)


def noise_subspace(
    R: np.ndarray,
    n_sources: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Eigendecompose R and return noise subspace and eigenvalues.

    Args:
        R:         complex Hermitian, shape (n, n).
        n_sources: number of signal sources. Clamped to n − 1.

    Returns:
        tuple: (E_n, eigenvalues)
            E_n:         noise eigenvectors, shape (n, n − n_sources).
            eigenvalues: real, descending, shape (n,).
    """
    n          = R.shape[0]
    n_sources  = min(n_sources, n - 1)
    evals, evecs = np.linalg.eigh(R)
    idx          = np.argsort(evals)[::-1]
    return evecs[:, idx][:, n_sources:], np.real(evals[idx])


def noise_projector(E_n: np.ndarray) -> np.ndarray:
    """Orthogonal projector onto noise subspace: C = E_n @ E_n^H."""
    return E_n @ E_n.conj().T


# -----------------------------------------------------------------------
# Root-MUSIC polynomial helpers (unchanged)
# -----------------------------------------------------------------------

def rare_l_polynomial(C: np.ndarray) -> np.ndarray:
    """Form Root-MUSIC polynomial coefficients from noise projector C."""
    n      = C.shape[0]
    coeffs = np.zeros(2 * n - 1, dtype=complex)
    for m in range(-(n - 1), n):
        coeffs[m + n - 1] = np.sum(np.diagonal(C, offset=m))
    return coeffs[::-1]    # highest power first for np.roots


def select_doa_roots(
    roots: np.ndarray,
    n_sources: int,
    min_angle_sep: float = 0.02,
) -> np.ndarray:
    """Select n_sources distinct roots nearest the unit circle."""
    dist     = np.abs(np.abs(roots) - 1.0)
    inside   = np.abs(roots) <= 1.0 + 1e-9
    priority = dist + (~inside) * 1e6
    order    = np.argsort(priority)

    selected, sel_angles = [], []
    for i in order:
        if len(selected) >= n_sources:
            break
        ang = np.angle(roots[i])
        if any(abs(ang - a) < min_angle_sep for a in sel_angles):
            continue
        selected.append(roots[i])
        sel_angles.append(ang)

    while len(selected) < n_sources:
        selected.append(np.nan + 0j)
    return np.array(selected)


def roots_to_doa(
    roots: np.ndarray,
    d_lambda: float = DEFAULT_D_LAMBDA,
) -> np.ndarray:
    """Convert complex roots near the unit circle to DoA angles (degrees)."""
    psi   = np.angle(roots)
    sin_t = psi / (2.0 * np.pi * d_lambda)
    valid = np.abs(sin_t) <= 1.0
    theta = np.full(len(roots), np.nan)
    theta[valid] = np.rad2deg(np.arcsin(sin_t[valid]))
    return theta


# -----------------------------------------------------------------------
# Internal ULA pipeline helper (unchanged)
# -----------------------------------------------------------------------

def _run_1d_pipeline(
    R_sub: np.ndarray,
    n_src: int,
    d_lam: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Run the 1D Root-MUSIC pipeline on a ULA covariance sub-matrix.

    Args:
        R_sub: complex Hermitian, shape (n, n). Must be a ULA covariance
               (uniform element spacing d_lam).
        n_src: number of sources.
        d_lam: element spacing in wavelengths.

    Returns:
        tuple: (doa_deg, roots, E_n, eigenvalues)
    """
    n = R_sub.shape[0]
    if n < 2:
        return (np.full(n_src, np.nan), np.full(n_src, np.nan + 0j),
                np.zeros((n, 1)), np.array([0.0]))

    E_n, eigs = noise_subspace(R_sub, n_src)
    C         = noise_projector(E_n)
    poly      = rare_l_polynomial(C)
    roots     = np.roots(poly)
    sel       = select_doa_roots(roots, n_src)
    doa       = roots_to_doa(sel, d_lam)
    return doa, sel, E_n, eigs


# -----------------------------------------------------------------------
# 1D MUSIC spectrum helpers (ULA, unchanged)
# -----------------------------------------------------------------------

def music_spectrum(
    E_n: np.ndarray,
    d_lambda: float = DEFAULT_D_LAMBDA,
) -> np.ndarray:
    """MUSIC pseudospectrum over the _SCAN_ANGLES_DEG grid (ULA).

    Args:
        E_n:      noise eigenvectors, shape (n, n − n_sources).
        d_lambda: inter-element spacing in wavelengths.

    Returns:
        np.ndarray: real, shape (361,).
    """
    n    = E_n.shape[0]
    C    = noise_projector(E_n)
    spec = np.zeros(len(_SCAN_ANGLES_RAD))
    for i, theta in enumerate(_SCAN_ANGLES_RAD):
        psi     = 2.0 * np.pi * d_lambda * np.sin(theta)
        a       = np.exp(1j * psi * np.arange(n))
        spec[i] = 1.0 / max(abs(np.real(a.conj() @ C @ a)), 1e-12)
    return spec


def music_spectrum_db(
    E_n: np.ndarray,
    d_lambda: float = DEFAULT_D_LAMBDA,
) -> np.ndarray:
    """MUSIC pseudospectrum (ULA), normalised to 0 dB at peak."""
    s    = music_spectrum(E_n, d_lambda)
    s_db = 10.0 * np.log10(np.maximum(s, 1e-12))
    return s_db - s_db.max()


# -----------------------------------------------------------------------
# Arbitrary-geometry MUSIC spectrum (new)
# -----------------------------------------------------------------------

def music_spectrum_arb(
    C_comp: np.ndarray,
    pos_x:  np.ndarray,
    el_rad: float,
    n_pts:  int = 361,
) -> np.ndarray:
    """1D MUSIC azimuth spectrum for an arbitrary array at fixed elevation.

    Uses the elevation-compensated noise projector C_comp (built from
    R_comp = R · phase_compensation(el)) and the x-only steering vector:

        a_x(az)[i] = exp(j 2π x_i sin(az) cos(el))

    This function is called inside decoupled_doa_estimate and can also
    be used standalone for visualisation.

    Args:
        C_comp:  noise projector of elevation-compensated covariance,
                 shape (Nr, Nr).
        pos_x:   x coordinates of elements in wavelengths, shape (Nr,).
        el_rad:  candidate elevation angle in radians.
        n_pts:   number of azimuth scan points (default 361 → 0.5° steps).

    Returns:
        np.ndarray: real pseudospectrum, shape (n_pts,). NOT in dB.
    """
    az_scan = np.linspace(-np.pi / 2, np.pi / 2, n_pts)
    cos_el  = np.cos(el_rad)
    spec    = np.zeros(n_pts)
    for i, az in enumerate(az_scan):
        a_x     = np.exp(1j * 2.0 * np.pi * pos_x * np.sin(az) * cos_el)
        denom   = np.real(a_x.conj() @ C_comp @ a_x)
        spec[i] = 1.0 / max(abs(denom), 1e-12)
    return spec


# -----------------------------------------------------------------------
# Core fix: elevation-compensated decoupled MUSIC (new)
# -----------------------------------------------------------------------

def decoupled_doa_estimate(
    R:         np.ndarray,
    geo:       ArrayGeometry,
    n_sources: int,
    n_el_scan: int = 91,
    n_az_fine: int = 361,
) -> dict:
    """Estimate 2D DoA for an arbitrary array via decoupled MUSIC.

    Algorithm
    ---------
    1. Scan a coarse elevation grid el_1..el_K (default 91 points, 2° steps).
    2. For each candidate elevation el_k:
       a. Phase-compensate R to remove elevation contribution:
              R_comp[i,j] = R[i,j] · exp(-j 2π (y_i − y_j) sin(el_k))
          After compensation, R_comp depends only on x-coordinate differences
          for a source at elevation el_k, making it behave like a 1D (x-only)
          covariance.
       b. Eigendecompose R_comp to get the elevation-compensated noise
          projector C_comp.
       c. Compute the 1D azimuth MUSIC spectrum P(az | el_k) using x-only
          steering vectors. Score = peak value of P.
    3. Select el* = argmax_k score(el_k).
    4. Re-run fine azimuth estimation at el* with n_az_fine points.
    5. Elevation is refined symmetrically: fix az* and scan y-only steering
       against the y-phase compensated covariance.

    Why this is correct for arbitrary arrays
    ----------------------------------------
    For any source at (az, el) the phase of element i in R is:

        phi_i = 2π (x_i sin(az) cos(el) + y_i sin(el))

    After subtracting the y-contribution using the true el, R_comp has phase:

        phi_i^comp = 2π x_i sin(az) cos(el)

    which is exactly the phase of an x-only 1D array. The noise subspace
    of R_comp is therefore orthogonal to the x-only steering vector at the
    true azimuth — regardless of how x_i are distributed. MUSIC finds the
    true azimuth by searching for this orthogonality.

    When el_k ≠ el_true, R_comp retains residual y-phase that blurs the
    azimuth spectrum, giving a lower peak score. The coarse scan finds the
    elevation where the residual is smallest (i.e., the true elevation).

    Args:
        R:         full spatial covariance, shape (Nr, Nr).
        geo:       ArrayGeometry with element positions.
        n_sources: number of sources to estimate.
        n_el_scan: number of coarse elevation scan points (default 91).
        n_az_fine: number of azimuth scan points for final estimate
            (default 361).

    Returns:
        dict:
            az_doa (np.ndarray): azimuth estimates in degrees,
                shape (n_sources,).
            el_doa (np.ndarray): elevation estimates in degrees,
                shape (n_sources,).
            az_spectrum_db (np.ndarray): azimuth MUSIC spectrum at
                best el, dB, shape (n_az_fine,).
            el_spectrum_db (np.ndarray): elevation MUSIC spectrum at
                best az, dB, shape (n_el_scan,).
            az_scan_deg (np.ndarray): azimuth scan angles (degrees),
                shape (n_az_fine,).
            el_scan_deg (np.ndarray): elevation scan angles (degrees),
                shape (n_el_scan,).
    """
    pos   = geo.pos
    x     = pos[:, 0]
    y     = pos[:, 1]
    Nr    = len(x)
    n_src = max(1, min(n_sources, Nr - 1))

    # Precompute y-difference matrix (Nr × Nr): dy[i,j] = y_i - y_j
    dy = y[:, None] - y[None, :]

    # ---- Coarse elevation scan -----------------------------------------
    el_scan_rad = np.linspace(-np.pi / 2, np.pi / 2, n_el_scan)
    el_scores   = np.zeros(n_el_scan)
    el_results  = []   # store (R_comp, E_n, C) per el for the winner

    for ki, el in enumerate(el_scan_rad):
        R_comp    = _compensate_elevation(R, dy, el)
        E_n, _    = noise_subspace(R_comp, n_src)
        C_comp    = noise_projector(E_n)
        spec_az   = music_spectrum_arb(C_comp, x, el, n_pts=45)   # coarse az
        el_scores[ki] = spec_az.max()
        el_results.append((R_comp, E_n, C_comp))

    # ---- Fine azimuth at best elevation --------------------------------
    best_el_idx              = int(np.argmax(el_scores))
    best_el_rad              = el_scan_rad[best_el_idx]
    R_comp_best, _, C_best   = el_results[best_el_idx]

    spec_az_fine  = music_spectrum_arb(C_best, x, best_el_rad, n_pts=n_az_fine)
    az_scan_rad   = np.linspace(-np.pi / 2, np.pi / 2, n_az_fine)

    # Extract the top n_src azimuth peaks
    az_doa_rad = _peak_angles(spec_az_fine, az_scan_rad, n_src)

    # ---- Elevation refinement: fix az*, scan y-only -------------------
    dx = x[:, None] - x[None, :]
    best_az_rad = az_doa_rad[0] if not np.isnan(az_doa_rad[0]) else 0.0

    el_spec_fine = np.zeros(n_el_scan)
    for ki, el in enumerate(el_scan_rad):
        # Compensate x-contribution at the estimated azimuth
        R_el_comp = R * np.exp(-1j * 2.0 * np.pi * dx
                               * np.sin(best_az_rad) * np.cos(el))
        R_el_comp = (R_el_comp + R_el_comp.conj().T) / 2.0
        E_n_el, _ = noise_subspace(R_el_comp, n_src)
        C_el      = noise_projector(E_n_el)
        a_y       = np.exp(1j * 2.0 * np.pi * y * np.sin(el))
        denom     = np.real(a_y.conj() @ C_el @ a_y)
        el_spec_fine[ki] = 1.0 / max(abs(denom), 1e-12)

    el_doa_rad = _peak_angles(el_spec_fine, el_scan_rad, n_src)

    # ---- dB-normalised spectra for output ------------------------------
    def _to_db(s):
        s_db = 10.0 * np.log10(np.maximum(s, 1e-12))
        return s_db - s_db.max()

    return {
        'az_doa':         np.rad2deg(az_doa_rad),
        'el_doa':         np.rad2deg(el_doa_rad),
        'az_spectrum_db': _to_db(spec_az_fine),
        'el_spectrum_db': _to_db(el_spec_fine),
        'az_scan_deg':    np.rad2deg(az_scan_rad),
        'el_scan_deg':    np.rad2deg(el_scan_rad),
    }


def _compensate_elevation(
    R:   np.ndarray,
    dy:  np.ndarray,
    el:  float,
) -> np.ndarray:
    """Remove elevation phase contribution from R element-wise.

    Args:
        R:  full covariance, shape (Nr, Nr).
        dy: y-difference matrix (y_i - y_j), shape (Nr, Nr).
        el: elevation angle in radians.

    Returns:
        np.ndarray: Hermitian compensated covariance, shape (Nr, Nr).
    """
    R_comp = R * np.exp(-1j * 2.0 * np.pi * dy * np.sin(el))
    return (R_comp + R_comp.conj().T) / 2.0


def _peak_angles(
    spec:     np.ndarray,
    scan_rad: np.ndarray,
    n_peaks:  int,
    min_sep:  float = 0.035,   # radians, ~2°
) -> np.ndarray:
    """Extract the top n_peaks angles from a MUSIC pseudospectrum.

    Args:
        spec:     pseudospectrum values, shape (n_pts,).
        scan_rad: scan angles in radians, shape (n_pts,).
        n_peaks:  number of peaks to find.
        min_sep:  minimum angular separation between peaks (radians).

    Returns:
        np.ndarray: peak angles in radians, shape (n_peaks,). NaN-padded.
    """
    order    = np.argsort(spec)[::-1]
    selected, sel_angles = [], []
    for idx in order:
        if len(selected) >= n_peaks:
            break
        ang = scan_rad[idx]
        if any(abs(ang - a) < min_sep for a in sel_angles):
            continue
        selected.append(ang)
        sel_angles.append(ang)
    while len(selected) < n_peaks:
        selected.append(np.nan)
    return np.array(selected)


# -----------------------------------------------------------------------
# Original block-averaging UPA sub-covariances (kept for reference)
# These are only exact for rectangular UPA layouts.
# -----------------------------------------------------------------------

def upa_row_covariance(R: np.ndarray, n_cols: int) -> np.ndarray:
    """Extract N×N azimuth sub-covariance (rectangular UPA only).

    Averages the M block-diagonal N×N blocks of R. Exact only when the
    array is a rectangular UPA with row-major element ordering.

    Args:
        R:      complex Hermitian, shape (M*N, M*N).
        n_cols: N, number of columns.

    Returns:
        np.ndarray: complex Hermitian, shape (N, N).
    """
    nr     = R.shape[0]
    n_rows = nr // n_cols
    R_row  = np.zeros((n_cols, n_cols), dtype=complex)
    for m in range(n_rows):
        sl = slice(m * n_cols, (m + 1) * n_cols)
        R_row += R[sl, sl]
    return R_row / n_rows


def upa_col_covariance(R: np.ndarray, n_rows: int, n_cols: int) -> np.ndarray:
    """Extract M×M elevation sub-covariance (rectangular UPA only).

    Averages N strided M×M sub-matrices of R. Exact only for rectangular UPA.

    Args:
        R:      complex Hermitian, shape (M*N, M*N).
        n_rows: M, number of rows.
        n_cols: N, number of columns.

    Returns:
        np.ndarray: complex Hermitian, shape (M, M).
    """
    R_col = np.zeros((n_rows, n_rows), dtype=complex)
    for n in range(n_cols):
        idx = np.arange(n_rows) * n_cols + n
        R_col += R[np.ix_(idx, idx)]
    return R_col / n_cols


# -----------------------------------------------------------------------
# Full 2D MUSIC spectrum — works for arbitrary layouts
# -----------------------------------------------------------------------

def music_spectrum_2d(
    C_full:      np.ndarray,
    geo:         ArrayGeometry,
    n_az:        int = 91,
    n_el:        int = 91,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Full 2D MUSIC pseudospectrum for an arbitrary array (expensive).

    Scans an (n_az × n_el) angular grid. The steering vector uses the
    actual element positions — no Kronecker factorisation assumed:

        a(az, el)[i] = exp(j 2π (x_i sin(az) cos(el) + y_i sin(el)))

    Args:
        C_full: full noise projector, shape (Nr, Nr).
        geo:    ArrayGeometry with element positions in wavelengths.
        n_az:   number of azimuth scan points.
        n_el:   number of elevation scan points.

    Returns:
        tuple: (spec_db, az_deg, el_deg)
            spec_db (np.ndarray): dB, shape (n_el, n_az), 0 dB at peak.
            az_deg  (np.ndarray): shape (n_az,).
            el_deg  (np.ndarray): shape (n_el,).
    """
    x      = geo.pos[:, 0]
    y      = geo.pos[:, 1]
    az_deg = np.linspace(-90.0, 90.0, n_az)
    el_deg = np.linspace(-90.0, 90.0, n_el)
    az_rad = np.deg2rad(az_deg)
    el_rad = np.deg2rad(el_deg)
    spec   = np.zeros((n_el, n_az))

    for ei, el in enumerate(el_rad):
        y_phase = 2.0 * np.pi * y * np.sin(el)
        cos_el  = np.cos(el)
        for ai, az in enumerate(az_rad):
            phase        = y_phase + 2.0 * np.pi * x * np.sin(az) * cos_el
            a            = np.exp(1j * phase)
            denom        = np.real(a.conj() @ C_full @ a)
            spec[ei, ai] = 1.0 / max(abs(denom), 1e-12)

    spec_db  = 10.0 * np.log10(np.maximum(spec, 1e-12))
    spec_db -= spec_db.max()
    return spec_db, az_deg, el_deg


# -----------------------------------------------------------------------
# Unified top-level estimator
# -----------------------------------------------------------------------

def rare_l_estimate(
    v_all:       np.ndarray,
    n_sources:   int = 1,
    d_x:         float = DEFAULT_D_LAMBDA,
    d_y:         float = DEFAULT_D_LAMBDA,
    array_shape: Optional[Tuple[int, int]] = None,
    geometry:    Optional[ArrayGeometry]   = None,
    n_el_scan:   int = 91,
) -> dict:
    """Run RARE-L for ULA, rectangular UPA, or arbitrary array geometry.

    Geometry precedence (highest to lowest):
      1. geometry= (ArrayGeometry) — used as-is for the decoupled MUSIC path.
      2. array_shape=(M, N) — builds a rectangular geometry,
         uses decoupled MUSIC.
      3. Neither — ULA, runs the original 1D Root-MUSIC pipeline.

    Note: the decoupled MUSIC path (cases 1 and 2) is always used for 2D
    arrays, including rectangular UPAs. The old block-averaging path is
    retained in upa_row_covariance / upa_col_covariance for reference but
    is not called here. This ensures consistent behaviour across array types.

    Args:
        v_all:       complex, shape (n_sc, nr, nc).
        n_sources:   number of sources to estimate.
        d_x:         x-axis element spacing in wavelengths (rectangular UPA
                     or virtual scan Nyquist band for arbitrary arrays).
        d_y:         y-axis element spacing in wavelengths.
        array_shape: (M_rows, N_cols) for rectangular UPA (legacy shortcut).
        geometry:    ArrayGeometry for arbitrary layouts.
        n_el_scan:   number of coarse elevation scan points in decoupled MUSIC.

    Returns:
        dict:
            mode (str):            'ula', 'upa', or 'arbitrary'.
            eigenvalues (np.ndarray): descending eigenvalues of full R.
            n_sources (int):       n_sources used.
            az (dict):             DoA results for azimuth axis.
                doa (np.ndarray):        degrees, shape (n_sources,).
                spectrum_db (np.ndarray): MUSIC spectrum,
                    shape (361 or n_el_scan,).
                scan_deg (np.ndarray):   scan angles for the spectrum.
            el (dict or None):     same structure for elevation. None if ULA.
    """
    nr = v_all.shape[1]
    R  = covariance_from_cbf(v_all)

    # ---- ULA path -------------------------------------------------------
    if geometry is None and array_shape is None:
        n_src = max(1, min(n_sources, nr - 1))
        az_doa, az_z, E_n_az, eigs = _run_1d_pipeline(R, n_src, d_x)
        az_spec = music_spectrum_db(E_n_az, d_x)
        return {
            'mode':        'ula',
            'eigenvalues': eigs,
            'n_sources':   n_src,
            'az': {
                'doa':         az_doa,
                'roots':       az_z,
                'spectrum_db': az_spec,
                'scan_deg':    _SCAN_ANGLES_DEG,
            },
            'el': None,
        }

    # ---- Build geometry if not provided explicitly ----------------------
    if geometry is None:
        M, N     = array_shape
        geometry = build_geometry(n_rows=M, n_cols=N, d_x=d_x, d_y=d_y)
        mode     = 'upa'
    else:
        mode = 'arbitrary'

    if geometry.pos.shape[0] != nr:
        raise ValueError(
            f"geometry has {geometry.pos.shape[0]} elements but v_all has "
            f"nr={nr}. Ensure geometry.pos rows match the array element count."
        )

    # ---- Decoupled MUSIC ------------------------------------------------
    result = decoupled_doa_estimate(
        R, geometry, n_sources, n_el_scan=n_el_scan)

    _, eigs_full = noise_subspace(R, max(1, min(n_sources, nr - 1)))

    return {
        'mode':        mode,
        'eigenvalues': eigs_full,
        'n_sources':   n_sources,
        'az': {
            'doa':         result['az_doa'],
            'spectrum_db': result['az_spectrum_db'],
            'scan_deg':    result['az_scan_deg'],
        },
        'el': {
            'doa':         result['el_doa'],
            'spectrum_db': result['el_spectrum_db'],
            'scan_deg':    result['el_scan_deg'],
        },
    }


# -----------------------------------------------------------------------
# Self-test
# -----------------------------------------------------------------------

def _self_test():
    """Smoke-test ULA, rectangular UPA, hexagonal, and L-shaped arrays."""
    rng = np.random.default_rng(42)

    def _make_signal(pos, az_true, el_true, n_sc=16, n_snap=8, snr=20.0):
        nr   = pos.shape[0]
        x, y = pos[:, 0], pos[:, 1]
        sigs = np.zeros((n_sc, nr, n_snap), dtype=complex)
        for az, el in zip(az_true, el_true):
            az_r, el_r = np.deg2rad(az), np.deg2rad(el)
            phase = x * np.sin(az_r) * np.cos(el_r) + y * np.sin(el_r)
            a     = np.exp(1j * 2 * np.pi * phase)
            for k in range(n_sc):
                s = (rng.standard_normal(n_snap)
                     + 1j * rng.standard_normal(n_snap))
                sigs[k] += np.outer(a, s) / np.sqrt(2)
        noise_std = 10 ** (-snr / 20.0)
        sigs += noise_std * (rng.standard_normal(sigs.shape)
                             + 1j * rng.standard_normal(sigs.shape)
                             ) / np.sqrt(2)
        return sigs

    tol = 2.0   # degrees

    # ULA
    geo = build_geometry(n_rows=1, n_cols=8)
    v   = _make_signal(geo.pos, [20.0], [0.0])
    out = rare_l_estimate(v, n_sources=1)
    az  = out['az']['doa'][0]
    ok  = abs(az - 20.0) < tol
    print(f"[ULA]  az={az:.2f}°  (true 20°)  {'OK' if ok else 'FAIL'}")

    # Rectangular UPA
    geo = build_geometry(n_rows=4, n_cols=4)
    v   = _make_signal(geo.pos, [15.0], [10.0])
    out = rare_l_estimate(v, n_sources=1, array_shape=(4, 4))
    az, el = out['az']['doa'][0], out['el']['doa'][0]
    ok  = abs(az - 15.0) < tol and abs(el - 10.0) < tol
    print(f"[UPA]  az={az:.2f}°  el={el:.2f}°  (true 15°, 10°)"
          f"  {'OK' if ok else 'FAIL'}")

    # Hexagonal
    geo = build_hexagonal_geometry(n_rings=2)
    v   = _make_signal(geo.pos, [15.0], [10.0])
    out = rare_l_estimate(v, n_sources=1, geometry=geo)
    az, el = out['az']['doa'][0], out['el']['doa'][0]
    ok  = abs(az - 15.0) < tol and abs(el - 10.0) < tol
    print(f"[HEX]  az={az:.2f}°  el={el:.2f}°  (true 15°, 10°)  "
          f"Nr={geo.pos.shape[0]}  {'OK' if ok else 'FAIL'}")

    # L-shaped
    geo = build_l_shaped_geometry(n_arm=6)
    v   = _make_signal(geo.pos, [15.0], [10.0])
    out = rare_l_estimate(v, n_sources=1, geometry=geo)
    az, el = out['az']['doa'][0], out['el']['doa'][0]
    ok  = abs(az - 15.0) < tol and abs(el - 10.0) < tol
    print(f"[L]    az={az:.2f}°  el={el:.2f}°  (true 15°, 10°)  "
          f"Nr={geo.pos.shape[0]}  {'OK' if ok else 'FAIL'}")

    # Two sources on hex array (tests peak-finding)
    geo = build_hexagonal_geometry(n_rings=3)
    v   = _make_signal(geo.pos, [20.0, -10.0], [5.0, 15.0], snr=25.0)
    out = rare_l_estimate(v, n_sources=2, geometry=geo)
    print(f"[HEX2] az={np.round(out['az']['doa'],1)}°  "
          f"el={np.round(out['el']['doa'],1)}°  "
          f"(true az=[20,-10]°, el=[5,15]°)  Nr={geo.pos.shape[0]}")


if __name__ == "__main__":
    _self_test()


# -----------------------------------------------------------------------
# Array self-calibration
# -----------------------------------------------------------------------

def calibrate_array_geometry(
    v_all:     np.ndarray,
    n_sources: int = 1,
    init_geo:  Optional[ArrayGeometry] = None,
    n_rows:    int = 1,
    n_cols:    int = 1,
    max_iter:  int = 200,
) -> ArrayGeometry:
    """Estimate array geometry via subspace self-calibration.

    Minimises the noise-subspace projection residual over element
    positions, with element 0 fixed at the origin as reference:

        min_{pos}  Σ_k || P_noise · a(az_k, el_k, pos) ||²

    Initial DoA estimates az_k, el_k are obtained from decoupled
    MUSIC using init_geo (or a rectangular grid if not supplied).
    The noise projector P_noise is computed once from the full
    covariance R and held fixed during optimisation.

    Args:
        v_all (np.ndarray): (n_sc, nr, nc) complex CBF snapshots.
        n_sources (int): Number of sources.
        init_geo (ArrayGeometry): Starting geometry. Built from
            n_rows/n_cols when None.
        n_rows (int): Rows for rectangular initial geometry.
        n_cols (int): Cols for rectangular initial geometry.
        max_iter (int): Optimiser iteration limit.
    Returns:
        ArrayGeometry: Calibrated element positions.
    """
    from scipy.optimize import minimize

    nr    = v_all.shape[1]
    R     = covariance_from_cbf(v_all)
    n_src = max(1, min(n_sources, nr - 1))
    E_n, _  = noise_subspace(R, n_src)
    P_n     = noise_projector(E_n)

    if init_geo is None:
        init_geo = build_geometry(n_rows=n_rows, n_cols=n_cols)

    # Initial DoA estimates
    res0   = decoupled_doa_estimate(R, init_geo, n_src)
    az_rad = np.deg2rad(res0['az_doa'])
    el_rad = np.deg2rad(res0['el_doa'])
    valid  = ~(np.isnan(az_rad) | np.isnan(el_rad))
    az_rad = az_rad[valid]
    el_rad = el_rad[valid]

    def _residual(free_pos_flat: np.ndarray) -> float:
        # Element 0 fixed at origin; optimise elements 1..nr-1.
        pos = np.zeros((nr, 2))
        pos[1:] = free_pos_flat.reshape(nr - 1, 2)
        total   = 0.0
        for az, el in zip(az_rad, el_rad):
            phase = (
                pos[:, 0] * np.sin(az) * np.cos(el)
                + pos[:, 1] * np.sin(el)
            )
            a     = np.exp(1j * 2.0 * np.pi * phase)
            proj  = P_n @ a
            total += float(np.real(proj.conj() @ proj))
        return total

    x0  = init_geo.pos[1:].ravel()
    opt = minimize(
        _residual, x0, method='L-BFGS-B',
        options={'maxiter': max_iter, 'ftol': 1e-10})

    pos_cal       = np.zeros((nr, 2))
    pos_cal[1:]   = opt.x.reshape(nr - 1, 2)
    return ArrayGeometry(
        pos=pos_cal, d_x=init_geo.d_x, d_y=init_geo.d_y)
