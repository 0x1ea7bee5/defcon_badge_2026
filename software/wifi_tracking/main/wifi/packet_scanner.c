#include <string.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_wifi.h"
#include "packet_scanner.h"
#include "wifi_defs.h"

static const char *TAG = "pkt_scan";

/* Internal scanner state */
static struct {
    cbf_cb_t  on_cbf;
    ndpa_cb_t on_ndpa;
    ssid_cb_t on_ssid;
    bool      active;
} s_scanner;

/* ---------- Bit extraction ---------- */

/*
 * extract_bits - Extract up to 16 bits LSB-first from a byte array.
 *
 * buf        : (in) uint8_t* source buffer
 * bit_offset : (in) uint32_t starting bit position (bit 0 = LSB of buf[0])
 * nbits      : (in) uint8_t number of bits to extract (1-16)
 *
 * Returns uint16_t extracted value. Caller must ensure buf has at least
 * (bit_offset + nbits + 7) / 8 bytes available.
 */
static uint16_t extract_bits(const uint8_t *buf, uint32_t bit_offset,
                              uint8_t nbits)
{
    uint32_t byte_idx = bit_offset / 8;
    uint8_t  shift    = (uint8_t)(bit_offset % 8);
    uint32_t raw      = (uint32_t)buf[byte_idx] >> shift;

    if (shift + nbits > 8)
        raw |= (uint32_t)buf[byte_idx + 1] << (8 - shift);
    if (shift + nbits > 16)
        raw |= (uint32_t)buf[byte_idx + 2] << (16 - shift);

    return (uint16_t)(raw & ((1u << nbits) - 1));
}

/* ---------- Frame type predicates ---------- */

static inline bool is_action_frame(const uint8_t *fc)
{
    return (fc[0] & (FC_TYPE_MASK | FC_SUBTYPE_MASK)) ==
           (FC_TYPE_MGMT | FC_SUBTYPE_ACTION);
}

static inline bool is_action_noack_frame(const uint8_t *fc)
{
    return (fc[0] & (FC_TYPE_MASK | FC_SUBTYPE_MASK)) ==
           (FC_TYPE_MGMT | FC_SUBTYPE_ACTION_NOACK);
}

static inline bool is_beacon_frame(const uint8_t *fc)
{
    return (fc[0] & (FC_TYPE_MASK | FC_SUBTYPE_MASK)) ==
           (FC_TYPE_MGMT | FC_SUBTYPE_BEACON);
}

static inline bool is_ndpa_frame(const uint8_t *fc)
{
    return (fc[0] & (FC_TYPE_MASK | FC_SUBTYPE_MASK)) ==
           (FC_TYPE_CTRL | FC_SUBTYPE_NDPA);
}

/* ---------- Angle count helpers ---------- */

/*
 * phi_count - Number of phi Givens rotation angles per subcarrier.
 *
 * nc : (in) uint8_t number of transmit columns (streams sounded)
 * nr : (in) uint8_t number of receive rows
 *
 * Returns uint8_t angle count. Formula: Nc*Nr - Nc*(Nc+1)/2.
 */
static uint8_t phi_count(uint8_t nc, uint8_t nr)
{
    return (uint8_t)(nc * nr - nc * (nc + 1) / 2);
}

/*
 * psi_count - Number of psi Givens rotation angles per subcarrier.
 *
 * nc : (in) uint8_t number of transmit columns
 *
 * Returns uint8_t angle count. Formula: Nc*(Nc-1)/2.
 */
static uint8_t psi_count(uint8_t nc)
{
    return (uint8_t)(nc * (nc - 1) / 2);
}

/* ---------- Subcarrier count lookup ---------- */

/*
 * subcarrier_count - Return subcarrier count for a given bandwidth and
 *                   grouping index.
 *
 * bw  : (in) wifi_bw_t bandwidth enum
 * ng  : (in) uint8_t grouping index (0=Ng1, 1=Ng2, 2=Ng4)
 *
 * Returns uint16_t subcarrier count, or 0 for unsupported combination.
 */
