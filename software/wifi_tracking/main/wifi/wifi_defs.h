#ifndef WIFI_DEFS_H
#define WIFI_DEFS_H

#include <stdbool.h>
#include <stdint.h>

/* MAC address length in bytes */
#define WIFI_MAC_ADDR_LEN       6

/* Maximum spatial streams (802.11ax / HE) */
#define WIFI_MAX_STREAMS        8

/* ---------- Frame Control byte 0 ---------- */
#define FC_TYPE_MASK            0x0C
#define FC_SUBTYPE_MASK         0xF0

#define FC_TYPE_MGMT            0x00
#define FC_TYPE_CTRL            0x04
#define FC_TYPE_DATA            0x08

/* Management frame subtypes (in FC byte 0) */
#define FC_SUBTYPE_BEACON       0x80
#define FC_SUBTYPE_ACTION       0xD0
#define FC_SUBTYPE_ACTION_NOACK 0xE0

/* Control frame subtypes (in FC byte 0) */
#define FC_SUBTYPE_NDPA         0x50

/* Frame Control byte 1: set when 4-byte HT Control field is present */
#define FC_ORDER_MASK           0x80

/* ---------- 802.11 Management Frame Header Offsets ---------- */
#define MGMT_HDR_FC_OFFSET      0
#define MGMT_HDR_DUR_OFFSET     2
#define MGMT_HDR_DA_OFFSET      4
#define MGMT_HDR_SA_OFFSET      10
#define MGMT_HDR_BSSID_OFFSET   16
#define MGMT_HDR_SEQ_OFFSET     22
#define MGMT_HDR_BODY_OFFSET    24
#define MGMT_HDR_ACT_CAT_OFFSET 2


/* Control Frame (NDPA) Offsets */
#define CTRL_HDR_FC_OFFSET      0
#define CTRL_HDR_DUR_OFFSET     2
#define CTRL_HDR_RA_OFFSET      4
#define CTRL_HDR_TA_OFFSET      10
#define CTRL_NDPA_TOKEN_OFFSET  16

/* Beacon frame body offsets (relative to MGMT_HDR_BODY_OFFSET) */
#define BCN_TIMESTAMP_OFFSET    0
#define BCN_INTERVAL_OFFSET     8
#define BCN_CAPAB_OFFSET        10
#define BCN_IES_OFFSET          12

/* ---------- Action Frame Categories ---------- */
#define ACTION_CAT_VHT          21
#define ACTION_CAT_HE           30

/* VHT Action Codes */
#define VHT_ACTION_CBF          0   /* Compressed Beamforming */

/* HE Action Codes */
#define HE_ACTION_CBF_CQI       0   /* HE Compressed BF and CQI. Actually isn't 2, its 0 */

/* ---------- Information Element IDs ---------- */
#define IE_ID_SSID              0
#define IE_ID_SUPP_RATES        1
#define IE_ID_HT_CAP            45
#define IE_ID_EXT_SUPP_RATES    50
#define IE_ID_VHT_CAP           191
#define IE_ID_EXTENSION         255

/* IE Extension IDs (follow IE_ID_EXTENSION) */
#define IE_EXT_ID_HE_CAP        35

/* ---------- VHT MIMO Control field (3 bytes) ---------- */
/* Byte 0 */
#define VHT_MIMO_NC_MASK        0x07
#define VHT_MIMO_NR_MASK        0x38
#define VHT_MIMO_NR_SHIFT       3
#define VHT_MIMO_BW_MASK        0xC0
#define VHT_MIMO_BW_SHIFT       6
/* Byte 1 */
#define VHT_MIMO_GROUPING_MASK  0x03
#define VHT_MIMO_FBTYPE_MASK    0x10
/*
 * Sounding dialog token spans bits [23:19] of the 3-byte field:
 *   high 2 bits in byte[2] bits [1:0]
 *   low  3 bits in byte[1] bits [7:5]
 */
#define VHT_MIMO_TOKEN_LOW_MASK  0xE0
#define VHT_MIMO_TOKEN_LOW_SHIFT 5
#define VHT_MIMO_TOKEN_HI_MASK   0x03
#define VHT_MIMO_TOKEN_HI_SHIFT  3

