#ifndef WIFI_CLIENT_H
#define WIFI_CLIENT_H

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "wifi_defs.h"

/* Maximum APs returned from a single scan */
#define WIFI_CLIENT_MAX_SCAN_APS  20

/* AP record reported to scan callbacks */
typedef struct {
    char    ssid[33];
    uint8_t bssid[WIFI_MAC_ADDR_LEN];
    int8_t  rssi;
    uint8_t channel;
    uint8_t authmode;   /* wifi_auth_mode_t value */
} wifi_client_ap_t;

/*
 * scan_done_cb_t - Called when a channel scan completes.
 *
 * aps   : (in) wifi_client_ap_t* array of discovered APs; valid only
 *              during the callback
 * count : (in) uint16_t number of entries in aps
 */
typedef void (*scan_done_cb_t)(const wifi_client_ap_t *aps, uint16_t count);

/*
 * assoc_cb_t - Called when the station successfully associates.
 *
 * bssid   : (in) uint8_t* MAC address of the AP
 * channel : (in) uint8_t operating channel
 */
typedef void (*assoc_cb_t)(const uint8_t *bssid, uint8_t channel);

/*
 * disassoc_cb_t - Called when the station disassociates.
 *
 * reason : (in) uint8_t disconnection reason code
 */
typedef void (*disassoc_cb_t)(uint8_t reason);

/*
 * ip_ready_cb_t - Called when DHCP assigns an IP address.
 *
 * ip : (in) uint32_t IPv4 address (host byte order)
 * gw : (in) uint32_t default gateway (host byte order)
 */
typedef void (*ip_ready_cb_t)(uint32_t ip, uint32_t gw);

/*
 * wifi_client_init - Initialize the WiFi stack in station mode.
 *                   Must be called once before all other client functions.
 *
 * on_scan_done : (in) callback for scan completion (may be NULL)
 * on_assoc     : (in) callback on association (may be NULL)
 * on_disassoc  : (in) callback on disassociation (may be NULL)
 * on_ip        : (in) callback when IP is assigned (may be NULL)
 *
 * Returns ESP_OK on success, ESP_ERR_INVALID_STATE if already initialized.
 */
esp_err_t wifi_client_init(scan_done_cb_t on_scan_done,
                            assoc_cb_t     on_assoc,
                            disassoc_cb_t  on_disassoc,
                            ip_ready_cb_t  on_ip);

/*
 * wifi_client_scan - Start a passive scan for available APs.
 *                   Results are delivered via the scan_done_cb_t callback.
 *
 * Returns ESP_OK if the scan was started successfully.
 */
esp_err_t wifi_client_scan(void);

/*
 * wifi_client_connect - Initiate connection to an AP by SSID.
 *
 * ssid     : (in) const char* null-terminated SSID (required)
 * password : (in) const char* null-terminated passphrase; pass empty
 *                 string or NULL for open networks
 *
 * Returns ESP_OK if the connection attempt was started.
 */
esp_err_t wifi_client_connect(const char *ssid, const char *password);

/*
 * wifi_client_disconnect - Disassociate from the current AP.
 *
 * Returns ESP_OK on success.
 */
esp_err_t wifi_client_disconnect(void);

/*
 * wifi_client_deinit - Stop the WiFi stack and free all client resources.
 *
 * Returns ESP_OK on success.
 */
esp_err_t wifi_client_deinit(void);

#endif /* WIFI_CLIENT_H */
