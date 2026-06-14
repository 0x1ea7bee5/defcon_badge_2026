#ifndef PACKET_SCANNER_H
#define PACKET_SCANNER_H

#include <stdbool.h>
#include "esp_err.h"
#include "esp_wifi_types.h"
#include "wifi_defs.h"

/*
 * cbf_cb_t - Callback invoked for each beamforming feedback frame.
 *            result is valid only for the duration of the callback.
 *            result: (in) parsed CBF data
 */
typedef void (*cbf_cb_t)(const wifi_cbf_result_t *result);

/*
 * csi_cb_t - Callback invoked for each CSI measurement.
 *            result is valid only for the duration of the callback.
 *            result: (in) parsed CSI data
 */
typedef void (*csi_cb_t)(const wifi_csi_result_t *result);

/*
 * ndpa_cb_t - Callback invoked for each NDPA frame.
 *             result: (in) parsed NDPA data
 */
typedef void (*ndpa_cb_t)(const wifi_ndpa_result_t *result);

/*
 * ssid_cb_t - Callback invoked for each beacon frame.
 *             result: (in) parsed AP info
 */
typedef void (*ssid_cb_t)(const wifi_ap_info_t *result);

/*
 * scan_for_cbf - Filter and parse VHT/HE compressed beamforming
 *                feedback from an Action or Action No Ack frame.
 *
 * frame   : (in)  raw 802.11 frame bytes
 * len     : (in)  frame length in bytes
 * rx_ctrl : (in)  receive metadata (RSSI, timestamp, channel)
 * out     : (out) populated on match; caller must call
 *                 wifi_cbf_result_free() when done
 *
 * Returns true if frame matched and out is populated, false otherwise.
 */
bool scan_for_cbf(const uint8_t *frame, uint16_t len,
                  const wifi_pkt_rx_ctrl_t *rx_ctrl,
                  wifi_cbf_result_t *out);

/*
 * scan_for_ndpa - Filter and parse NDP Announcement control frames.
 *
 * frame : (in)  raw 802.11 frame bytes
 * len   : (in)  frame length in bytes
 * out   : (out) populated on match
 *
 * Returns true if frame matched and out is populated, false otherwise.
 */
bool scan_for_ndpa(const uint8_t *frame, uint16_t len,
                   wifi_ndpa_result_t *out);

/*
 * scan_for_ssid - Filter Beacon frames and extract AP capabilities.
 *
 * frame   : (in)  raw 802.11 frame bytes
 * len     : (in)  frame length in bytes
 * rx_ctrl : (in)  receive metadata (RSSI, timestamp, channel)
 * out     : (out) populated on match
 *
 * Returns true if frame matched and out is populated, false otherwise.
 */
bool scan_for_ssid(const uint8_t *frame, uint16_t len,
                   const wifi_pkt_rx_ctrl_t *rx_ctrl,
                   wifi_ap_info_t *out);

/*
 * scan_for_csi - Extract CSI data from an ESP32 CSI callback event.
 *
 * info : (in)  wifi_csi_info_t* from esp_wifi_set_csi_rx_cb
 * out  : (out) populated on success; caller must call
 *              wifi_csi_result_free() when done
 *
 * Skips the first hardware word (4 bytes) when first_word_invalid is
 * set by the driver. Returns true on success.
 */
bool scan_for_csi(const wifi_csi_info_t *info, wifi_csi_result_t *out);

/*
 * wifi_csi_result_free - Free the dynamically allocated data buffer
 *                        inside a wifi_csi_result_t.
 *
 * result : (in/out) result struct; data is freed and set to NULL
 */
void wifi_csi_result_free(wifi_csi_result_t *result);

/*
 * start_monitor - Enable promiscuous (monitor) mode on the given channel.
 *                 Registers promiscuous and CSI callbacks that dispatch
 *                 packets to the user-supplied callbacks.
 *
 * channel  : (in) WiFi channel (1-14 for 2.4 GHz, 36+ for 5 GHz)
 * on_cbf   : (in) callback for beamforming feedback frames (may be NULL)
 * on_ndpa  : (in) callback for NDPA frames (may be NULL)
 * on_ssid  : (in) callback for beacon frames (may be NULL)
 * on_csi   : (in) callback for CSI measurements (may be NULL)
 *
 * Returns ESP_OK on success, ESP_ERR_INVALID_STATE if already active.
 */
esp_err_t start_monitor(uint8_t channel,
                         cbf_cb_t  on_cbf,
                         ndpa_cb_t on_ndpa,
                         ssid_cb_t on_ssid,
                         csi_cb_t  on_csi);

/*
 * switch_channel - Change the sniffing channel while monitor mode is active.
 *
 * channel : (in) new channel number
 *
 * Returns ESP_OK on success.
 */
esp_err_t switch_channel(uint8_t channel);

/*
 * stop_monitor - Disable promiscuous mode and clear scanner state.
 *
 * Returns ESP_OK on success.
 */
esp_err_t stop_monitor(void);

/*
 * wifi_cbf_result_free - Free dynamically allocated phi and psi arrays
 *                        inside a wifi_cbf_result_t.
 *
 * result : (in/out) result struct; phi and psi are freed and set to NULL
 */
void wifi_cbf_result_free(wifi_cbf_result_t *result);

#endif /* PACKET_SCANNER_H */