/* VHT angle bit widths (802.11-2020 Table 9-95) */
#define VHT_BPHI                7   /* bits per phi angle */
#define VHT_BPSI                5   /* bits per psi angle */

/* ---------- HE MIMO Control field (4 bytes = 32 bits) ----------
 * Byte 0: Nc[2:0] | Nr[2:0] | BW[1:0]
 * Byte 1: Ng[1:0] | Codebook | FBType | RemFBSeg[2:0] | FirstFBSeg
 * Byte 2: RUEndTone[6:0] | Disambiguation
 * Byte 3: SoundingToken[5:0] | Reserved[1:0]
 */
#define HE_MIMO_CTRL_LEN        4
/* Byte 0 */
#define HE_MIMO_NC_MASK         0x07
#define HE_MIMO_NR_MASK         0x38
#define HE_MIMO_NR_SHIFT        3
#define HE_MIMO_BW_MASK         0xC0
#define HE_MIMO_BW_SHIFT        6
/* Byte 1 */
#define HE_MIMO_GROUPING_MASK   0x03
#define HE_MIMO_CODEBOOK_MASK   0x04
#define HE_MIMO_FB_TYPE_MASK    0x08   /* 0 = SU, 1 = MU */
/* Byte 3: dialog token at bits [5:0] */
#define HE_MIMO_TOKEN_BYTE_IDX  3
#define HE_MIMO_TOKEN_MASK      0x3F

/* HE SU angle bit widths (802.11ax Table 9-98e, Feedback Type = SU) */
#define HE_SU_BPHI_CB0          4   /* Codebook=0: phi bits */
#define HE_SU_BPSI_CB0          2   /* Codebook=0: psi bits */
#define HE_SU_BPHI_CB1          6   /* Codebook=1: phi bits */
#define HE_SU_BPSI_CB1          4   /* Codebook=1: psi bits */

/* HE MU angle bit widths (802.11ax Table 9-98e, Feedback Type = MU) */
#define HE_MU_BPHI_CB0          7   /* Codebook=0: phi bits */
#define HE_MU_BPSI_CB0          5   /* Codebook=0: psi bits */
#define HE_MU_BPHI_CB1          9   /* Codebook=1: phi bits */
#define HE_MU_BPSI_CB1          7   /* Codebook=1: psi bits */

/* ---------- Subcarrier counts per bandwidth ---------- */
/* VHT subcarriers for Ng=1, Ng=2, Ng=4 */
#define VHT_NSC_20_NG1          52
#define VHT_NSC_40_NG1          108
#define VHT_NSC_80_NG1          234 
#define VHT_NSC_160_NG1         468

#define VHT_NSC_20_NG2          26
#define VHT_NSC_40_NG2          54
#define VHT_NSC_80_NG2          117
#define VHT_NSC_160_NG2         234

#define VHT_NSC_20_NG4          13
#define VHT_NSC_40_NG4          27
#define VHT_NSC_80_NG4          59
#define VHT_NSC_160_NG4         117

/* HE subcarrier counts (approximate; same grouping scheme) */
#define HE_NSC_20_NG1           52
#define HE_NSC_40_NG1           108
#define HE_NSC_80_NG1           234
#define HE_NSC_160_NG1          468

#define HE_NSC_20_NG2           26
#define HE_NSC_40_NG2           54
#define HE_NSC_80_NG2           117
#define HE_NSC_160_NG2          234

#define HE_NSC_20_NG4           13
#define HE_NSC_40_NG4           27
#define HE_NSC_80_NG4           59
#define HE_NSC_160_NG4          117

/*
 * wifi_csi_result_t - CSI measurement from the ESP32 CSI subsystem.
 *
 * data holds num_samples int8_t values in interleaved I/Q order
 * (I0, Q0, I1, Q1, ...). Dynamically allocated; caller must call
 * wifi_csi_result_free() after use.
 * The first hardware word (4 bytes) is omitted when first_word_invalid
 * is set by the driver, so num_samples may be less than the raw length.
 */
