/*
 * test_packet_scanner.c
 *
 * Unit tests for packet_scanner.c using Unity + CMock.
 *
 * Parse functions (scan_for_cbf, scan_for_ndpa, scan_for_ssid) and
 * wifi_cbf_result_free are tested with raw frame byte arrays; they use
 * only stdlib and require no ESP-IDF mocking.
 *
 * Monitor mode functions (start_monitor, switch_channel, stop_monitor)
 * call ESP-IDF WiFi APIs and use the mock_esp_wifi mock.
 *
 * Build (host):
 *   gcc -I main/tests/support -I main/wifi -I main/tests \
 *       main/wifi/packet_scanner.c \
 *       main/tests/mock_esp_wifi.c \
 *       main/tests/test_packet_scanner.c \
 *       unity/src/unity.c \
 *       -o test_packet_scanner && ./test_packet_scanner
 */

#include "unity.h"
#include "mock_esp_wifi.h"
#include "packet_scanner.h"
#include "wifi_defs.h"

#include <string.h>
#include <stdlib.h>

/* ================================================================
 * Frame builder helpers
 * ================================================================ */

/*
 * make_rx_ctrl - Fill a wifi_pkt_rx_ctrl_t with test values.
 *
 * rssi      : (in) int8_t
 * channel   : (in) uint8_t
 * timestamp : (in) uint32_t microseconds
 * sig_len   : (in) uint16_t total frame length
 *
 * Returns wifi_pkt_rx_ctrl_t.
 */
static wifi_pkt_rx_ctrl_t make_rx_ctrl(int8_t rssi, uint8_t channel,
                                        uint32_t timestamp,
                                        uint16_t sig_len)
{
    wifi_pkt_rx_ctrl_t r;
    memset(&r, 0, sizeof(r));
    r.rssi      = rssi;
    r.channel   = channel;
    r.timestamp = timestamp;
    r.sig_len   = sig_len;
    return r;
}

/*
 * write_mac - Write a 6-byte MAC address into buf at offset.
 */
static void write_mac(uint8_t *buf, size_t offset, const uint8_t *mac)
{
    memcpy(buf + offset, mac, WIFI_MAC_ADDR_LEN);
}

/*
 * build_mgmt_header - Fill the standard 24-byte 802.11 management header.
 *
 * buf   : (in/out) frame buffer (must be >= 24 bytes)
 * fc0   : (in)     FC byte 0 (type+subtype)
 * da    : (in)     6-byte destination address
 * sa    : (in)     6-byte source address
 * bssid : (in)     6-byte BSSID
 */
static void build_mgmt_header(uint8_t *buf, uint8_t fc0,
                               const uint8_t *da,
                               const uint8_t *sa,
                               const uint8_t *bssid)
{
    memset(buf, 0, 24);
    buf[0] = fc0;
    buf[1] = 0x00;
    write_mac(buf, MGMT_HDR_DA_OFFSET,    da);
    write_mac(buf, MGMT_HDR_SA_OFFSET,    sa);
    write_mac(buf, MGMT_HDR_BSSID_OFFSET, bssid);
}

/* ================================================================
 * Pre-built test frames
 *
 * VHT_CBF_FRAME: Action frame, Nc=1, Nr=2, BW=20 MHz, Ng=4 (index=2)
 *   phi_count = 1*2 - 1*2/2 = 1
 *   psi_count = 1  (equals phi_count; one psi per phi pair)
 *   num_subcarriers = VHT_NSC_20_NG4 = 13
 *   angle data: 13 SCs * (7+5) bits = 156 bits → 20 bytes of 0xFF
 *     each 7-bit phi = 0x7F = 127, each 5-bit psi = 0x1F = 31
 *   dialog_token = 7
 *     token_low (bits[2:0]) = 7 → mimo[1] bits[7:5] = 7 << 5 = 0xE0
 *     token_hi  (bits[4:3]) = 0 → mimo[2] bits[1:0] = 0
 *   SNR[0] = 40 (40 * 0.25 = 10 dB) at body[25]
 *   src_mac = {0xAA,0xBB,0xCC,0xDD,0xEE,0xFF}
 * ================================================================ */

static const uint8_t VHT_SRC_MAC[6]  = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
static const uint8_t BCAST_MAC[6]    = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static const uint8_t BSSID_MAC[6]    = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55};

#define VHT_CBF_FRAME_LEN  50u
static uint8_t vht_cbf_frame[VHT_CBF_FRAME_LEN];