static uint16_t subcarrier_count(wifi_bw_t bw, uint8_t ng)
{
    static const uint16_t tbl[4][3] = {
        { VHT_NSC_20_NG1, VHT_NSC_20_NG2, VHT_NSC_20_NG4 },
        { VHT_NSC_40_NG1, VHT_NSC_40_NG2, VHT_NSC_40_NG4 },
        { VHT_NSC_80_NG1, VHT_NSC_80_NG2, VHT_NSC_80_NG4 },
        { VHT_NSC_160_NG1, VHT_NSC_160_NG2, VHT_NSC_160_NG4 },
    };

    if (bw > WIFI_BW_160MHZ || ng > 2)
        return 0;
    return tbl[(uint8_t)bw][ng];
}

/* ---------- CBF angle allocation and parsing ---------- */

/*
 * alloc_angles - Allocate phi and psi arrays in a wifi_cbf_result_t.
 *
 * result : (in/out) result with phi_count, psi_count, num_subcarriers set
 *
 * Returns true on success, false on allocation failure.
 * On failure, phi and psi are both NULL.
 */
static bool alloc_angles(wifi_cbf_result_t *result)
{
    uint32_t phi_total = (uint32_t)result->num_subcarriers *
                         result->phi_count;
    uint32_t psi_total = (uint32_t)result->num_subcarriers *
                         result->psi_count;

    result->phi = (int16_t *)calloc(phi_total, sizeof(int16_t));
    if (!result->phi)
        return false;

    if (psi_total > 0) {
        result->psi = (int16_t *)calloc(psi_total, sizeof(int16_t));
        if (!result->psi) {
            free(result->phi);
            result->phi = NULL;
            return false;
        }
    }
    return true;
}

/*
 * parse_angles - Extract phi and psi Givens angles from packed report data.
 *
 * report     : (in)  uint8_t* start of the beamforming report field
 * report_len : (in)  uint16_t bytes available in report
 * nc         : (in)  uint8_t columns (streams)
 * nr         : (in)  uint8_t rows (receive antennas)
 * bphi       : (in)  uint8_t bits per phi angle
 * bpsi       : (in)  uint8_t bits per psi angle
 * result     : (out) phi and psi arrays filled in
 *
 * Returns true on success, false if report is too short.
 *
 * Angle ordering per subcarrier matches 802.11-2020:
 *   phi_{l,m} for l=0..Nc-1, m=l+1..Nr-1  (Nr-l-1 phi per column l)
 *   psi_{l,m} for l=0..Nc-1, m=l+1..Nc-1  (Nc-l-1 psi per column l)
 */
static bool parse_angles(const uint8_t *report, uint16_t report_len,
                          uint8_t nc, uint8_t nr,
                          uint8_t bphi, uint8_t bpsi,
                          wifi_cbf_result_t *result)
{
    uint32_t bits_per_sc = (uint32_t)result->phi_count * bphi +
                           (uint32_t)result->psi_count * bpsi;
    uint32_t total_bits  = bits_per_sc * result->num_subcarriers;

    if ((total_bits + 7) / 8 > report_len)
        return false;

    uint32_t bit_pos = 0;
    uint16_t sc;

    for (sc = 0; sc < result->num_subcarriers; sc++) {
        uint8_t phi_idx = 0;
        uint8_t l, m;

        for (l = 0; l < nc; l++) {
            for (m = l + 1; m < nr; m++) {
                result->phi[sc * result->phi_count + phi_idx] =
                    (int16_t)extract_bits(report, bit_pos, bphi);
                phi_idx++;
                bit_pos += bphi;
            }
        }

        uint8_t psi_idx = 0;
        for (l = 0; l < nc; l++) {
            for (m = l + 1; m < nc; m++) {
                result->psi[sc * result->psi_count + psi_idx] =
                    (int16_t)extract_bits(report, bit_pos, bpsi);
                psi_idx++;
                bit_pos += bpsi;
            }
        }
    }
    return true;
}

/*
 * parse_snr - Extract per-stream SNR bytes following the angle report.
 *
 * buf    : (in)  uint8_t* pointer to start of SNR bytes
 * avail  : (in)  uint16_t available bytes
 * result : (out) snr[] populated up to num_streams entries
 *
 * SNR values are signed 8-bit in units of 0.25 dB.
 */
static void parse_snr(const uint8_t *buf, uint16_t avail,
                       wifi_cbf_result_t *result)
{
    uint8_t i;
    for (i = 0; i < result->num_streams && i < avail; i++)
        result->snr[i] = (int8_t)buf[i];
}

