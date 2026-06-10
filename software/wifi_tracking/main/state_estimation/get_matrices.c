#include <math.h>
#include <string.h>
#include "get_matrices.h"

/*
 * get_angle_bits - Derive phi and psi bit widths from a CBF result.
 *
 * cbf  : (in)  const wifi_cbf_result_t*
 * bphi : (out) uint8_t* bits per phi angle
 * bpsi : (out) uint8_t* bits per psi angle
 */
static void get_angle_bits(const wifi_cbf_result_t *cbf,
                            uint8_t *bphi, uint8_t *bpsi)
{
    if (cbf->phy_type == WIFI_PHY_VHT) {
        *bphi = VHT_BPHI;
        *bpsi = VHT_BPSI;
        return;
    }
    if (!cbf->is_mu) {
        *bphi = cbf->codebook ? HE_SU_BPHI_CB1 : HE_SU_BPHI_CB0;
        *bpsi = cbf->codebook ? HE_SU_BPSI_CB1 : HE_SU_BPSI_CB0;
    } else {
        *bphi = cbf->codebook ? HE_MU_BPHI_CB1 : HE_MU_BPHI_CB0;
        *bpsi = cbf->codebook ? HE_MU_BPSI_CB1 : HE_MU_BPSI_CB0;
    }
}

bool reconstruct_v(const wifi_cbf_result_t *cbf, uint16_t sc_idx,
                   float *v_real, float *v_imag)
{
    if (!cbf || !v_real || !v_imag)
        return false;
    if (sc_idx >= cbf->num_subcarriers)
        return false;
    if (cbf->phi_count > 0 && (!cbf->phi || !cbf->psi))
        return false;

    const uint8_t  nc   = cbf->num_streams;
    const uint8_t  nr   = cbf->num_rows;
    const uint16_t base = (uint16_t)(sc_idx * cbf->phi_count);

    uint8_t bphi, bpsi;
    get_angle_bits(cbf, &bphi, &bpsi);

    /* phi in [0, pi/2], psi in [-pi, pi) — uniform linear quantisation */
    const float phi_scale = ((float)M_PI / 2.0f) / (float)(1 << bphi);
    const float psi_scale = (2.0f * (float)M_PI) / (float)(1 << bpsi);

    /* Initialise V to first Nc columns of the Nr x Nr identity */
    const uint16_t sz = (uint16_t)nr * nc;
    memset(v_real, 0, sz * sizeof(float));
    memset(v_imag, 0, sz * sizeof(float));
    for (uint8_t c = 0; c < nc; c++)
        v_real[(uint16_t)c * nc + c] = 1.0f;

    /* Left-multiply V by G(l,m,phi,psi) for each (l,m) pair in order */
    uint8_t pair = 0;
    uint8_t l, m, j;

    for (l = 0; l < nc; l++) {
        for (m = l + 1; m < nr; m++) {
            const float phi = cbf->phi[base + pair] * phi_scale;
            const float psi =
                cbf->psi[base + pair] * psi_scale - (float)M_PI;
            pair++;

            const float cp = cosf(phi);
            const float sp = sinf(phi);
            const float cr = cosf(psi);
            const float sr = sinf(psi);

            for (j = 0; j < nc; j++) {
                const uint16_t li = (uint16_t)l * nc + j;
                const uint16_t mi = (uint16_t)m * nc + j;

                const float vl_r = v_real[li];
                const float vl_i = v_imag[li];
                const float vm_r = v_real[mi];
                const float vm_i = v_imag[mi];

                /* G[l,l]=cp, G[l,m]=-sp*e^{-j*psi}, G[m,l]=sp*e^{j*psi} */
                v_real[li] = cp * vl_r - sp * (cr * vm_r + sr * vm_i);
                v_imag[li] = cp * vl_i - sp * (cr * vm_i - sr * vm_r);

                v_real[mi] = sp * (cr * vl_r - sr * vl_i) + cp * vm_r;
                v_imag[mi] = sp * (cr * vl_i + sr * vl_r) + cp * vm_i;
            }
        }
    }
    return true;
}

/* ---- Eigendecomposition of V*V^H --------------------------------- */

#define JACOBI_MAX_SWEEPS  30
#define JACOBI_EPS         1e-6f

/*
 * compute_gram - Compute M = V * V^H  (nr x nr Hermitian).
 *
 * v_r, v_i : (in)  float* V matrix, row-major, nr x nc
 * nr, nc   : (in)  matrix dimensions
 * m_r, m_i : (out) float* result, row-major, nr x nr
 */
static void compute_gram(const float *v_r, const float *v_i,
                          uint8_t nr, uint8_t nc,
                          float *m_r, float *m_i)
{
    uint8_t i, j, k;
    for (i = 0; i < nr; i++) {
        for (j = 0; j < nr; j++) {
            float sr = 0.0f, si = 0.0f;
            for (k = 0; k < nc; k++) {
                float ar = v_r[i*nc+k], ai = v_i[i*nc+k];
                float br = v_r[j*nc+k], bi = v_i[j*nc+k];
                sr += ar*br + ai*bi;
                si += ai*br - ar*bi;
            }
            m_r[i*nr+j] = sr;
            m_i[i*nr+j] = si;
        }
    }
}

/*
 * jacobi_herm - Eigendecompose an n x n Hermitian matrix in-place.
 *
 * Applies cyclic complex Jacobi sweeps until convergence.
 * On return, eigenvalues are on the diagonal of m_r/m_i and
 * columns of q_r/q_i hold the corresponding eigenvectors.
 *
 * m_r, m_i : (in/out) float* matrix, overwritten with diagonal eigenvalues
 * n        : (in)     uint8_t matrix dimension
 * q_r, q_i : (out)    float* eigenvector matrix, initialised to identity
 */