static void build_vht_cbf_frame(void)
{
    memset(vht_cbf_frame, 0, sizeof(vht_cbf_frame));
    build_mgmt_header(vht_cbf_frame,
                      FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = vht_cbf_frame + MGMT_HDR_BODY_OFFSET;
    body[0] = ACTION_CAT_VHT;       /* category */
    body[1] = VHT_ACTION_CBF;       /* action   */
    /* MIMO ctrl byte 0: Nc-1=0, Nr-1=1 (<<3), BW=0 (<<6)  */
    body[2] = 0x08;
    /* MIMO ctrl byte 1: Ng=4 index=2, token_low=7 (<<5)    */
    body[3] = 0xE2;
    /* MIMO ctrl byte 2: token_hi=0                           */
    body[4] = 0x00;
    /* 20 bytes of angle data (13 SCs * 12 bits; all 0xFF)   *
     *   interleaved: phi=0x7F (7 bits), psi=0x1F (5 bits)   */
    memset(body + 5, 0xFF, 20);
    /* SNR for stream 0: at body[25] = 5 + ceil(156/8)       */
    body[25] = 40;
}

/*
 * HE_CBF_FRAME: Action (no HTC), Nc=2, Nr=2, BW=20 MHz, Ng=4,
 *               Codebook=0 (SU CB0), FBType=SU
 *   phi_count = 2*2 - 2*3/2 = 1
 *   psi_count = 2*1/2 = 1
 *   num_subcarriers = 13  (HE_NSC_20_NG4)
 *   bphi=4, bpsi=2 → bits/SC=6 → 78 bits → 10 bytes of 0xFF
 *     per SC: phi = bits[3:0] = 0xF = 15
 *             psi = bits[5:4] = 0x3 = 3
 *   dialog_token = 5  (mimo[3] bits[5:0])
 *   SNR[0]=20, SNR[1]=30
 *   src_mac = VHT_SRC_MAC
 *
 * HE MIMO ctrl is 4 bytes: cat(1)+act(1)+mimo(4) = 6, report at body+6
 */
#define HE_CBF_FRAME_LEN  42u
static uint8_t he_cbf_frame[HE_CBF_FRAME_LEN];

static void build_he_cbf_frame(void)
{
    memset(he_cbf_frame, 0, sizeof(he_cbf_frame));
    build_mgmt_header(he_cbf_frame,
                      FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = he_cbf_frame + MGMT_HDR_BODY_OFFSET;
    body[0] = ACTION_CAT_HE;        /* category */
    body[1] = HE_ACTION_CBF_CQI;   /* action   */
    /* mimo[0]: Nc-1=1, Nr-1=1 (<<3), BW=0 (<<6) */
    body[2] = 0x09;
    /* mimo[1]: Ng=4→index=2, codebook=0, FBType=SU */
    body[3] = 0x02;
    /* mimo[2]: RU end tone = 0 */
    body[4] = 0x00;
    /* mimo[3]: dialog token=5 in bits[5:0] */
    body[5] = 0x05;
    /* HE report: SNR (Nc=2 bytes) first, then angle data */
    body[6] = 20;                /* SNR[0] */
    body[7] = 30;                /* SNR[1] */
    memset(body + 8, 0xFF, 10); /* 10 bytes of angle data (all 0xFF) */
}

/*
 * NDPA_FRAME: Control NDPA frame
 *   dst_mac = BCAST_MAC
 *   src_mac = {0x11,0x22,0x33,0x44,0x55,0x66}
 *   dialog_token = 42
 */
static const uint8_t NDPA_SRC_MAC[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const uint8_t NDPA_DST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

#define NDPA_FRAME_LEN  17u
static uint8_t ndpa_frame[NDPA_FRAME_LEN];

static void build_ndpa_frame(void)
{
    memset(ndpa_frame, 0, sizeof(ndpa_frame));
    ndpa_frame[0] = FC_TYPE_CTRL | FC_SUBTYPE_NDPA;
    ndpa_frame[1] = 0x00;
    write_mac(ndpa_frame, CTRL_HDR_RA_OFFSET, NDPA_DST_MAC);
    write_mac(ndpa_frame, CTRL_HDR_TA_OFFSET, NDPA_SRC_MAC);
    ndpa_frame[CTRL_NDPA_TOKEN_OFFSET] = 42;
}

/*
 * BEACON_FRAME: Management Beacon frame
 *   src_mac = VHT_SRC_MAC
 *   ssid = "TestAP"
 *   supported rates: 4 rates
 *   HT cap: ht_cap_info = 0x012C
 *   VHT cap: vht_cap_info = 0x00000033
 *   HE cap: present (IE extension 0x23)
 */
#define BCN_FRAME_LEN  100u
static uint8_t bcn_frame[BCN_FRAME_LEN];

static void build_beacon_frame(void)
{
    memset(bcn_frame, 0, sizeof(bcn_frame));
    build_mgmt_header(bcn_frame,
                      FC_TYPE_MGMT | FC_SUBTYPE_BEACON,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = bcn_frame + MGMT_HDR_BODY_OFFSET;

    /* Fixed fields: timestamp(8) + interval(2) + capability(2) = 12 */
    body[8]  = 0x64;  /* interval low  = 100 TUs */
    body[9]  = 0x00;
    body[10] = 0x01;  /* capability    */
    body[11] = 0x04;

    /* IEs start at body[12] (BCN_IES_OFFSET = 12) */
    uint8_t *ie = body + BCN_IES_OFFSET;

    /* SSID = "TestAP" (6 bytes) */
    *ie++ = IE_ID_SSID;
    *ie++ = 6;
    memcpy(ie, "TestAP", 6); ie += 6;

    /* Supported Rates: 4 rates */
    *ie++ = IE_ID_SUPP_RATES;
    *ie++ = 4;
    *ie++ = 0x82;  /* 1 Mbps, basic */
    *ie++ = 0x84;  /* 2 Mbps, basic */
    *ie++ = 0x8B;  /* 5.5 Mbps, basic */
    *ie++ = 0x96;  /* 11 Mbps, basic */

    /* HT Capabilities (ID=45, len=26): ht_cap_info = 0x012C */
    *ie++ = IE_ID_HT_CAP;
    *ie++ = 26;
    *ie++ = 0x2C;  /* ht_cap_info low  */
    *ie++ = 0x01;  /* ht_cap_info high */
    memset(ie, 0, 24); ie += 24;

    /* VHT Capabilities (ID=191, len=12): vht_cap_info = 0x00000033 */
    *ie++ = IE_ID_VHT_CAP;
    *ie++ = 12;
    *ie++ = 0x33;  /* vht_cap_info byte 0 */
    *ie++ = 0x00;
    *ie++ = 0x00;
    *ie++ = 0x00;
    memset(ie, 0, 8); ie += 8;

    /* HE Capabilities (ID=255, ext_id=35, len=6) */
    *ie++ = IE_ID_EXTENSION;
    *ie++ = 6;
    *ie++ = IE_EXT_ID_HE_CAP;
    memset(ie, 0, 5); ie += 5;

    /* Verify we didn't overflow */
    TEST_ASSERT_LESS_OR_EQUAL(BCN_FRAME_LEN,
        (size_t)(ie - bcn_frame));
}

/* ================================================================
 * setUp / tearDown
 * ================================================================ */

void setUp(void)
{
    mock_esp_wifi_Init();
    build_vht_cbf_frame();
    build_he_cbf_frame();
    build_ndpa_frame();
    build_beacon_frame();
}

void tearDown(void)
{
    mock_esp_wifi_Verify();
    mock_esp_wifi_Destroy();
}

/* ================================================================
 * wifi_cbf_result_free tests
 * ================================================================ */

/*
 * Input:  NULL pointer
 * Expect: no crash, no-op
 */
void test_cbf_result_free_null_is_safe(void)
{
    wifi_cbf_result_free(NULL);
}

/*
 * Input:  result with dynamically allocated phi and psi
 * Expect: phi and psi freed and set to NULL
 */
void test_cbf_result_free_clears_pointers(void)
{
    wifi_cbf_result_t r;
    memset(&r, 0, sizeof(r));
    r.phi = (int16_t *)malloc(sizeof(int16_t));
    r.psi = (int16_t *)malloc(sizeof(int16_t));
    TEST_ASSERT_NOT_NULL(r.phi);
    TEST_ASSERT_NOT_NULL(r.psi);

    wifi_cbf_result_free(&r);

    TEST_ASSERT_NULL(r.phi);
    TEST_ASSERT_NULL(r.psi);
}

/*
 * Input:  result with NULL phi and psi (already freed)
 * Expect: calling free again is safe (double-free guard)
 */
void test_cbf_result_free_twice_is_safe(void)
{
    wifi_cbf_result_t r;
    memset(&r, 0, sizeof(r));
    r.phi = (int16_t *)malloc(sizeof(int16_t));
    r.psi = (int16_t *)malloc(sizeof(int16_t));

    wifi_cbf_result_free(&r);
    wifi_cbf_result_free(&r);  /* phi/psi are NULL now — must not crash */
}

/* ================================================================
 * scan_for_cbf: null / bad argument tests
 * ================================================================ */

/*
 * Input:  NULL frame pointer
 * Expect: returns false
 */
void test_cbf_null_frame_returns_false(void)
{
    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 1000, VHT_CBF_FRAME_LEN);
    TEST_ASSERT_FALSE(scan_for_cbf(NULL, VHT_CBF_FRAME_LEN, &rx, &out));
}

/*
 * Input:  NULL rx_ctrl
 * Expect: returns false
 */
void test_cbf_null_rx_ctrl_returns_false(void)
{
    wifi_cbf_result_t out;
    TEST_ASSERT_FALSE(
        scan_for_cbf(vht_cbf_frame, VHT_CBF_FRAME_LEN, NULL, &out));
}

/*
 * Input:  NULL output struct
 * Expect: returns false
 */
void test_cbf_null_out_returns_false(void)
{
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 1000, VHT_CBF_FRAME_LEN);
    TEST_ASSERT_FALSE(
        scan_for_cbf(vht_cbf_frame, VHT_CBF_FRAME_LEN, &rx, NULL));
}

/*
 * Input:  frame shorter than minimum management header + 2 body bytes
 * Expect: returns false
 */
void test_cbf_frame_too_short_returns_false(void)
{
    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 1000, 10);
    TEST_ASSERT_FALSE(scan_for_cbf(vht_cbf_frame, 10, &rx, &out));
}

/*
 * Input:  Data frame (FC type = 0x08), valid length
 * Expect: returns false (wrong frame type)
 */
void test_cbf_data_frame_rejected(void)
{
    uint8_t frame[VHT_CBF_FRAME_LEN];
    memcpy(frame, vht_cbf_frame, sizeof(frame));
    frame[0] = FC_TYPE_DATA | 0x00;  /* data, subtype 0 */

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 1000, sizeof(frame));
    TEST_ASSERT_FALSE(scan_for_cbf(frame, sizeof(frame), &rx, &out));
}