/* ---------- VHT CBF parsing ---------- */

/*
 * parse_vht_cbf - Parse a VHT Compressed Beamforming action frame body.
 *
 * body     : (in)  uint8_t* frame body (after category and action bytes)
 * body_len : (in)  uint16_t available bytes in body
 * result   : (out) populated on success
 *
 * Returns true on success.
 */
static bool parse_vht_cbf(const uint8_t *body, uint16_t body_len,
                            wifi_cbf_result_t *result)
{
    /* body: category(1) action(1) MIMO_ctrl(3) report(...) SNR(...) */
    if (body_len < 5)
        return false;

    const uint8_t *mimo = body + 2;
    uint8_t nc  = (mimo[0] & VHT_MIMO_NC_MASK) + 1;
    uint8_t nr  = ((mimo[0] & VHT_MIMO_NR_MASK) >> VHT_MIMO_NR_SHIFT) + 1;
    uint8_t bw  = (mimo[0] & VHT_MIMO_BW_MASK) >> VHT_MIMO_BW_SHIFT;
    uint8_t ng  = mimo[1] & VHT_MIMO_GROUPING_MASK;

    /* Token spans byte[1] bits[7:5] (low 3) and byte[2] bits[1:0] (high 2) */
    uint8_t tok = (uint8_t)(
        ((mimo[1] & VHT_MIMO_TOKEN_LOW_MASK) >> VHT_MIMO_TOKEN_LOW_SHIFT) |
        ((mimo[2] & VHT_MIMO_TOKEN_HI_MASK)  << VHT_MIMO_TOKEN_HI_SHIFT));

    result->phy_type        = WIFI_PHY_VHT;
    result->bandwidth       = (wifi_bw_t)bw;
    result->dialog_token    = tok;
    result->num_streams     = nc;
    result->num_rows        = nr;
    result->phi_count       = phi_count(nc, nr);
    result->psi_count       = psi_count(nc);
    result->num_subcarriers = subcarrier_count((wifi_bw_t)bw, ng);

    if (result->num_subcarriers == 0 || result->phi_count == 0)
        return false;

    if (!alloc_angles(result))
        return false;

    const uint8_t *report = body + 5;
    uint16_t report_len   = body_len - 5;

    if (!parse_angles(report, report_len, nc, nr,
                       VHT_BPHI, VHT_BPSI, result)) {
        wifi_cbf_result_free(result);
        return false;
    }

    uint32_t angle_bits = (uint32_t)result->num_subcarriers *
                          (result->phi_count * VHT_BPHI +
                           result->psi_count * VHT_BPSI);
    uint32_t snr_off = 5 + (angle_bits + 7) / 8;

    if (snr_off < body_len)
        parse_snr(body + snr_off, (uint16_t)(body_len - snr_off), result);

    return true;
}

/* ---------- HE CBF parsing ---------- */

/*
 * parse_he_cbf - Parse an HE Compressed Beamforming action body.
 *
 * body     : (in)  uint8_t* frame body starting at category byte
 * body_len : (in)  uint16_t available bytes
 * result   : (out) populated on success
 *
 * Returns true on success.
 */
