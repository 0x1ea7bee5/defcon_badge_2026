#ifndef STUB_ESP_IDF_H
#define STUB_ESP_IDF_H

/*
 * Minimal ESP-IDF type and constant stubs for host-based unit tests.
 * Replaces esp_err.h, esp_wifi.h, esp_wifi_types.h, and esp_log.h
 * so packet_scanner.c can be compiled and tested without the SDK.
 */

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>

/* ---------- esp_err.h stubs ---------- */
typedef int32_t esp_err_t;
#define ESP_OK               ((esp_err_t)0)
#define ESP_ERR_INVALID_STATE ((esp_err_t)0x103)
#define ESP_ERR_NO_MEM       ((esp_err_t)0x101)

/* ---------- esp_log.h stubs ---------- */
#define ESP_LOGI(tag, fmt, ...) ((void)0)
#define ESP_LOGE(tag, fmt, ...) ((void)0)
#define ESP_LOGW(tag, fmt, ...) ((void)0)

/* ---------- esp_wifi_types.h stubs ---------- */

/*
 * Minimal rx_ctrl fields used by packet_scanner.c.
 * The real ESP-IDF struct uses bitfields; here we use plain types
 * so test code can set them directly.
 */
typedef struct {
    int8_t   rssi;
    uint8_t  channel;
    uint32_t timestamp;
    uint16_t sig_len;
} wifi_pkt_rx_ctrl_t;

typedef struct {
    wifi_pkt_rx_ctrl_t rx_ctrl;
    uint8_t payload[0];
} wifi_promiscuous_pkt_t;

typedef enum {
    WIFI_PKT_MGMT  = 0,
    WIFI_PKT_CTRL  = 1,
    WIFI_PKT_DATA  = 2,
    WIFI_PKT_MISC  = 3,
} wifi_promiscuous_pkt_type_t;

typedef struct {
    uint32_t filter_mask;
} wifi_promiscuous_filter_t;

#define WIFI_PROMIS_FILTER_MASK_MGMT  (1u << 0)
#define WIFI_PROMIS_FILTER_MASK_CTRL  (1u << 1)

typedef void (*wifi_promiscuous_cb_t)(void *buf,
                                      wifi_promiscuous_pkt_type_t type);

typedef enum {
    WIFI_SECOND_CHAN_NONE = 0,
    WIFI_SECOND_CHAN_ABOVE,
    WIFI_SECOND_CHAN_BELOW,
} wifi_second_chan_t;

/* ---------- esp_wifi.h stubs (functions mocked separately) ---------- */
esp_err_t esp_wifi_set_promiscuous_filter(
    const wifi_promiscuous_filter_t *filter);
esp_err_t esp_wifi_set_promiscuous_rx_cb(wifi_promiscuous_cb_t cb);
esp_err_t esp_wifi_set_promiscuous(bool en);
esp_err_t esp_wifi_set_channel(uint8_t primary,
                                wifi_second_chan_t second);

#endif /* STUB_ESP_IDF_H */