/*
 * Input:  Action frame with unknown category (e.g. 99)
 * Expect: returns false
 */
void test_cbf_unknown_category_returns_false(void)
{
    uint8_t frame[VHT_CBF_FRAME_LEN];
    memcpy(frame, vht_cbf_frame, sizeof(frame));
    frame[MGMT_HDR_BODY_OFFSET] = 99;  /* unknown category */

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 1000, sizeof(frame));
    TEST_ASSERT_FALSE(scan_for_cbf(frame, sizeof(frame), &rx, &out));
}

/* ================================================================
 * scan_for_cbf: VHT happy path
 * ================================================================ */

/*
 * Input:  valid VHT CBF Action frame (Nc=1, Nr=2, BW=20 MHz, Ng=4,
 *         token=7, 12 bytes of 0xFF angle data, SNR[0]=40)
 *         rx_ctrl: rssi=-70, channel=36, timestamp=5000
 *
 * Expect:
 *   returns true
 *   phy_type     = WIFI_PHY_VHT
 *   bandwidth    = WIFI_BW_20MHZ
 *   num_streams  = 1
 *   num_rows     = 2
 *   dialog_token = 7
 *   phi_count    = 1
 *   psi_count    = 1  (equals phi_count; one psi per Givens pair)
 *   num_subcarriers = 13
 *   phi[0..12]   = 127  (7-bit 0xFF fields)
 *   psi[0..12]   = 31   (5-bit 0xFF fields, interleaved after each phi)
 *   src_mac      = VHT_SRC_MAC
 *   rssi         = -70
 *   channel      = 36
 *   timestamp_us = 5000
 *   snr[0]       = 40
 */
void test_cbf_vht_valid_frame_parsed(void)
{
    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-70, 36, 5000, VHT_CBF_FRAME_LEN);

    bool ok = scan_for_cbf(vht_cbf_frame, VHT_CBF_FRAME_LEN, &rx, &out);
    TEST_ASSERT_TRUE(ok);

    TEST_ASSERT_EQUAL_INT(WIFI_PHY_VHT, out.phy_type);
    TEST_ASSERT_EQUAL_INT(WIFI_BW_20MHZ, out.bandwidth);
    TEST_ASSERT_EQUAL_UINT8(1,  out.num_streams);
    TEST_ASSERT_EQUAL_UINT8(2,  out.num_rows);
    TEST_ASSERT_EQUAL_UINT8(7,  out.dialog_token);
    TEST_ASSERT_EQUAL_UINT8(1,  out.phi_count);
    TEST_ASSERT_EQUAL_UINT8(1,  out.psi_count);
    TEST_ASSERT_EQUAL_UINT16(13, out.num_subcarriers);

    TEST_ASSERT_NOT_NULL(out.phi);
    TEST_ASSERT_NOT_NULL(out.psi);

    for (int i = 0; i < 13; i++) {
        TEST_ASSERT_EQUAL_INT16(127, out.phi[i]);
        TEST_ASSERT_EQUAL_INT16(31,  out.psi[i]);
    }

    TEST_ASSERT_EQUAL_MEMORY(VHT_SRC_MAC, out.src_mac, WIFI_MAC_ADDR_LEN);
    TEST_ASSERT_EQUAL_INT8(-70,  out.rssi);
    TEST_ASSERT_EQUAL_UINT8(36,  out.channel);
    TEST_ASSERT_EQUAL_INT64(5000, out.timestamp_us);
    TEST_ASSERT_EQUAL_INT8(40,   out.snr[0]);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  same VHT frame but with FC Action No Ack subtype (0xE0)
 * Expect: also accepted and parsed identically
 */