static bool parse_he_cbf(const uint8_t *body, uint16_t body_len,
                           wifi_cbf_result_t *result)
{
    /* body: category(1) action(1) MIMO_ctrl(6) report(...) */
    if (body_len < 8)
        return false;

    const uint8_t *mimo = body + 2;
    uint8_t nc   = (mimo[0] & HE_MIMO_NC_MASK) + 1;
    uint8_t nr   = ((mimo[0] & HE_MIMO_NR_MASK) >> HE_MIMO_NR_SHIFT) + 1;
    uint8_t bw   = (mimo[0] & HE_MIMO_BW_MASK) >> HE_MIMO_BW_SHIFT;
    uint8_t ng   = mimo[1] & HE_MIMO_GROUPING_MASK;
    bool    cb4  = (mimo[1] & HE_MIMO_CODEBOOK_MASK) != 0;
    uint8_t tok  = mimo[HE_MIMO_TOKEN_BYTE_IDX];

    uint8_t bphi = cb4 ? HE_BPHI_CB4 : HE_BPHI_CB7;
    uint8_t bpsi = cb4 ? HE_BPSI_CB4 : HE_BPSI_CB7;

    result->phy_type        = WIFI_PHY_HE;
    result->bandwidth       = (wifi_bw_t)bw;
    result->dialog_token    = tok;
    result->num_streams     = nc;
    result->num_rows        = nr;
    result->phi_count       = phi_count(nc, nr);
    result->psi_count       = psi_count(nc);
    result->num_subcarriers = subcarrier_count((wifi_bw_t)bw, ng);

    if (result->num_subcarriers == 0 || result->phi_count == 0)
        return false;

    if (!alloc_angles(result))
        return false;

    const uint8_t *report = body + 8;
    uint16_t report_len   = body_len - 8;

    if (!parse_angles(report, report_len, nc, nr,
                       bphi, bpsi, result)) {
        wifi_cbf_result_free(result);
        return false;
    }

    uint32_t angle_bits = (uint32_t)result->num_subcarriers *
                          (result->phi_count * bphi +
                           result->psi_count * bpsi);
    uint32_t snr_off = 8 + (angle_bits + 7) / 8;

    if (snr_off < body_len)
        parse_snr(body + snr_off, (uint16_t)(body_len - snr_off), result);

    return true;
}

/* ---------- Public API ---------- */

bool scan_for_cbf(const uint8_t *frame, uint16_t len,
                  const wifi_pkt_rx_ctrl_t *rx_ctrl,
                  wifi_cbf_result_t *out)
{
    const uint16_t min_len = MGMT_HDR_BODY_OFFSET + 2;

    if (!frame || !rx_ctrl || !out || len < min_len)
        return false;

    const uint8_t *fc = frame + MGMT_HDR_FC_OFFSET;
    if (!is_action_frame(fc) && !is_action_noack_frame(fc))
        return false;

    const uint8_t *body     = frame + MGMT_HDR_BODY_OFFSET;
    uint16_t       body_len = len - MGMT_HDR_BODY_OFFSET;
    uint8_t        cat      = body[0];
    uint8_t        act      = body[1];

    memset(out, 0, sizeof(*out));

    bool matched = false;

    if (cat == ACTION_CAT_VHT && act == VHT_ACTION_CBF)
        matched = parse_vht_cbf(body, body_len, out);
    else if (cat == ACTION_CAT_HE && act == HE_ACTION_CBF_CQI)
        matched = parse_he_cbf(body, body_len, out);

    if (!matched)
        return false;

    memcpy(out->src_mac, frame + MGMT_HDR_SA_OFFSET, WIFI_MAC_ADDR_LEN);
    out->timestamp_us = (int64_t)rx_ctrl->timestamp;
    out->rssi         = (int8_t)rx_ctrl->rssi;
    out->channel      = (uint8_t)rx_ctrl->channel;

    return true;
}

bool scan_for_ndpa(const uint8_t *frame, uint16_t len,
                   wifi_ndpa_result_t *out)
{
    /* Min: FC(2) + Dur(2) + RA(6) + TA(6) + Token(1) = 17 bytes */
    const uint16_t min_len = CTRL_NDPA_TOKEN_OFFSET + 1;

    if (!frame || !out || len < min_len)
        return false;

    if (!is_ndpa_frame(frame + CTRL_HDR_FC_OFFSET))
        return false;

    memset(out, 0, sizeof(*out));
    memcpy(out->dst_mac, frame + CTRL_HDR_RA_OFFSET, WIFI_MAC_ADDR_LEN);
    memcpy(out->src_mac, frame + CTRL_HDR_TA_OFFSET, WIFI_MAC_ADDR_LEN);
    out->dialog_token = frame[CTRL_NDPA_TOKEN_OFFSET];

    return true;
}

/* ---------- IE parsing helpers ---------- */

/*
 * find_ie - Search for an Information Element by ID in an IE buffer.
 *
 * ies     : (in)  uint8_t* start of IEs
 * ies_len : (in)  uint16_t total bytes in IE buffer
 * ie_id   : (in)  uint8_t IE ID to find
 * ie_len  : (out) uint8_t* length of IE value field (excluding id/len bytes)
 *
 * Returns pointer to IE value, or NULL if not found.
 */
