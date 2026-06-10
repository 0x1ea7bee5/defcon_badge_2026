# get_matrices

Functions for reconstructing channel state matrices from parsed CBF data.

---

## `reconstruct_v`

Reconstructs the Nr × Nc complex beamforming steering matrix **V** for a
single subcarrier from a `wifi_cbf_result_t` produced by `packet_scanner`.

### Signature

```c
bool reconstruct_v(const wifi_cbf_result_t *cbf, uint16_t sc_idx,
                   float *v_real, float *v_imag);
```

### Parameters

| Parameter | Direction | Description |
|---|---|---|
| `cbf` | in | Parsed CBF result from `scan_for_cbf` |
| `sc_idx` | in | Subcarrier index (0 .. `num_subcarriers − 1`) |
| `v_real` | out | Real part of V, row-major, `num_rows × num_streams` floats |
| `v_imag` | out | Imaginary part of V, same layout |

Element (r, c) is at index `r * num_streams + c`.

Both arrays must be pre-allocated by the caller.

Returns `false` on NULL inputs, out-of-range `sc_idx`, or missing angle data.

### Algorithm

1. Initialise V to the first Nc columns of the Nr × Nr identity matrix.
2. For each Givens rotation pair (l, m) — in the order l = 0..Nc−1,
   m = l+1..Nr−1 — left-multiply V by G(l, m, φ, ψ):

```
G[l,l] =  cos φ
G[l,m] = −sin φ · e^{−jψ}
G[m,l] =  sin φ · e^{+jψ}
G[m,m] =  cos φ
```

### Angle quantisation

Raw angle indices from `wifi_cbf_result_t` are converted to radians:

```
φ = raw_phi * (π/2) / 2^bphi      range [0, π/2)
ψ = raw_psi * (2π)  / 2^bpsi − π  range [−π, π)
```

Bit widths are derived from `phy_type`, `is_mu`, and `codebook`:

| PHY | Mode | Codebook | bφ | bψ |
|---|---|---|---|---|
| VHT | — | — | 7 | 5 |
| HE | SU | 0 | 4 | 2 |
| HE | SU | 1 | 6 | 4 |
| HE | MU | 0 | 7 | 5 |
| HE | MU | 1 | 9 | 7 |

### Example usage

```c
float v_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
float v_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

if (reconstruct_v(&cbf, 0, v_r, v_i)) {
    for (uint8_t r = 0; r < cbf.num_rows; r++) {
        for (uint8_t c = 0; c < cbf.num_streams; c++) {
            uint16_t idx = r * cbf.num_streams + c;
            printf("V[%u][%u] = %.4f + %.4fj\n",
                   r, c, v_r[idx], v_i[idx]);
        }
    }
}
```

---

## `get_eigendecomp`

Computes the eigenvalues and eigenvectors of the spatial covariance matrix
**M = V · V^H** (Nr × Nr Hermitian positive semidefinite).

### Signature

```c
bool get_eigendecomp(const float *v_real, const float *v_imag,
                     uint8_t nr, uint8_t nc,
                     float *eigenvals,
                     float *evec_real, float *evec_imag);
```

### Parameters

| Parameter | Direction | Description |
|---|---|---|
| `v_real` | in | Real part of V, row-major, `nr × nc` floats |
| `v_imag` | in | Imaginary part of V, same layout |
| `nr` | in | Number of rows (receive antennas) |
| `nc` | in | Number of columns (spatial streams) |
| `eigenvals` | out | `nr` eigenvalues, sorted descending |
| `evec_real` | out | Real part of eigenvectors, `nr × nr`, column `j` = eigenvector for `eigenvals[j]` |
| `evec_imag` | out | Imaginary part, same layout |

`eigenvals` must be pre-allocated to `nr` floats; `evec_real` and `evec_imag`
to `nr × nr` floats each.

Returns `false` on NULL inputs.

### Algorithm

1. **Gram matrix**: Compute M = V · V^H (Nr × Nr Hermitian).
   `M[i][j] = Σ_k V[i][k] · conj(V[j][k])`

2. **Jacobi eigendecomposition**: Apply cyclic complex Jacobi sweeps (up to
   30) until all off-diagonal elements are below threshold 1e-6.
   For each pivot pair (p, q):
   - Compute rotation angle from the off-diagonal element magnitude and phase.
   - Apply a unitary 2×2 rotation that zeroes `M[p][q]`.
   - Accumulate the rotation into the eigenvector matrix Q (initialised to I).

3. **Sort**: Insertion-sort eigenvalues descending, swapping the corresponding
   eigenvector columns.

On return eigenvalues are on the diagonal of the diagonalised M, and column
`j` of `evec_real`/`evec_imag` holds the eigenvector for `eigenvals[j]`.

### Example usage

```c
float v_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
float v_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
float evals[WIFI_MAX_STREAMS];
float evec_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
float evec_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

if (reconstruct_v(&cbf, 0, v_r, v_i)) {
    if (get_eigendecomp(v_r, v_i, cbf.num_rows, cbf.num_streams,
                        evals, evec_r, evec_i)) {
        for (uint8_t k = 0; k < cbf.num_rows; k++)
            printf("lambda[%u] = %.4f\n", k, evals[k]);
    }
}
```