void test_cbf_vht_action_noack_accepted(void)
{
    uint8_t frame[VHT_CBF_FRAME_LEN];
    memcpy(frame, vht_cbf_frame, sizeof(frame));
    frame[0] = FC_TYPE_MGMT | FC_SUBTYPE_ACTION_NOACK;

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 0, sizeof(frame));

    TEST_ASSERT_TRUE(scan_for_cbf(frame, sizeof(frame), &rx, &out));
    TEST_ASSERT_EQUAL_INT(WIFI_PHY_VHT, out.phy_type);
    TEST_ASSERT_EQUAL_UINT8(7, out.dialog_token);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  VHT CBF frame truncated so angle data doesn't fit
 *         (only 5 header bytes in body — MIMO ctrl with no report)
 * Expect: returns false
 */
void test_cbf_vht_report_too_short_returns_false(void)
{
    /* Keep MAC header (24) + cat(1)+act(1)+mimo(3) = 29 bytes total */
    uint8_t frame[29];
    memcpy(frame, vht_cbf_frame, 29);

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 0, 29);
    TEST_ASSERT_FALSE(scan_for_cbf(frame, 29, &rx, &out));
}

/*
 * Input:  VHT CBF frame with dialog token = 31 (max 5-bit value)
 *   token_low = 31 & 0x7 = 7  → mimo[1] bits[7:5] = 0xE0
 *   token_hi  = 31 >> 3 = 3   → mimo[2] bits[1:0] = 0x03
 * Expect: dialog_token = 31
 */
void test_cbf_vht_token_max(void)
{
    uint8_t frame[VHT_CBF_FRAME_LEN];
    memcpy(frame, vht_cbf_frame, sizeof(frame));

    uint8_t *body = frame + MGMT_HDR_BODY_OFFSET;
    body[3] = (body[3] & 0x1F) | 0xE0;  /* token_low = 7 */
    body[4] = 0x03;                       /* token_hi  = 3 */

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 0, sizeof(frame));

    TEST_ASSERT_TRUE(scan_for_cbf(frame, sizeof(frame), &rx, &out));
    TEST_ASSERT_EQUAL_UINT8(31, out.dialog_token);

    wifi_cbf_result_free(&out);
}

/* ================================================================
 * scan_for_cbf: HE happy path
 * ================================================================ */

/*
 * Input:  valid HE CBF Action frame (Nc=2, Nr=2, BW=20 MHz, Ng=4,
 *         codebook=4, token=5, 10 bytes of 0xFF angle data,
 *         SNR[0]=20, SNR[1]=30)
 *
 * Expect:
 *   returns true
 *   phy_type      = WIFI_PHY_HE
 *   bandwidth     = WIFI_BW_20MHZ
 *   num_streams   = 2
 *   num_rows      = 2
 *   dialog_token  = 5
 *   phi_count     = 1
 *   psi_count     = 1
 *   num_subcarriers = 13
 *   All phi angles  = 15  (4-bit fields from 0xFF stream)
 *   All psi angles  = 3   (2-bit fields from 0xFF stream)
 *   snr[0]        = 20,  snr[1] = 30
 */
void test_cbf_he_valid_frame_parsed(void)
{
    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-55, 6, 9999,
                                          HE_CBF_FRAME_LEN);

    bool ok = scan_for_cbf(he_cbf_frame, HE_CBF_FRAME_LEN, &rx, &out);
    TEST_ASSERT_TRUE(ok);

    TEST_ASSERT_EQUAL_INT(WIFI_PHY_HE, out.phy_type);
    TEST_ASSERT_EQUAL_INT(WIFI_BW_20MHZ, out.bandwidth);
    TEST_ASSERT_EQUAL_UINT8(2,  out.num_streams);
    TEST_ASSERT_EQUAL_UINT8(2,  out.num_rows);
    TEST_ASSERT_EQUAL_UINT8(5,  out.dialog_token);
    TEST_ASSERT_EQUAL_UINT8(1,  out.phi_count);
    TEST_ASSERT_EQUAL_UINT8(1,  out.psi_count);
    TEST_ASSERT_EQUAL_UINT16(13, out.num_subcarriers);

    TEST_ASSERT_NOT_NULL(out.phi);
    TEST_ASSERT_NOT_NULL(out.psi);

    for (int i = 0; i < 13; i++) {
        TEST_ASSERT_EQUAL_INT16(15, out.phi[i]);
        TEST_ASSERT_EQUAL_INT16(3,  out.psi[i]);
    }

    TEST_ASSERT_EQUAL_INT8(20, out.snr[0]);
    TEST_ASSERT_EQUAL_INT8(30, out.snr[1]);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  HE CBF SU frame with Codebook=1 (bit set in mimo[1])
 *         → SU CB1: bphi=6, bpsi=4
 *         Nc=2, Nr=2, BW=20 MHz, Ng=4, token=3
 *         bits/SC = 6+4 = 10 → 13*10 = 130 bits → 17 bytes
 *         angle data: 17 bytes of 0xFF
 *           phi = bits[5:0] = 0x3F = 63
 *           psi = bits[9:6] = 0xF  = 15
 * Expect: phi[0] = 63, psi[0] = 15, is_mu = false
 */