static const uint8_t *find_ie(const uint8_t *ies, uint16_t ies_len,
                               uint8_t ie_id, uint8_t *ie_len)
{
    uint16_t off = 0;

    while (off + 2 <= ies_len) {
        uint8_t id  = ies[off];
        uint8_t len = ies[off + 1];

        if (off + 2 + len > ies_len)
            break;

        if (id == ie_id) {
            if (ie_len)
                *ie_len = len;
            return ies + off + 2;
        }
        off += 2 + len;
    }
    return NULL;
}

/*
 * find_ie_ext - Search for an Extended IE (ID=255) by extension ID.
 *
 * ies     : (in)  uint8_t* start of IEs
 * ies_len : (in)  uint16_t total IE buffer length
 * ext_id  : (in)  uint8_t extension ID byte
 * ie_len  : (out) uint8_t* length of value after the extension ID byte
 *
 * Returns pointer to value (past ext_id byte), or NULL if not found.
 */
static const uint8_t *find_ie_ext(const uint8_t *ies, uint16_t ies_len,
                                   uint8_t ext_id, uint8_t *ie_len)
{
    uint16_t off = 0;

    while (off + 2 <= ies_len) {
        uint8_t id  = ies[off];
        uint8_t len = ies[off + 1];

        if (off + 2 + len > ies_len)
            break;

        if (id == IE_ID_EXTENSION && len >= 1 &&
            ies[off + 2] == ext_id) {
            if (ie_len)
                *ie_len = (uint8_t)(len - 1);
            return ies + off + 3;
        }
        off += 2 + len;
    }
    return NULL;
}

/*
 * append_rates - Append rates from an IE value into a wifi_rate_set_t.
 *
 * ie     : (in)     uint8_t* IE value bytes
 * ie_len : (in)     uint8_t IE value length
 * rates  : (in/out) wifi_rate_set_t* rate set to append to (max 16 entries)
 */
static void append_rates(const uint8_t *ie, uint8_t ie_len,
                          wifi_rate_set_t *rates)
{
    uint8_t i;
    for (i = 0; i < ie_len && rates->count < 16; i++)
        rates->rates[rates->count++] = ie[i];
}

bool scan_for_ssid(const uint8_t *frame, uint16_t len,
                   const wifi_pkt_rx_ctrl_t *rx_ctrl,
                   wifi_ap_info_t *out)
{
    const uint16_t min_len = MGMT_HDR_BODY_OFFSET + BCN_IES_OFFSET;

    if (!frame || !rx_ctrl || !out || len < min_len)
        return false;

    if (!is_beacon_frame(frame + MGMT_HDR_FC_OFFSET))
        return false;

    memset(out, 0, sizeof(*out));
    memcpy(out->src_mac, frame + MGMT_HDR_SA_OFFSET, WIFI_MAC_ADDR_LEN);
    out->timestamp_us = (int64_t)rx_ctrl->timestamp;
    out->rssi         = (int8_t)rx_ctrl->rssi;
    out->channel      = (uint8_t)rx_ctrl->channel;

    const uint8_t *ies = frame + MGMT_HDR_BODY_OFFSET + BCN_IES_OFFSET;
    uint16_t ies_len   = len - MGMT_HDR_BODY_OFFSET - BCN_IES_OFFSET;

    uint8_t         ie_len = 0;
    const uint8_t  *ie_val;

    ie_val = find_ie(ies, ies_len, IE_ID_SSID, &ie_len);
    if (ie_val && ie_len <= 32) {
        memcpy(out->ssid, ie_val, ie_len);
        out->ssid[ie_len] = '\0';
    }

    ie_val = find_ie(ies, ies_len, IE_ID_SUPP_RATES, &ie_len);
    if (ie_val)
        append_rates(ie_val, ie_len, &out->rates);

    ie_val = find_ie(ies, ies_len, IE_ID_EXT_SUPP_RATES, &ie_len);
    if (ie_val)
        append_rates(ie_val, ie_len, &out->rates);

    ie_val = find_ie(ies, ies_len, IE_ID_HT_CAP, &ie_len);
    if (ie_val && ie_len >= 2) {
        out->cap_flags  |= WIFI_CAP_HT;
        out->ht_cap_info = (uint16_t)(ie_val[0] | ((uint16_t)ie_val[1] << 8));
    }

    ie_val = find_ie(ies, ies_len, IE_ID_VHT_CAP, &ie_len);
    if (ie_val && ie_len >= 4) {
        out->cap_flags   |= WIFI_CAP_VHT;
        out->vht_cap_info = (uint32_t)ie_val[0]              |
                            ((uint32_t)ie_val[1] << 8)        |
                            ((uint32_t)ie_val[2] << 16)       |
                            ((uint32_t)ie_val[3] << 24);
    }

    ie_val = find_ie_ext(ies, ies_len, IE_EXT_ID_HE_CAP, &ie_len);
    if (ie_val)
        out->cap_flags |= WIFI_CAP_HE;

    return true;
}

