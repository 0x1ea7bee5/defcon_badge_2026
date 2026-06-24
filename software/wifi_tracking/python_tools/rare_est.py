"""
RARE-L: Root Array Response Estimation with L Subcarrier Snapshots
====================================================================

Overview
--------
RARE-L is a subspace-based Direction of Arrival (DoA) estimator adapted
for OFDM WiFi systems. It extends Root-MUSIC by exploiting L subcarrier
frequency-domain snapshots to build a robust spatial covariance matrix.

This module supports two array geometries:
  - Uniform Linear Array  (ULA):  elements in a line (1D)
  - Uniform Planar Array  (UPA):  elements in an M×N rectangular grid (2D)

ULA model (original)
--------------------
An Nr-element ULA with inter-element spacing d_x (wavelengths) receives d
plane-wave signals from azimuth angles theta_1..theta_d (broadside = 0°).
Steering vector:

    a(theta) = [1, z, z^2, ..., z^{Nr-1}]^T,   z = exp(j*2*pi*d_x*sin(theta))

UPA model (extension)
---------------------
An M-row × N-column rectangular grid with row spacing d_y and column
spacing d_x (both in wavelengths). Element at row m, column n has linear
index i = m*N + n (row-major). The 2D steering vector is a Kronecker product:

    a(az, el) = a_col(v) ⊗ a_row(u)           (M*N × 1)

where:
    u = d_x * sin(az) * cos(el)   — column (azimuth)  spatial frequency
    v = d_y * sin(el)             — row    (elevation) spatial frequency
    a_row(u) = [1, e^{j2πu}, ..., e^{j2π(N-1)u}]^T   (N × 1)
    a_col(v) = [1, e^{j2πv}, ..., e^{j2π(M-1)v}]^T   (M × 1)

Elevation el is measured from the x-y plane (positive = up). Azimuth az
is measured in the x-y plane from the x-axis (the column direction).

Algorithm — ULA path (Steps 1–6, unchanged)
-------------------------------------------
Step 1  Covariance:   R = (1/L·Nc) sum_k V_k @ V_k^H          (Nr × Nr)
Step 2  Eigen:        R = E Λ E^H, sorted descending
Step 3  Projector:    C = E_n @ E_n^H                          (noise subspace)
Step 4  Polynomial:   P(z) = z^{Nr-1} * sum_m c_m z^m,  c_m = tr-diag(C, m)
Step 5  Root select:  pick n_sources roots nearest |z|=1, deduplicated by angle
Step 6  DoA:          theta = arcsin(angle(z) / (2*pi*d_x))

Algorithm — UPA path (separable extension)
------------------------------------------
Step 1  Covariance:   Same full (Nr×Nr) covariance R.

Step 2  Marginalise:  Extract two sub-covariances from R by summing
                      block-diagonal entries:

    R_row  (N×N) — "azimuth covariance", averages over the M row blocks:
        R_row[n1, n2] = (1/M) sum_{m=0}^{M-1} R[m·N+n1, m·N+n2]

    R_col  (M×M) — "elevation covariance", averages over the N column strides:
        R_col[m1, m2] = (1/N) sum_{n=0}^{N-1} R[m1·N+n, m2·N+n]

    These are equivalent to treating the row elements as an N-element ULA
    (observed M times) and the column elements as an M-element ULA (observed
    N times). Both marginal covariances are Hermitian PSD.

Step 3  1D Root-MUSIC on each marginal covariance (reusing ULA Steps 3–6):
    From R_row: estimate u = d_x sin(az) cos(el) per source
    From R_col: estimate v = d_y sin(el) per source

Step 4  Angle recovery:
    el_deg = arcsin(v / d_y)
    az_deg = arcsin(u / (d_x * cos(el_rad)))   [corrected for elevation]

    Correction: the row covariance collapses the M row-pairs into a spatial
    frequency u that includes the cos(el) projection. Without correction,
    az_apparent = arcsin(u/d_x) underestimates true azimuth at non-zero
    elevation. Dividing by cos(el_rad) recovers true azimuth, clamped to
    ±90° if the argument exceeds ±1 (unphysical at large el).

Step 5  Spectra:
    Azimuth spectrum: 1D MUSIC on E_n_row over scan angles (-90°..+90°)
    Elevation spectrum: 1D MUSIC on E_n_col over scan angles (-90°..+90°)
    Full 2D MUSIC (optional, expensive): scan an (n_az × n_el) grid using
        the full MN-element noise projector and the Kronecker steering vector.

Changes from ULA-only version
-------------------------------
1. upa_row_covariance(R, n_cols) [NEW]
   Extracts the N×N azimuth sub-covariance by summing the M block-diagonal
   entries of R.

2. upa_col_covariance(R, n_rows, n_cols) [NEW]
   Extracts the M×M elevation sub-covariance by summing N strided diagonals.

3. _run_1d_pipeline(R_sub, n_src, d_lam) [NEW, internal]
   Refactors the ULA Root-MUSIC steps into one helper so the azimuth and
   elevation passes share identical code.

4. rare_l_estimate() — updated signature and return value
   - d_lambda parameter replaced by d_x and d_y (one spacing per axis).
   - array_shape=(M, N) added; None means ULA (same as (1, Nr)).
   - Return type changed from a (doa, roots, eigenvalues) tuple to a dict:
       {mode, eigenvalues, n_sources, az, el}
     where az and el are sub-dicts {doa, roots, spectrum_db}
     and el is None in ULA mode.

5. music_spectrum_upa(E_n_az, E_n_el, d_x, d_y) [NEW]
   Returns two separable 1D spectra (azimuth + elevation) for real-time use.

6. music_spectrum_2d(C_full, array_shape, d_x, d_y, n_az, n_el) [NEW]
   Full 2D MUSIC by scanning an (n_az × n_el) grid. Uses the complete
   noise projector and Kronecker UPA steering vector. Expensive; not
   called in the real-time ingestion path.

Limitations
-----------
- UPA assumes rectangular (orthogonal) layout, equal spacing per axis.
- Mutual coupling and per-element phase errors are not modelled.
- The separable sub-covariances are exact only when the UPA steering vector
  factors as a Kronecker product, which holds for a planar array with
  independent row/column responses.
- Source pairing (which azimuth root belongs to which elevation root) is
  not performed; sources are returned independently per axis.
- Nr >= d + 1 (at least one noise eigenvector) is required for each
  marginal (N >= d+1 for azimuth, M >= d+1 for elevation).

References
----------
- Barabell, A. J. (1983). ICASSP. (Root-MUSIC)
- Rao, B. D. & Hari, K. V. S. (1989). IEEE Trans. ASSP, 37(12). (Root-MUSIC)
- Schmidt, R. O. (1986). IEEE Trans. Ant. Propag., 34(3). (MUSIC)
- Zoltowski, M. D. et al. (1993). "Closed-form 2-D angle estimation with
  rectangular arrays in element space or beamspace via unitary ESPRIT."
  IEEE Trans. SP, 44(2):316-328. (separable UPA subspace methods)
- Kotaru, M. et al. (2015). ACM SIGCOMM. (subcarrier covariance for WiFi)
"""