void test_cbf_he_codebook7_angles(void)
{
    /* cat(1)+act(1)+mimo(4)+SNR(2)+angles(17) = 25 body bytes */
    const uint16_t frame_len = MGMT_HDR_BODY_OFFSET + 1 + 1 + 4 + 2 + 17;
    uint8_t frame[frame_len];
    memset(frame, 0, sizeof(frame));

    build_mgmt_header(frame, FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = frame + MGMT_HDR_BODY_OFFSET;
    body[0] = ACTION_CAT_HE;
    body[1] = HE_ACTION_CBF_CQI;
    /* mimo[0]: Nc-1=1, Nr-1=1 */
    body[2] = 0x09;
    /* mimo[1]: Ng=4→index=2=0x02, Codebook=1 bit=0x04 → 0x06; FBType=SU */
    body[3] = 0x06;
    /* mimo[2]: RU end tone = 0 */
    body[4] = 0x00;
    /* mimo[3]: token=3 in bits[5:0] */
    body[5] = 0x03;
    /* HE report: SNR (Nc=2 bytes) first, then angles */
    /* body[6..7] = SNR[0..1] = 0 (already zeroed by memset) */
    /* 17 bytes of angle data at body[8] */
    memset(body + 8, 0xFF, 17);

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 0, frame_len);

    TEST_ASSERT_TRUE(scan_for_cbf(frame, frame_len, &rx, &out));
    TEST_ASSERT_EQUAL_INT16(63, out.phi[0]);
    TEST_ASSERT_EQUAL_INT16(15, out.psi[0]);
    TEST_ASSERT_FALSE(out.is_mu);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  HE CBF frame with FC[1] bit 7 set (+HTC):
 *         4-byte HT Control field at offset 24, body at offset 28.
 *         Same parameters as test_cbf_he_valid_frame_parsed.
 *
 * Expect: returns true, token=5, phi[0]=15, psi[0]=3
 *         (HTC field skipped; category byte read at correct offset)
 */
void test_cbf_he_htc_frame_parsed(void)
{
    /* MAC header(24) + HTC(4) + cat(1)+act(1)+mimo(4)+SNR(2)+angles(10) */
    const uint16_t frame_len = 24 + 4 + 1 + 1 + 4 + 2 + 10;
    uint8_t frame[frame_len];
    memset(frame, 0, sizeof(frame));

    build_mgmt_header(frame, FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);
    /* Set FC[1] Order/+HTC bit */
    frame[1] = FC_ORDER_MASK;
    /* frame[24..27] = HT Control (all zeros) */

    uint8_t *body = frame + 28;   /* body after 24-byte hdr + 4-byte HTC */
    body[0] = ACTION_CAT_HE;
    body[1] = HE_ACTION_CBF_CQI;
    body[2] = 0x09;               /* mimo[0]: Nc=2, Nr=2, BW=20 */
    body[3] = 0x02;               /* mimo[1]: Ng=4, CB0, SU */
    body[4] = 0x00;               /* mimo[2] */
    body[5] = 0x05;               /* mimo[3]: token=5 */
    /* HE report: SNR (Nc=2 bytes) first, then angles */
    body[6] = 20;                 /* SNR[0] */
    body[7] = 30;                 /* SNR[1] */
    memset(body + 8, 0xFF, 10);   /* angle data */

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-55, 100, 0, frame_len);

    bool ok = scan_for_cbf(frame, frame_len, &rx, &out);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_EQUAL_INT(WIFI_PHY_HE, out.phy_type);
    TEST_ASSERT_EQUAL_UINT8(5,  out.dialog_token);
    TEST_ASSERT_EQUAL_INT16(15, out.phi[0]);
    TEST_ASSERT_EQUAL_INT16(3,  out.psi[0]);
    TEST_ASSERT_FALSE(out.is_mu);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  HE SU CBF frame with Nc=2, Nr=4, BW=20 MHz, Ng=4, CB0, token=7.
 *         phi_count = 2*4 - 2*3/2 = 5
 *         psi_count = 5  (equals phi_count; HE interleaves one psi per pair)
 *         bits/SC   = 5 * (4+2) = 30 → 13*30=390 bits → 49 bytes
 *         49 bytes of 0xFF angle data:
 *           per pair: phi = bits[3:0] = 15, psi = bits[5:4] = 3
 *
 * Expect: phi_count=5, psi_count=5, phi[0]=15, psi[0]=3, is_mu=false
 */
void test_cbf_he_nc2_nr4_psi_count(void)
{
    /* cat(1)+act(1)+mimo(4)+SNR(2)+angles(49) = 57 body bytes */
    const uint16_t frame_len = MGMT_HDR_BODY_OFFSET + 57;
    uint8_t frame[frame_len];
    memset(frame, 0, sizeof(frame));

    build_mgmt_header(frame, FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = frame + MGMT_HDR_BODY_OFFSET;
    body[0] = ACTION_CAT_HE;
    body[1] = HE_ACTION_CBF_CQI;
    /* mimo[0]: Nc-1=1, Nr-1=3 (<<3=0x18), BW=0 → 0x19 */
    body[2] = 0x19;
    /* mimo[1]: Ng=4→idx=2=0x02, CB0, SU */
    body[3] = 0x02;
    /* mimo[2]: 0 */
    body[4] = 0x00;
    /* mimo[3]: token=7 */
    body[5] = 0x07;
    /* SNR[0..1] already 0 from memset */
    /* 49 bytes of angle data at body[8] */
    memset(body + 8, 0xFF, 49);

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 6, 0, frame_len);

    bool ok = scan_for_cbf(frame, frame_len, &rx, &out);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_EQUAL_UINT8(5,   out.phi_count);
    TEST_ASSERT_EQUAL_UINT8(5,   out.psi_count);
    TEST_ASSERT_EQUAL_UINT8(7,   out.dialog_token);
    TEST_ASSERT_EQUAL_UINT8(2,   out.num_streams);
    TEST_ASSERT_EQUAL_UINT8(4,   out.num_rows);
    TEST_ASSERT_EQUAL_UINT16(13, out.num_subcarriers);
    TEST_ASSERT_EQUAL_INT16(15,  out.phi[0]);
    TEST_ASSERT_EQUAL_INT16(3,   out.psi[0]);
    TEST_ASSERT_FALSE(out.is_mu);

    wifi_cbf_result_free(&out);
}

/*
 * Input:  HE MU-MIMO CBF frame: FBType=1 (MU), Codebook=0
 *         → MU CB0: bphi=7, bpsi=5
 *         Nc=2, Nr=2, BW=20 MHz, Ng=4, token=9
 *         bits/SC = 12 → 13*12 = 156 bits → 20 bytes
 *         angle data: 20 bytes of 0xFF
 *           phi = bits[6:0] = 0x7F = 127
 *           psi = bits[11:7] = 0x1F = 31
 *
 * Expect: returns true, is_mu=true, phi[0]=127, psi[0]=31, token=9
 */
void test_cbf_he_mu_frame_parsed(void)
{
    /* cat(1)+act(1)+mimo(4)+SNR(2)+angles(20) = 28 body bytes */
    const uint16_t frame_len = MGMT_HDR_BODY_OFFSET + 28;
    uint8_t frame[frame_len];
    memset(frame, 0, sizeof(frame));

    build_mgmt_header(frame, FC_TYPE_MGMT | FC_SUBTYPE_ACTION,
                      BCAST_MAC, VHT_SRC_MAC, BSSID_MAC);

    uint8_t *body = frame + MGMT_HDR_BODY_OFFSET;
    body[0] = ACTION_CAT_HE;
    body[1] = HE_ACTION_CBF_CQI;
    /* mimo[0]: Nc-1=1, Nr-1=1, BW=0 */
    body[2] = 0x09;
    /* mimo[1]: Ng=4→idx=2=0x02, Codebook=0, FBType=MU=0x08 → 0x0A */
    body[3] = 0x0A;
    /* mimo[2]: RU end tone = 0 */
    body[4] = 0x00;
    /* mimo[3]: token=9 in bits[5:0] */
    body[5] = 0x09;
    /* HE report: SNR (Nc=2 bytes) first, then angles */
    /* body[6..7] = SNR[0..1] = 0 (already zeroed) */
    /* 20 bytes angle data at body[8] */
    memset(body + 8, 0xFF, 20);

    wifi_cbf_result_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-60, 100, 0, frame_len);

    bool ok = scan_for_cbf(frame, frame_len, &rx, &out);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_EQUAL_INT(WIFI_PHY_HE, out.phy_type);
    TEST_ASSERT_TRUE(out.is_mu);
    TEST_ASSERT_EQUAL_UINT8(9,   out.dialog_token);
    TEST_ASSERT_EQUAL_UINT8(2,   out.num_streams);
    TEST_ASSERT_EQUAL_UINT16(13, out.num_subcarriers);
    TEST_ASSERT_EQUAL_INT16(127, out.phi[0]);
    TEST_ASSERT_EQUAL_INT16(31,  out.psi[0]);

    wifi_cbf_result_free(&out);
}