/* ---------- Monitor mode ---------- */

/*
 * promiscuous_rx_cb - ESP-IDF promiscuous callback; dispatches each
 *                    received frame to the registered scanner functions.
 *
 * buf  : (in) void* wifi_promiscuous_pkt_t from the driver
 * type : (in) wifi_promiscuous_pkt_type_t packet type classification
 */
static void promiscuous_rx_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    if (!buf)
        return;

    const wifi_promiscuous_pkt_t *pkt =
        (const wifi_promiscuous_pkt_t *)buf;
    const uint8_t *frame = pkt->payload;
    uint16_t       flen  = (uint16_t)pkt->rx_ctrl.sig_len;

    if (flen == 0)
        return;

    if (s_scanner.on_cbf) {
        wifi_cbf_result_t cbf = {0};
        if (scan_for_cbf(frame, flen, &pkt->rx_ctrl, &cbf)) {
            s_scanner.on_cbf(&cbf);
            wifi_cbf_result_free(&cbf);
        }
    }

    if (s_scanner.on_ndpa) {
        wifi_ndpa_result_t ndpa = {0};
        if (scan_for_ndpa(frame, flen, &ndpa))
            s_scanner.on_ndpa(&ndpa);
    }

    if (s_scanner.on_ssid) {
        wifi_ap_info_t ap = {0};
        if (scan_for_ssid(frame, flen, &pkt->rx_ctrl, &ap))
            s_scanner.on_ssid(&ap);
    }
}

esp_err_t start_monitor(uint8_t channel,
                         cbf_cb_t  on_cbf,
                         ndpa_cb_t on_ndpa,
                         ssid_cb_t on_ssid)
{
    if (s_scanner.active) {
        ESP_LOGW(TAG, "monitor already active");
        return ESP_ERR_INVALID_STATE;
    }

    s_scanner.on_cbf  = on_cbf;
    s_scanner.on_ndpa = on_ndpa;
    s_scanner.on_ssid = on_ssid;

    wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT |
                       WIFI_PROMIS_FILTER_MASK_CTRL,
    };

    esp_err_t ret;

    ret = esp_wifi_set_promiscuous_filter(&filter);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "set filter failed: %d", ret);
        return ret;
    }

    ret = esp_wifi_set_promiscuous_rx_cb(promiscuous_rx_cb);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "set rx cb failed: %d", ret);
        return ret;
    }

    ret = esp_wifi_set_promiscuous(true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "enable promiscuous failed: %d", ret);
        return ret;
    }

    ret = switch_channel(channel);
    if (ret != ESP_OK) {
        esp_wifi_set_promiscuous(false);
        return ret;
    }

    s_scanner.active = true;
    ESP_LOGI(TAG, "monitor started on channel %u", channel);
    return ESP_OK;
}

esp_err_t switch_channel(uint8_t channel)
{
    esp_err_t ret = esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
    if (ret != ESP_OK)
        ESP_LOGE(TAG, "channel switch to %u failed: %d", channel, ret);
    return ret;
}

esp_err_t stop_monitor(void)
{
    if (!s_scanner.active)
        return ESP_OK;

    esp_err_t ret = esp_wifi_set_promiscuous(false);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "disable promiscuous failed: %d", ret);
        return ret;
    }

    memset(&s_scanner, 0, sizeof(s_scanner));
    ESP_LOGI(TAG, "monitor stopped");
    return ESP_OK;
}

void wifi_cbf_result_free(wifi_cbf_result_t *result)
{
    if (!result)
        return;
    free(result->phi);
    free(result->psi);
    result->phi = NULL;
    result->psi = NULL;
}