static void jacobi_herm(float *m_r, float *m_i, uint8_t n,
                         float *q_r, float *q_i)
{
    uint8_t i, j, k, p, q;

    /* Initialise Q = I */
    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++) {
            q_r[i*n+j] = (i == j) ? 1.0f : 0.0f;
            q_i[i*n+j] = 0.0f;
        }

    for (int sweep = 0; sweep < JACOBI_MAX_SWEEPS; sweep++) {
        bool any = false;

        for (p = 0; p < n; p++) {
            for (q = p + 1; q < n; q++) {
                float a  = m_r[p*n+q], b = m_i[p*n+q];
                float r  = sqrtf(a*a + b*b);
                if (r < JACOBI_EPS)
                    continue;
                any = true;

                float phi = atan2f(b, a);
                float pc  = cosf(phi), ps = sinf(phi);

                float mpp = m_r[p*n+p], mqq = m_r[q*n+q];
                float tau = (mqq - mpp) / (2.0f * r);
                float t   = (tau >= 0.0f)
                    ?  1.0f / ( tau + sqrtf(1.0f + tau*tau))
                    :  1.0f / ( tau - sqrtf(1.0f + tau*tau));
                float c   = 1.0f / sqrtf(1.0f + t*t);
                float s   = t * c;

                /* Zero out (p,q) and update diagonal */
                m_r[p*n+p] = c*c*mpp + s*s*mqq - 2.0f*s*c*r;
                m_r[q*n+q] = s*s*mpp + c*c*mqq + 2.0f*s*c*r;
                m_r[p*n+q] = m_i[p*n+q] = 0.0f;
                m_r[q*n+p] = m_i[q*n+p] = 0.0f;

                /* Update off-diagonal rows/columns k != p, q */
                for (k = 0; k < n; k++) {
                    if (k == p || k == q)
                        continue;
                    float hkpr = m_r[k*n+p], hkpi = m_i[k*n+p];
                    float hkqr = m_r[k*n+q], hkqi = m_i[k*n+q];

                    /* h'[k][p] = c*h[k][p] - s*e^{-j*phi}*h[k][q] */
                    m_r[k*n+p] = c*hkpr - s*(pc*hkqr + ps*hkqi);
                    m_i[k*n+p] = c*hkpi - s*(pc*hkqi - ps*hkqr);
                    /* h'[k][q] = s*e^{j*phi}*h[k][p] + c*h[k][q] */
                    m_r[k*n+q] = s*(pc*hkpr - ps*hkpi) + c*hkqr;
                    m_i[k*n+q] = s*(ps*hkpr + pc*hkpi) + c*hkqi;
                    /* Hermitian symmetry */
                    m_r[p*n+k] =  m_r[k*n+p];
                    m_i[p*n+k] = -m_i[k*n+p];
                    m_r[q*n+k] =  m_r[k*n+q];
                    m_i[q*n+k] = -m_i[k*n+q];
                }

                /* Update eigenvector columns: Q' = Q * G */
                for (k = 0; k < n; k++) {
                    float qkpr = q_r[k*n+p], qkpi = q_i[k*n+p];
                    float qkqr = q_r[k*n+q], qkqi = q_i[k*n+q];

                    q_r[k*n+p] = c*qkpr - s*(pc*qkqr + ps*qkqi);
                    q_i[k*n+p] = c*qkpi - s*(pc*qkqi - ps*qkqr);
                    q_r[k*n+q] = s*(pc*qkpr - ps*qkpi) + c*qkqr;
                    q_i[k*n+q] = s*(ps*qkpr + pc*qkpi) + c*qkqi;
                }
            }
        }
        if (!any)
            break;
    }
}

/*
 * sort_eigen_desc - Sort eigenvalues descending; reorder eigenvectors.
 *
 * vals     : (in/out) float*, n eigenvalues
 * q_r, q_i : (in/out) float*, n x n eigenvector matrix (column-per-vector)
 * n        : (in)     uint8_t number of eigenvalues
 */
static void sort_eigen_desc(float *vals, float *q_r, float *q_i,
                              uint8_t n)
{
    float col_r[WIFI_MAX_STREAMS], col_i[WIFI_MAX_STREAMS];
    uint8_t i, j, k;

    for (i = 1; i < n; i++) {
        float key = vals[i];
        for (k = 0; k < n; k++) {
            col_r[k] = q_r[k*n + i];
            col_i[k] = q_i[k*n + i];
        }
        j = i;
        while (j > 0 && vals[j - 1] < key) {
            vals[j] = vals[j - 1];
            for (k = 0; k < n; k++) {
                q_r[k*n + j] = q_r[k*n + (j-1)];
                q_i[k*n + j] = q_i[k*n + (j-1)];
            }
            j--;
        }
        vals[j] = key;
        for (k = 0; k < n; k++) {
            q_r[k*n + j] = col_r[k];
            q_i[k*n + j] = col_i[k];
        }
    }
}

bool get_eigendecomp(const float *v_real, const float *v_imag,
                     uint8_t nr, uint8_t nc,
                     float *eigenvals,
                     float *evec_real, float *evec_imag)
{
    if (!v_real || !v_imag || !eigenvals || !evec_real || !evec_imag)
        return false;

    float m_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float m_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

    compute_gram(v_real, v_imag, nr, nc, m_r, m_i);
    jacobi_herm(m_r, m_i, nr, evec_real, evec_imag);

    for (uint8_t k = 0; k < nr; k++)
        eigenvals[k] = m_r[k * nr + k];

    sort_eigen_desc(eigenvals, evec_real, evec_imag, nr);
    return true;
}