/* ================================================================
 * scan_for_ndpa tests
 * ================================================================ */

/*
 * Input:  NULL frame
 * Expect: returns false
 */
void test_ndpa_null_frame_returns_false(void)
{
    wifi_ndpa_result_t out;
    TEST_ASSERT_FALSE(scan_for_ndpa(NULL, NDPA_FRAME_LEN, &out));
}

/*
 * Input:  NULL output struct
 * Expect: returns false
 */
void test_ndpa_null_out_returns_false(void)
{
    TEST_ASSERT_FALSE(scan_for_ndpa(ndpa_frame, NDPA_FRAME_LEN, NULL));
}

/*
 * Input:  frame length < 17 (minimum NDPA size)
 * Expect: returns false
 */
void test_ndpa_frame_too_short_returns_false(void)
{
    wifi_ndpa_result_t out;
    TEST_ASSERT_FALSE(scan_for_ndpa(ndpa_frame, 16, &out));
}

/*
 * Input:  management Action frame (not control NDPA)
 * Expect: returns false
 */
void test_ndpa_wrong_frame_type_returns_false(void)
{
    uint8_t frame[NDPA_FRAME_LEN];
    memcpy(frame, ndpa_frame, sizeof(frame));
    frame[0] = FC_TYPE_MGMT | FC_SUBTYPE_ACTION;

    wifi_ndpa_result_t out;
    TEST_ASSERT_FALSE(scan_for_ndpa(frame, sizeof(frame), &out));
}

/*
 * Input:  valid NDPA frame
 *         dst_mac = BCAST_MAC
 *         src_mac = NDPA_SRC_MAC = {0x11,0x22,0x33,0x44,0x55,0x66}
 *         token   = 42
 * Expect: returns true, fields match
 */
void test_ndpa_valid_frame_parsed(void)
{
    wifi_ndpa_result_t out;
    bool ok = scan_for_ndpa(ndpa_frame, NDPA_FRAME_LEN, &out);
    TEST_ASSERT_TRUE(ok);

    TEST_ASSERT_EQUAL_MEMORY(NDPA_DST_MAC, out.dst_mac, WIFI_MAC_ADDR_LEN);
    TEST_ASSERT_EQUAL_MEMORY(NDPA_SRC_MAC, out.src_mac, WIFI_MAC_ADDR_LEN);
    TEST_ASSERT_EQUAL_UINT8(42, out.dialog_token);
}

/*
 * Input:  NDPA frame with token = 0
 * Expect: dialog_token = 0
 */
void test_ndpa_zero_token(void)
{
    uint8_t frame[NDPA_FRAME_LEN];
    memcpy(frame, ndpa_frame, sizeof(frame));
    frame[CTRL_NDPA_TOKEN_OFFSET] = 0;

    wifi_ndpa_result_t out;
    TEST_ASSERT_TRUE(scan_for_ndpa(frame, sizeof(frame), &out));
    TEST_ASSERT_EQUAL_UINT8(0, out.dialog_token);
}

/* ================================================================
 * scan_for_ssid tests
 * ================================================================ */

/*
 * Input:  NULL frame
 * Expect: returns false
 */
void test_ssid_null_frame_returns_false(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);
    TEST_ASSERT_FALSE(scan_for_ssid(NULL, BCN_FRAME_LEN, &rx, &out));
}

/*
 * Input:  NULL rx_ctrl
 * Expect: returns false
 */
