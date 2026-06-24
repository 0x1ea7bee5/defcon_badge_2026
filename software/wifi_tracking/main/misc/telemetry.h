#ifndef TELEMETRY_H
#define TELEMETRY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "wifi/wifi_defs.h"

/*
 * telemetry_send - Emit raw bytes over USB serial JTAG.
 *
 * data : (in) const uint8_t* — byte array to transmit
 * len  : (in) size_t         — number of bytes
 *
 * Returns true on success, false on NULL / zero-length input.
 */
bool telemetry_send(const uint8_t *data, size_t len);

/*
 * telemetry_send_cbf - Serialize and emit a binary CBF telemetry packet.
 *
 * For every subcarrier in cbf, reconstructs V and computes eigenvalues and
 * eigenvectors of V*V^H, then sends one framed binary packet containing:
 *   src_mac, num_streams, num_rows, num_subcarriers, phy_type, is_mu,
 *   bandwidth, SNR per stream, and per subcarrier:
 *     phi angles, psi angles, eigenvalues, eigenvectors (real + imag).
 *
 * Wire format:
 *   [MAGIC 4B: CB F1 FE ED][PAYLOAD_LEN 2B LE][PAYLOAD][XOR_CKSUM 1B]
 *
 * cbf : (in) const wifi_cbf_result_t* — parsed CBF result
 *
 * Returns true on success.
 */
bool telemetry_send_cbf(const wifi_cbf_result_t *cbf);

/*
 * telemetry_send_csi - Serialize and emit a binary CSI telemetry packet.
 *
 * Wire format:
 *   [MAGIC 4B: C5 1D FE ED][PAYLOAD_LEN 2B LE][PAYLOAD][XOR_CKSUM 1B]
 *
 * Payload: src_mac(6) dst_mac(6) rssi(1) channel(1)
 *          num_samples(2 LE) data[num_samples]
 *
 * data contains interleaved signed int8 I/Q values (I0,Q0,I1,Q1,...).
 *
 * csi : (in) const wifi_csi_result_t* — parsed CSI result
 *
 * Returns true on success.
 */
bool telemetry_send_csi(const wifi_csi_result_t *csi);

#endif /* TELEMETRY_H */
