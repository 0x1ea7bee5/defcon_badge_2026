#include <stdio.h>
#include <string.h>
#include "telemetry.h"
#include "state_estimation/get_matrices_cbf.h"

/*
 * Wire format — one text line per packet, immune to CR/LF translation:
 *   TELEM:<hex>\n
 * where <hex> encodes: MAGIC(4) LENGTH(2 LE) PAYLOAD(N) CKSUM(1)
 * CKSUM is XOR of all PAYLOAD bytes.
 */

static const char HEX[] = "0123456789ABCDEF";
static const uint8_t MAGIC[4]     = {0xCB, 0xF1, 0xFE, 0xED};
static const uint8_t MAGIC_CSI[4] = {0xC5, 0x1D, 0xFE, 0xED};

/* Emit one byte as two hex chars; if ck != NULL also accumulate XOR. */
static void hb(uint8_t v, uint8_t *ck)
{
    if (ck) *ck ^= v;
    char h[2] = {HEX[v >> 4], HEX[v & 0xF]};
    fwrite(h, 1, 2, stdout);
}

static void hbuf(const uint8_t *b, size_t n, uint8_t *ck)
{
    for (size_t i = 0; i < n; i++)
        hb(b[i], ck);
}

static void hu16(uint16_t v, uint8_t *ck)
{
    hb((uint8_t)v,        ck);
    hb((uint8_t)(v >> 8), ck);
}

static void hi16(int16_t v, uint8_t *ck)
{
    hb((uint8_t)v,                  ck);
    hb((uint8_t)((uint16_t)v >> 8), ck);
}

static void hf32(float v, uint8_t *ck)
{
    uint8_t b[4];
    memcpy(b, &v, 4);
    hbuf(b, 4, ck);
}

bool telemetry_send(const uint8_t *data, size_t len)
{
    if (!data || len == 0)
        return false;
    printf("TELEM:");
    hbuf(data, len, NULL);
    printf("\n");
    fflush(stdout);
    return true;
}

/*
 * cbf_payload_len - Payload byte count for a CBF telemetry packet.
 *
 * Fixed : 6 (MAC) + 1 + 1 + 2 + 1 + 1 + 1 + nc (SNR)
 * Per SC: phi*2 + psi*2 + 2*nr*nc*4 (V real + imag)
 */
static uint16_t cbf_payload_len(const wifi_cbf_result_t *cbf)
{
    const uint16_t per_sc =
        (uint16_t)cbf->phi_count   * 2u +
        (uint16_t)cbf->psi_count   * 2u +
        (uint16_t)cbf->num_rows    *
        (uint16_t)cbf->num_streams * 8u;

    return 6u + 1u + 1u + 2u + 1u + 1u + 1u +
           (uint16_t)cbf->num_streams +
           cbf->num_subcarriers * per_sc;
}

bool telemetry_send_cbf(const wifi_cbf_result_t *cbf)
{
    if (!cbf)
        return false;

    const uint8_t  nr   = cbf->num_rows;
    const uint8_t  nc   = cbf->num_streams;
    const uint16_t plen = cbf_payload_len(cbf);
    const uint16_t nrnc = (uint16_t)nr * nc;

    float v_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float v_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

    /* Line prefix + framing header (magic + length, not checksummed) */
    printf("TELEM:");
    hbuf(MAGIC, 4, NULL);
    hu16(plen, NULL);

    /* Payload (every byte also XOR'd into ck) */
    uint8_t ck = 0;

    hbuf(cbf->src_mac, WIFI_MAC_ADDR_LEN, &ck);
    hb(nc,                      &ck);
    hb(nr,                      &ck);
    hu16(cbf->num_subcarriers,  &ck);
    hb((uint8_t)cbf->phy_type,  &ck);
    hb((uint8_t)cbf->is_mu,     &ck);
    hb((uint8_t)cbf->bandwidth, &ck);
    for (uint8_t i = 0; i < nc; i++)
        hb((uint8_t)cbf->snr[i], &ck);

    for (uint16_t sc = 0; sc < cbf->num_subcarriers; sc++) {
        for (uint8_t a = 0; a < cbf->phi_count; a++)
            hi16(cbf->phi[sc * cbf->phi_count + a], &ck);
        for (uint8_t a = 0; a < cbf->psi_count; a++)
            hi16(cbf->psi[sc * cbf->psi_count + a], &ck);

        if (reconstruct_v(cbf, sc, v_r, v_i)) {
            for (uint16_t k = 0; k < nrnc; k++)
                hf32(v_r[k], &ck);
            for (uint16_t k = 0; k < nrnc; k++)
                hf32(v_i[k], &ck);
        } else {
            const float zero = 0.0f;
            for (uint16_t k = 0; k < 2u * nrnc; k++)
                hf32(zero, &ck);
        }
    }

    /* Checksum byte (not included in its own XOR) */
    hb(ck, NULL);
    printf("\n");
    fflush(stdout);
    return true;
}

bool telemetry_send_csi(const wifi_csi_result_t *csi)
{
    if (!csi || !csi->data || csi->num_samples == 0)
        return false;

    /* payload: src_mac(6) dst_mac(6) rssi(1) channel(1)
     *          num_samples(2 LE) data[num_samples]  */
    const uint16_t plen = 16u + csi->num_samples;

    printf("TELEM:");
    hbuf(MAGIC_CSI, 4, NULL);
    hu16(plen, NULL);

    uint8_t ck = 0;
    hbuf(csi->src_mac, WIFI_MAC_ADDR_LEN, &ck);
    hbuf(csi->dst_mac, WIFI_MAC_ADDR_LEN, &ck);
    hb((uint8_t)csi->rssi, &ck);
    hb(csi->channel,       &ck);
    hu16(csi->num_samples, &ck);
    for (uint16_t i = 0; i < csi->num_samples; i++)
        hb((uint8_t)csi->data[i], &ck);
    hb(ck, NULL);
    printf("\n");
    fflush(stdout);
    return true;
}