void test_ssid_null_rx_ctrl_returns_false(void)
{
    wifi_ap_info_t out;
    TEST_ASSERT_FALSE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, NULL, &out));
}

/*
 * Input:  NULL output struct
 * Expect: returns false
 */
void test_ssid_null_out_returns_false(void)
{
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);
    TEST_ASSERT_FALSE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, NULL));
}

/*
 * Input:  Action frame instead of Beacon
 * Expect: returns false
 */
void test_ssid_non_beacon_rejected(void)
{
    uint8_t frame[BCN_FRAME_LEN];
    memcpy(frame, bcn_frame, sizeof(frame));
    frame[0] = FC_TYPE_MGMT | FC_SUBTYPE_ACTION;

    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, sizeof(frame));
    TEST_ASSERT_FALSE(scan_for_ssid(frame, sizeof(frame), &rx, &out));
}

/*
 * Input:  valid beacon frame with SSID="TestAP", rssi=-50, channel=11,
 *         timestamp=12345, src_mac=VHT_SRC_MAC
 * Expect: returns true, ssid and metadata match
 */
void test_ssid_valid_beacon_ssid_and_metadata(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 12345, BCN_FRAME_LEN);

    bool ok = scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, &out);
    TEST_ASSERT_TRUE(ok);

    TEST_ASSERT_EQUAL_STRING("TestAP", out.ssid);
    TEST_ASSERT_EQUAL_MEMORY(VHT_SRC_MAC, out.src_mac, WIFI_MAC_ADDR_LEN);
    TEST_ASSERT_EQUAL_INT8(-50,   out.rssi);
    TEST_ASSERT_EQUAL_UINT8(11,   out.channel);
    TEST_ASSERT_EQUAL_INT64(12345, out.timestamp_us);
}

/*
 * Input:  beacon with HT Capabilities IE (ht_cap_info = 0x012C)
 * Expect: cap_flags has WIFI_CAP_HT set, ht_cap_info = 0x012C
 */
void test_ssid_ht_capability_detected(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);

    TEST_ASSERT_TRUE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, &out));
    TEST_ASSERT_BITS(WIFI_CAP_HT, WIFI_CAP_HT, out.cap_flags);
    TEST_ASSERT_EQUAL_UINT16(0x012C, out.ht_cap_info);
}

/*
 * Input:  beacon with VHT Capabilities IE (vht_cap_info = 0x00000033)
 * Expect: cap_flags has WIFI_CAP_VHT set, vht_cap_info = 0x33
 */
void test_ssid_vht_capability_detected(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);

    TEST_ASSERT_TRUE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, &out));
    TEST_ASSERT_BITS(WIFI_CAP_VHT, WIFI_CAP_VHT, out.cap_flags);
    TEST_ASSERT_EQUAL_UINT32(0x00000033, out.vht_cap_info);
}

/*
 * Input:  beacon with HE Capabilities Extension IE (ext_id=0x23)
 * Expect: cap_flags has WIFI_CAP_HE set
 */
void test_ssid_he_capability_detected(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);

    TEST_ASSERT_TRUE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, &out));
    TEST_ASSERT_BITS(WIFI_CAP_HE, WIFI_CAP_HE, out.cap_flags);
}

/*
 * Input:  beacon with supported rates IE (4 rates: 0x82,0x84,0x8B,0x96)
 * Expect: rates.count = 4, rates.rates[0] = 0x82
 */
void test_ssid_supported_rates_parsed(void)
{
    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, BCN_FRAME_LEN);

    TEST_ASSERT_TRUE(scan_for_ssid(bcn_frame, BCN_FRAME_LEN, &rx, &out));
    TEST_ASSERT_EQUAL_UINT8(4,    out.rates.count);
    TEST_ASSERT_EQUAL_UINT8(0x82, out.rates.rates[0]);
    TEST_ASSERT_EQUAL_UINT8(0x96, out.rates.rates[3]);
}

/*
 * Input:  beacon with empty SSID IE (hidden network, SSID len=0)
 * Expect: returns true, ssid is empty string (no crash)
 */
void test_ssid_hidden_ssid_no_crash(void)
{
    uint8_t frame[BCN_FRAME_LEN];
    memcpy(frame, bcn_frame, sizeof(frame));

    /* Zero out the SSID length byte (body[12+1] = body[13]) */
    frame[MGMT_HDR_BODY_OFFSET + BCN_IES_OFFSET + 1] = 0;

    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, sizeof(frame));

    TEST_ASSERT_TRUE(scan_for_ssid(frame, sizeof(frame), &rx, &out));
}

/*
 * Input:  beacon where an IE length byte overflows the frame boundary
 * Expect: returns true (walks IEs safely, just skips the malformed IE)
 */
void test_ssid_malformed_ie_length_safe(void)
{
    uint8_t frame[BCN_FRAME_LEN];
    memcpy(frame, bcn_frame, sizeof(frame));

    /* Corrupt the SSID IE length to point past end of frame */
    frame[MGMT_HDR_BODY_OFFSET + BCN_IES_OFFSET + 1] = 0xFF;

    wifi_ap_info_t out;
    wifi_pkt_rx_ctrl_t rx = make_rx_ctrl(-50, 11, 0, sizeof(frame));

    /* Should not crash; HT/VHT/HE IEs won't be found but no UB */
    scan_for_ssid(frame, sizeof(frame), &rx, &out);
}

/* ================================================================
 * Monitor mode tests (require mocking esp_wifi functions)
 * ================================================================ */

/*
 * Input:  start_monitor on channel 6, all callbacks NULL,
 *         all esp_wifi calls return ESP_OK
 * Expect: returns ESP_OK, calls the four esp_wifi functions in order
 */
void test_start_monitor_success(void)
{
    esp_wifi_set_promiscuous_filter_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_rx_cb_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_ExpectAndReturn(true, ESP_OK);
    esp_wifi_set_channel_ExpectAndReturn(6, WIFI_SECOND_CHAN_NONE, ESP_OK);

    esp_err_t ret = start_monitor(6, NULL, NULL, NULL);
    TEST_ASSERT_EQUAL(ESP_OK, ret);

    /* Clean up: stop monitor */
    esp_wifi_set_promiscuous_ExpectAndReturn(false, ESP_OK);
    stop_monitor();
}