typedef struct {
    uint8_t  src_mac[WIFI_MAC_ADDR_LEN];
    uint8_t  dst_mac[WIFI_MAC_ADDR_LEN];
    int64_t  timestamp_us;
    int8_t   rssi;
    uint8_t  channel;
    uint16_t num_samples;
    int8_t  *data;
} wifi_csi_result_t;

/* ---------- Enumerated Types ---------- */

/* PHY generation for beamforming feedback */
typedef enum {
    WIFI_PHY_VHT = 0,
    WIFI_PHY_HE,
} wifi_phy_type_t;

/* Channel bandwidth */
typedef enum {
    WIFI_BW_20MHZ = 0,
    WIFI_BW_40MHZ,
    WIFI_BW_80MHZ,
    WIFI_BW_160MHZ,
} wifi_bw_t;

/* AP capability flags (bitmask) */
typedef enum {
    WIFI_CAP_HT  = (1 << 0),
    WIFI_CAP_VHT = (1 << 1),
    WIFI_CAP_HE  = (1 << 2),
} wifi_cap_flags_t;

/* ---------- Data Structures ---------- */

/*
 * wifi_cbf_result_t - Compressed beamforming feedback result.
 *
 * phi and psi are dynamically allocated. Caller must call
 * wifi_cbf_result_free() after use.
 *
 * phi is indexed as phi[subcarrier * phi_count + angle_idx].
 * psi is indexed as psi[subcarrier * psi_count + angle_idx].
 *
 * Angle counts per subcarrier obey the Givens rotation decomposition
 * of an Nr x Nc steering matrix (both VHT and HE):
 *   phi_count = Nc * Nr - Nc * (Nc + 1) / 2
 *   psi_count = phi_count  (one complex psi per real phi rotation pair)
 *
 * SNR values are in units of 0.25 dB (signed 8-bit).
 */
typedef struct {
    uint8_t         src_mac[WIFI_MAC_ADDR_LEN];
    uint8_t         dialog_token;
    int64_t         timestamp_us;
    int8_t          snr[WIFI_MAX_STREAMS];
    uint8_t         num_streams;        /* Nc: columns sounded */
    uint8_t         num_rows;           /* Nr: rows at receiver */
    int8_t          rssi;
    uint8_t         channel;
    uint16_t        num_subcarriers;
    uint8_t         phi_count;          /* phi angles per subcarrier */
    uint8_t         psi_count;          /* psi angles per subcarrier */
    bool            is_mu;              /* true if MU-MIMO feedback */
    bool            codebook;           /* HE codebook selection bit */
    wifi_phy_type_t phy_type;
    wifi_bw_t       bandwidth;
    int16_t        *phi;
    int16_t        *psi;
} wifi_cbf_result_t;

/* wifi_ndpa_result_t - NDP Announcement frame result. */
typedef struct {
    uint8_t src_mac[WIFI_MAC_ADDR_LEN];
    uint8_t dst_mac[WIFI_MAC_ADDR_LEN];
    uint8_t dialog_token;
} wifi_ndpa_result_t;

/* wifi_rate_set_t - Supported rates list from beacon IEs. */
typedef struct {
    uint8_t rates[16]; /* 500 kbps units; MSB set = basic rate */
    uint8_t count;
} wifi_rate_set_t;

/*
 * wifi_ap_info_t - AP information extracted from a beacon frame.
 *
 * cap_flags uses wifi_cap_flags_t bitmask.
 * ht_cap_info and vht_cap_info are the raw capability info fields
 * as defined in 802.11-2020.
 */
typedef struct {
    uint8_t         src_mac[WIFI_MAC_ADDR_LEN];
    char            ssid[33];       /* null-terminated, max 32 chars */
    int64_t         timestamp_us;
    int8_t          rssi;
    uint8_t         channel;
    uint32_t        cap_flags;
    uint16_t        ht_cap_info;
    uint32_t        vht_cap_info;
    wifi_rate_set_t rates;
} wifi_ap_info_t;

#endif /* WIFI_DEFS_H */