import numpy as np

DEFAULT_D_LAMBDA = 0.5   # default inter-element spacing (wavelengths), both axes

# Scan grid for 1D MUSIC pseudospectrum, degrees.
_SCAN_ANGLES_DEG = np.linspace(-90.0, 90.0, 361)
_SCAN_ANGLES_RAD = np.deg2rad(_SCAN_ANGLES_DEG)


# -----------------------------------------------------------------------
# Shared covariance / subspace helpers (ULA and UPA)
# -----------------------------------------------------------------------

def covariance_from_cbf(v_all):
    """Estimate full spatial covariance R from CBF V snapshots.

    Args:
        v_all (np.ndarray): complex, shape (n_sc, nr, nc).

    Returns:
        np.ndarray: complex Hermitian, shape (nr, nr).
    """
    n_sc, nr, nc = v_all.shape
    R = np.zeros((nr, nr), dtype=complex)
    for k in range(n_sc):
        Vk = v_all[k]
        R += Vk @ Vk.conj().T
    R /= float(n_sc * nc)
    return R


def noise_subspace(R, n_sources):
    """Eigendecompose R and return noise subspace and eigenvalues.

    Args:
        R (np.ndarray): complex Hermitian, shape (n, n).
        n_sources (int): number of signal sources d. Clamped to n-1.

    Returns:
        tuple: (E_n, eigenvalues)
            E_n (np.ndarray): noise eigenvectors, shape (n, n - n_sources).
            eigenvalues (np.ndarray): real descending, shape (n,).
    """
    n = R.shape[0]
    n_sources = min(n_sources, n - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(R)
    idx = np.argsort(eigenvalues)[::-1]
    return eigenvectors[:, idx][:, n_sources:], np.real(eigenvalues[idx])


def noise_projector(E_n):
    """Orthogonal projector onto noise subspace: C = E_n @ E_n^H.

    Args:
        E_n (np.ndarray): complex, shape (n, n - n_sources).

    Returns:
        np.ndarray: complex Hermitian, shape (n, n).
    """
    return E_n @ E_n.conj().T


# -----------------------------------------------------------------------
# UPA marginal covariances (new)
# -----------------------------------------------------------------------

def upa_row_covariance(R, n_cols):
    """Extract N×N azimuth sub-covariance from full (M·N × M·N) R.

    Sums over the M block-diagonal N×N blocks, then normalises.

    Args:
        R (np.ndarray): complex Hermitian, shape (M*N, M*N).
        n_cols (int): N, number of columns (elements along x/azimuth axis).

    Returns:
        np.ndarray: complex Hermitian, shape (N, N).
    """
    nr    = R.shape[0]
    n_rows = nr // n_cols
    R_row = np.zeros((n_cols, n_cols), dtype=complex)
    for m in range(n_rows):
        sl = slice(m * n_cols, (m + 1) * n_cols)
        R_row += R[sl, sl]
    return R_row / n_rows


def upa_col_covariance(R, n_rows, n_cols):
    """Extract M×M elevation sub-covariance from full (M·N × M·N) R.

    Sums over N strided blocks (one per column index n), then normalises.

    Args:
        R (np.ndarray): complex Hermitian, shape (M*N, M*N).
        n_rows (int): M, number of rows (elements along y/elevation axis).
        n_cols (int): N, number of columns.

    Returns:
        np.ndarray: complex Hermitian, shape (M, M).
    """
    R_col = np.zeros((n_rows, n_rows), dtype=complex)
    for n in range(n_cols):
        idx = np.arange(n_rows) * n_cols + n
        R_col += R[np.ix_(idx, idx)]
    return R_col / n_cols


# -----------------------------------------------------------------------
# Root-MUSIC polynomial helpers (ULA and both axes of UPA)
# -----------------------------------------------------------------------

def rare_l_polynomial(C):
    """Form Root-MUSIC polynomial coefficients from noise projector C.

    Builds Laurent polynomial P_L(z) = sum_m c_m z^m and multiplies by
    z^{n-1} to give a standard polynomial of degree 2*(n-1).

    Args:
        C (np.ndarray): complex Hermitian, shape (n, n).

    Returns:
        np.ndarray: complex, length 2*n-1, highest power first.
    """
    n = C.shape[0]
    coeffs = np.zeros(2 * n - 1, dtype=complex)
    for m in range(-(n - 1), n):
        coeffs[m + n - 1] = np.sum(np.diagonal(C, offset=m))
    return coeffs[::-1]   # highest power first for np.roots


def select_doa_roots(roots, n_sources, min_angle_sep=0.02):
    """Select n_sources distinct roots nearest the unit circle.

    Unit-circle roots are self-conjugate-reciprocal, so each true DoA
    appears twice. Deduplication by phase angle prevents double-counting.

    Args:
        roots (np.ndarray): complex, all polynomial roots.
        n_sources (int): roots to return.
        min_angle_sep (float): minimum phase separation (rad) to count as
            distinct. Default 0.02 rad ≈ 1.1°.

    Returns:
        np.ndarray: complex, shape (n_sources,). NaN-padded if needed.
    """
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


def roots_to_doa(roots, d_lambda=DEFAULT_D_LAMBDA):
    """Convert complex roots near the unit circle to DoA angles (degrees).

    Args:
        roots (np.ndarray): complex.
        d_lambda (float): inter-element spacing in wavelengths.

    Returns:
        np.ndarray: real degrees, NaN for unphysical (|sin|>1) roots.
    """
    psi   = np.angle(roots)
    sin_t = psi / (2.0 * np.pi * d_lambda)
    valid = np.abs(sin_t) <= 1.0
    theta = np.full(len(roots), np.nan)
    theta[valid] = np.rad2deg(np.arcsin(sin_t[valid]))
    return theta


# -----------------------------------------------------------------------
# Internal pipeline helper
# -----------------------------------------------------------------------

def _run_1d_pipeline(R_sub, n_src, d_lam):
    """Run the 1D Root-MUSIC pipeline on a covariance sub-matrix.

    Shared by both the ULA path and each axis of the UPA separable path.

    Args:
        R_sub (np.ndarray): complex Hermitian, shape (n, n).
        n_src (int): number of sources.
        d_lam (float): spacing in wavelengths for this axis.

    Returns:
        tuple: (doa_deg, roots, E_n, eigenvalues)
    """
    n = R_sub.shape[0]
    if n < 2:
        nan_d = np.full(n_src, np.nan)
        nan_z = np.full(n_src, np.nan + 0j)
        return nan_d, nan_z, np.zeros((n, 1)), np.array([0.0])

    E_n, eigs = noise_subspace(R_sub, n_src)
    C         = noise_projector(E_n)
    poly      = rare_l_polynomial(C)
    roots     = np.roots(poly)
    sel       = select_doa_roots(roots, n_src)
    doa       = roots_to_doa(sel, d_lam)
    return doa, sel, E_n, eigs


# -----------------------------------------------------------------------
# 1D MUSIC spectrum (ULA and each axis of UPA)
# -----------------------------------------------------------------------

def music_spectrum(E_n, d_lambda=DEFAULT_D_LAMBDA):
    """MUSIC pseudospectrum over the _SCAN_ANGLES_DEG grid.

    Args:
        E_n (np.ndarray): complex, shape (n, n - n_sources).
        d_lambda (float): inter-element spacing in wavelengths.

    Returns:
        np.ndarray: real, shape (361,). P_MUSIC(theta) = 1/|a^H C a|.
    """
    n    = E_n.shape[0]
    C    = noise_projector(E_n)
    spec = np.zeros(len(_SCAN_ANGLES_RAD))
    for i, theta in enumerate(_SCAN_ANGLES_RAD):
        psi      = 2.0 * np.pi * d_lambda * np.sin(theta)
        a        = np.exp(1j * psi * np.arange(n))
        spec[i]  = 1.0 / max(abs(np.real(a.conj() @ C @ a)), 1e-12)
    return spec


def music_spectrum_db(E_n, d_lambda=DEFAULT_D_LAMBDA):
    """MUSIC pseudospectrum, normalised to 0 dB at peak.

    Args:
        E_n (np.ndarray): complex, shape (n, n - n_sources).
        d_lambda (float): inter-element spacing in wavelengths.

    Returns:
        np.ndarray: real dB, shape (361,).
    """
    s = music_spectrum(E_n, d_lambda)
    s_db = 10.0 * np.log10(np.maximum(s, 1e-12))
    return s_db - s_db.max()


# -----------------------------------------------------------------------
# UPA two-axis spectrum (new)
# -----------------------------------------------------------------------

def music_spectrum_upa(E_n_az, E_n_el, d_x=DEFAULT_D_LAMBDA,
                       d_y=DEFAULT_D_LAMBDA):
    """Separable 1D MUSIC spectra for azimuth and elevation axes.

    Uses the marginal noise subspaces (from upa_row_covariance /
    upa_col_covariance) rather than the full UPA projector. Cost is
    O(n_az * N^2 + n_el * M^2) instead of O(n_az * n_el * (M*N)^2).

    Args:
        E_n_az (np.ndarray): noise subspace from row covariance (N × *).
        E_n_el (np.ndarray): noise subspace from col covariance (M × *).
        d_x (float): column (azimuth) spacing in wavelengths.
        d_y (float): row (elevation) spacing in wavelengths.

    Returns:
        tuple: (az_db, el_db), each shape (361,), dB normalised to 0.
    """
    return (music_spectrum_db(E_n_az, d_x),
            music_spectrum_db(E_n_el, d_y))


# -----------------------------------------------------------------------
# Full 2D MUSIC spectrum (expensive, for offline use)
# -----------------------------------------------------------------------

def music_spectrum_2d(C_full, array_shape, d_x=DEFAULT_D_LAMBDA,
                      d_y=DEFAULT_D_LAMBDA, n_az=91, n_el=91):
    """Full 2D MUSIC pseudospectrum for a UPA (expensive).

    Scans an (n_az × n_el) angular grid using the complete MN-element
    noise projector and the Kronecker UPA steering vector. Not called in
    the real-time ingestion path.

    Args:
        C_full (np.ndarray): full noise projector, shape (M*N, M*N).
        array_shape (tuple): (M, N) — rows × columns.
        d_x (float): column spacing in wavelengths.
        d_y (float): row spacing in wavelengths.
        n_az (int): number of azimuth scan points.
        n_el (int): number of elevation scan points.

    Returns:
        tuple: (spec_db, az_deg, el_deg)
            spec_db (np.ndarray): dB, shape (n_el, n_az), 0 dB at peak.
            az_deg (np.ndarray): shape (n_az,).
            el_deg (np.ndarray): shape (n_el,).
    """
    M, N        = array_shape
    az_deg      = np.linspace(-90.0, 90.0, n_az)
    el_deg      = np.linspace(-90.0, 90.0, n_el)
    az_rad      = np.deg2rad(az_deg)
    el_rad      = np.deg2rad(el_deg)
    rows        = np.arange(M)
    cols        = np.arange(N)
    spec        = np.zeros((n_el, n_az))

    for ei, el in enumerate(el_rad):
        v = d_y * np.sin(el)
        a_col = np.exp(1j * 2.0 * np.pi * v * rows)   # (M,)
        for ai, az in enumerate(az_rad):
            u     = d_x * np.sin(az) * np.cos(el)
            a_row = np.exp(1j * 2.0 * np.pi * u * cols)   # (N,)
            a     = np.kron(a_col, a_row)                  # (M*N,)
            denom = np.real(a.conj() @ C_full @ a)
            spec[ei, ai] = 1.0 / max(abs(denom), 1e-12)

    spec_db = 10.0 * np.log10(np.maximum(spec, 1e-12))
    spec_db -= spec_db.max()
    return spec_db, az_deg, el_deg


# -----------------------------------------------------------------------
# Unified top-level estimator
# -----------------------------------------------------------------------

def rare_l_estimate(v_all, n_sources=1, d_x=DEFAULT_D_LAMBDA,
                    d_y=DEFAULT_D_LAMBDA, array_shape=None):
    """Run RARE-L for ULA or UPA and return a result dict.

    For ULA (array_shape=None): runs the 1D Root-MUSIC pipeline on the
    full Nr-element covariance. El fields in the result are None.

    For UPA (array_shape=(M, N)): marginalises R into row/column
    sub-covariances, runs 1D Root-MUSIC on each axis separately, and
    corrects the azimuth angle for the elevation projection.

    Args:
        v_all (np.ndarray): complex, shape (n_sc, nr, nc).
        n_sources (int): number of sources to estimate per axis.
        d_x (float): inter-element spacing along x/column axis (wavelengths).
        d_y (float): inter-element spacing along y/row axis (wavelengths).
            Ignored in ULA mode.
        array_shape (tuple or None): (M_rows, N_cols) for UPA.
            None means ULA (treat full Nr as a single row).

    Returns:
        dict:
            mode (str): 'ula' or 'upa'.
            eigenvalues (np.ndarray): descending eigenvalues of full R.
            n_sources (int): n_sources used.
            az (dict): {doa, roots, spectrum_db} for azimuth.
                doa (np.ndarray): degrees, shape (n_sources,).
                roots (np.ndarray): complex, shape (n_sources,).
                spectrum_db (np.ndarray): 1D MUSIC, shape (361,).
            el (dict or None): same structure for elevation. None if ULA.
    """
    nr = v_all.shape[1]
    R  = covariance_from_cbf(v_all)

    if array_shape is None:
        # ---- ULA path -----------------------------------------------
        n_src          = max(1, min(n_sources, nr - 1))
        az_doa, az_z, E_n_az, eigs = _run_1d_pipeline(R, n_src, d_x)
        az_spec        = music_spectrum_db(E_n_az, d_x)
        return {
            'mode':        'ula',
            'eigenvalues': eigs,
            'n_sources':   n_src,
            'az': {'doa': az_doa, 'roots': az_z, 'spectrum_db': az_spec},
            'el': None,
        }

    # ---- UPA path ---------------------------------------------------
    M, N   = array_shape
    n_src_az = max(1, min(n_sources, N - 1))
    n_src_el = max(1, min(n_sources, M - 1))

    R_row = upa_row_covariance(R, N)
    R_col = upa_col_covariance(R, M, N)

    az_doa_raw, az_z, E_n_az, _  = _run_1d_pipeline(R_row, n_src_az, d_x)
    el_doa,     el_z, E_n_el, eigs = _run_1d_pipeline(R_col, n_src_el, d_y)

    # Correct azimuth: row covariance Root-MUSIC gives u = d_x sin(az) cos(el)
    # → az_true = arcsin(u / (d_x * cos(el_rad)))
    az_doa = _correct_azimuth(az_doa_raw, el_doa, d_x)

    az_spec, el_spec = music_spectrum_upa(E_n_az, E_n_el, d_x, d_y)

    # Full covariance eigenvalues for display
    _, eigs_full = noise_subspace(R, max(1, min(n_sources, nr - 1)))

    return {
        'mode':        'upa',
        'eigenvalues': eigs_full,
        'n_sources':   n_sources,
        'az': {'doa': az_doa, 'roots': az_z, 'spectrum_db': az_spec},
        'el': {'doa': el_doa, 'roots': el_z, 'spectrum_db': el_spec},
    }


def _correct_azimuth(az_apparent_deg, el_deg, d_x):
    """Correct azimuth DoA estimates for elevation projection.

    The row-covariance Root-MUSIC estimates u = d_x sin(az) cos(el).
    Dividing by cos(el) recovers true azimuth.

    Args:
        az_apparent_deg (np.ndarray): uncorrected az estimates (degrees).
        el_deg (np.ndarray): elevation estimates (degrees).
        d_x (float): column spacing in wavelengths.

    Returns:
        np.ndarray: corrected azimuth in degrees.
    """
    # Use the first elevation estimate (or 0 if NaN/unavailable).
    el_rad    = np.deg2rad(np.nanmean(el_deg) if len(el_deg) else 0.0)
    cos_el    = np.cos(el_rad)
    if abs(cos_el) < 1e-6:
        return az_apparent_deg   # near ±90° el: correction undefined

    az_corrected = np.full(len(az_apparent_deg), np.nan)
    for i, az_app in enumerate(az_apparent_deg):
        if np.isnan(az_app):
            continue
        psi    = 2.0 * np.pi * d_x * np.sin(np.deg2rad(az_app))
        sin_az = psi / (2.0 * np.pi * d_x * cos_el)
        if abs(sin_az) <= 1.0:
            az_corrected[i] = np.rad2deg(np.arcsin(sin_az))
    return az_corrected