/*
 * Input:  start_monitor called twice without stop_monitor
 * Expect: second call returns ESP_ERR_INVALID_STATE, no esp_wifi calls
 */
void test_start_monitor_already_active(void)
{
    esp_wifi_set_promiscuous_filter_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_rx_cb_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_ExpectAndReturn(true, ESP_OK);
    esp_wifi_set_channel_ExpectAndReturn(6, WIFI_SECOND_CHAN_NONE, ESP_OK);
    start_monitor(6, NULL, NULL, NULL);

    /* Second call — no new wifi calls expected */
    esp_err_t ret = start_monitor(6, NULL, NULL, NULL);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, ret);

    esp_wifi_set_promiscuous_ExpectAndReturn(false, ESP_OK);
    stop_monitor();
}

/*
 * Input:  start_monitor where esp_wifi_set_promiscuous_filter fails
 * Expect: returns the error code, no further esp_wifi calls
 */
void test_start_monitor_filter_fails(void)
{
    esp_wifi_set_promiscuous_filter_ExpectAndReturn(NULL,
        ESP_ERR_INVALID_STATE);

    esp_err_t ret = start_monitor(1, NULL, NULL, NULL);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, ret);
}

/*
 * Input:  switch_channel to channel 11
 * Expect: calls esp_wifi_set_channel(11, WIFI_SECOND_CHAN_NONE)
 *         and returns ESP_OK
 */
void test_switch_channel_calls_wifi_api(void)
{
    esp_wifi_set_channel_ExpectAndReturn(11, WIFI_SECOND_CHAN_NONE, ESP_OK);
    esp_err_t ret = switch_channel(11);
    TEST_ASSERT_EQUAL(ESP_OK, ret);
}

/*
 * Input:  stop_monitor when not active
 * Expect: returns ESP_OK immediately, no esp_wifi calls
 */
void test_stop_monitor_when_not_active(void)
{
    esp_err_t ret = stop_monitor();
    TEST_ASSERT_EQUAL(ESP_OK, ret);
}

/*
 * Input:  stop_monitor after start_monitor
 * Expect: calls esp_wifi_set_promiscuous(false), returns ESP_OK
 */
void test_stop_monitor_after_start(void)
{
    esp_wifi_set_promiscuous_filter_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_rx_cb_ExpectAndReturn(NULL, ESP_OK);
    esp_wifi_set_promiscuous_ExpectAndReturn(true, ESP_OK);
    esp_wifi_set_channel_ExpectAndReturn(6, WIFI_SECOND_CHAN_NONE, ESP_OK);
    start_monitor(6, NULL, NULL, NULL);

    esp_wifi_set_promiscuous_ExpectAndReturn(false, ESP_OK);
    esp_err_t ret = stop_monitor();
    TEST_ASSERT_EQUAL(ESP_OK, ret);
}

/* ================================================================
 * Test runner
 * ================================================================ */

int main(void)
{
    UNITY_BEGIN();

    /* wifi_cbf_result_free */
    RUN_TEST(test_cbf_result_free_null_is_safe);
    RUN_TEST(test_cbf_result_free_clears_pointers);
    RUN_TEST(test_cbf_result_free_twice_is_safe);

    /* scan_for_cbf — bad args */
    RUN_TEST(test_cbf_null_frame_returns_false);
    RUN_TEST(test_cbf_null_rx_ctrl_returns_false);
    RUN_TEST(test_cbf_null_out_returns_false);
    RUN_TEST(test_cbf_frame_too_short_returns_false);
    RUN_TEST(test_cbf_data_frame_rejected);
    RUN_TEST(test_cbf_unknown_category_returns_false);

    /* scan_for_cbf — VHT */
    RUN_TEST(test_cbf_vht_valid_frame_parsed);
    RUN_TEST(test_cbf_vht_action_noack_accepted);
    RUN_TEST(test_cbf_vht_report_too_short_returns_false);
    RUN_TEST(test_cbf_vht_token_max);

    /* scan_for_cbf — HE */
    RUN_TEST(test_cbf_he_valid_frame_parsed);
    RUN_TEST(test_cbf_he_codebook7_angles);
    RUN_TEST(test_cbf_he_htc_frame_parsed);
    RUN_TEST(test_cbf_he_nc2_nr4_psi_count);
    RUN_TEST(test_cbf_he_mu_frame_parsed);

    /* scan_for_ndpa */
    RUN_TEST(test_ndpa_null_frame_returns_false);
    RUN_TEST(test_ndpa_null_out_returns_false);
    RUN_TEST(test_ndpa_frame_too_short_returns_false);
    RUN_TEST(test_ndpa_wrong_frame_type_returns_false);
    RUN_TEST(test_ndpa_valid_frame_parsed);
    RUN_TEST(test_ndpa_zero_token);

    /* scan_for_ssid */
    RUN_TEST(test_ssid_null_frame_returns_false);
    RUN_TEST(test_ssid_null_rx_ctrl_returns_false);
    RUN_TEST(test_ssid_null_out_returns_false);
    RUN_TEST(test_ssid_non_beacon_rejected);
    RUN_TEST(test_ssid_valid_beacon_ssid_and_metadata);
    RUN_TEST(test_ssid_ht_capability_detected);
    RUN_TEST(test_ssid_vht_capability_detected);
    RUN_TEST(test_ssid_he_capability_detected);
    RUN_TEST(test_ssid_supported_rates_parsed);
    RUN_TEST(test_ssid_hidden_ssid_no_crash);
    RUN_TEST(test_ssid_malformed_ie_length_safe);

    /* Monitor mode */
    RUN_TEST(test_start_monitor_success);
    RUN_TEST(test_start_monitor_already_active);
    RUN_TEST(test_start_monitor_filter_fails);
    RUN_TEST(test_switch_channel_calls_wifi_api);
    RUN_TEST(test_stop_monitor_when_not_active);
    RUN_TEST(test_stop_monitor_after_start);

    return UNITY_END();
}
