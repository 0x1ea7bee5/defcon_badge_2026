#ifndef GET_MATRICES_CBF_H
#define GET_MATRICES_CBF_H

#include <stdbool.h>
#include <stdint.h>
#include "wifi/wifi_defs.h"

/*
 * reconstruct_v - Reconstruct the Nr x Nc beamforming steering matrix V
 *                 for a single subcarrier from a CBF result.
 *
 * The matrix is built by initialising V to the first Nc columns of the
 * Nr x Nr identity matrix and then left-multiplying by each Givens
 * rotation G(l,m,phi,psi) in the order they appear in the CBF report
 * (l = 0..Nc-1, m = l+1..Nr-1).
 *
 * cbf    : (in)  const wifi_cbf_result_t* — parsed CBF data
 * sc_idx : (in)  uint16_t — subcarrier index (0..num_subcarriers-1)
 * v_real : (out) float* — real part, row-major, nr * nc elements
 * v_imag : (out) float* — imaginary part, same layout
 *
 * Element (r, c) is at v_real[r * num_streams + c].
 * Both arrays must be pre-allocated to (num_rows * num_streams) floats.
 * Returns false on NULL inputs, out-of-range sc_idx, or missing angles.
 */
bool reconstruct_v(const wifi_cbf_result_t *cbf, uint16_t sc_idx,
                   float *v_real, float *v_imag);

/*
 * get_eigendecomp - Eigendecompose the spatial covariance matrix V*V^H.
 *
 * Computes eigenvalues and eigenvectors of the nr x nr Hermitian matrix
 * M = V*V^H, where V is provided as separate real and imaginary arrays
 * (as returned by reconstruct_v).  Uses cyclic Jacobi iteration.
 *
 * v_real    : (in)  float* — real part of V, row-major, nr * nc elements
 * v_imag    : (in)  float* — imaginary part of V, same layout
 * nr        : (in)  uint8_t — number of rows (receive antennas)
 * nc        : (in)  uint8_t — number of columns (spatial streams)
 * eigenvals : (out) float* — nr eigenvalues sorted descending
 * evec_real : (out) float* — real part of eigenvectors, nr * nr, column j
 *                            corresponds to eigenvals[j]
 * evec_imag : (out) float* — imaginary part, same layout
 *
 * All output arrays must be pre-allocated to (nr * nr) floats except
 * eigenvals which needs nr floats.
 * Returns false on NULL inputs.
 */
bool get_eigendecomp(const float *v_real, const float *v_imag,
                     uint8_t nr, uint8_t nc,
                     float *eigenvals,
                     float *evec_real, float *evec_imag);

#endif /* GET_MATRICES_H */
