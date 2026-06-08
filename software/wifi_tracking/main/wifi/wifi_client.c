#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "wifi_client.h"

static const char *TAG = "wifi_client";

static struct {
    scan_done_cb_t  on_scan_done;
    assoc_cb_t      on_assoc;
    disassoc_cb_t   on_disassoc;
    ip_ready_cb_t   on_ip;
    bool            initialized;
    bool            connected;
} s_client;

/* Scan result buffer shared between the event handler and the callback */
static wifi_client_ap_t s_scan_buf[WIFI_CLIENT_MAX_SCAN_APS];

/* ---------- Event handler ---------- */

/*
 * on_wifi_event - Handle WIFI_EVENT events from the default event loop.
 *
 * arg  : (in) void* unused
 * base : (in) esp_event_base_t event base (WIFI_EVENT)
 * id   : (in) int32_t event ID
 * data : (in) void* event-specific data
 */
static void on_wifi_event(void *arg, esp_event_base_t base,
                           int32_t id, void *data)
{
    switch (id) {

    case WIFI_EVENT_STA_CONNECTED: {
        wifi_event_sta_connected_t *ev =
            (wifi_event_sta_connected_t *)data;
        s_client.connected = true;
        if (s_client.on_assoc)
            s_client.on_assoc(ev->bssid, ev->channel);
        break;
    }

    case WIFI_EVENT_STA_DISCONNECTED: {
        wifi_event_sta_disconnected_t *ev =
            (wifi_event_sta_disconnected_t *)data;
        s_client.connected = false;
        if (s_client.on_disassoc)
            s_client.on_disassoc(ev->reason);
        break;
    }

    case WIFI_EVENT_SCAN_DONE: {
        uint16_t count = WIFI_CLIENT_MAX_SCAN_APS;
        wifi_ap_record_t records[WIFI_CLIENT_MAX_SCAN_APS];

        esp_err_t ret = esp_wifi_scan_get_ap_records(&count, records);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "scan get records failed: %d", ret);
            break;
        }

        for (uint16_t i = 0; i < count; i++) {
            memcpy(s_scan_buf[i].ssid, records[i].ssid,
                   sizeof(s_scan_buf[i].ssid));
            memcpy(s_scan_buf[i].bssid, records[i].bssid,
                   WIFI_MAC_ADDR_LEN);
            s_scan_buf[i].rssi     = records[i].rssi;
            s_scan_buf[i].channel  = records[i].primary;
            s_scan_buf[i].authmode = (uint8_t)records[i].authmode;
        }

        if (s_client.on_scan_done)
            s_client.on_scan_done(s_scan_buf, count);
        break;
    }

    default:
        break;
    }
}

/*
 * on_ip_event - Handle IP_EVENT_STA_GOT_IP from the default event loop.
 *
 * arg  : (in) void* unused
 * base : (in) esp_event_base_t event base (IP_EVENT)
 * id   : (in) int32_t event ID
 * data : (in) void* ip_event_got_ip_t*
 */
static void on_ip_event(void *arg, esp_event_base_t base,
                         int32_t id, void *data)
{
    if (id != IP_EVENT_STA_GOT_IP)
        return;

    ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
    if (s_client.on_ip)
        s_client.on_ip(ev->ip_info.ip.addr, ev->ip_info.gw.addr);
}

/* ---------- Public API ---------- */

esp_err_t wifi_client_init(scan_done_cb_t on_scan_done,
                            assoc_cb_t     on_assoc,
                            disassoc_cb_t  on_disassoc,
                            ip_ready_cb_t  on_ip)
{
    if (s_client.initialized)
        return ESP_ERR_INVALID_STATE;

    s_client.on_scan_done = on_scan_done;
    s_client.on_assoc     = on_assoc;
    s_client.on_disassoc  = on_disassoc;
    s_client.on_ip        = on_ip;

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        ret = nvs_flash_init();
    }
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init failed: %d", ret);
        return ret;
    }

    ret = esp_netif_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "netif init failed: %d", ret);
        return ret;
    }

    ret = esp_event_loop_create_default();
    /* ESP_ERR_INVALID_STATE means the loop already exists — accept it */
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "event loop create failed: %d", ret);
        return ret;
    }

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "wifi init failed: %d", ret);
        return ret;
    }

    ret = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                      on_wifi_event, NULL);
    if (ret != ESP_OK)
        goto fail;

    ret = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                      on_ip_event, NULL);
    if (ret != ESP_OK)
        goto fail;

    ret = esp_wifi_set_mode(WIFI_MODE_STA);
    if (ret != ESP_OK)
        goto fail;

    ret = esp_wifi_start();
    if (ret != ESP_OK)
        goto fail;

    s_client.initialized = true;
    ESP_LOGI(TAG, "initialized");
    return ESP_OK;

fail:
    esp_wifi_deinit();
    return ret;
}

esp_err_t wifi_client_scan(void)
{
    if (!s_client.initialized)
        return ESP_ERR_INVALID_STATE;

    wifi_scan_config_t cfg = {
        .ssid        = NULL,
        .bssid       = NULL,
        .channel     = 0,
        .show_hidden = false,
        .scan_type   = WIFI_SCAN_TYPE_PASSIVE,
    };

    esp_err_t ret = esp_wifi_scan_start(&cfg, false);
    if (ret != ESP_OK)
        ESP_LOGE(TAG, "scan start failed: %d", ret);
    return ret;
}

esp_err_t wifi_client_connect(const char *ssid, const char *password)
{
    if (!s_client.initialized || !ssid)
        return ESP_ERR_INVALID_ARG;

    wifi_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));

    strncpy((char *)cfg.sta.ssid, ssid, sizeof(cfg.sta.ssid) - 1);

    bool has_password = (password != NULL && password[0] != '\0');
    if (has_password)
        strncpy((char *)cfg.sta.password, password,
                sizeof(cfg.sta.password) - 1);

    cfg.sta.threshold.authmode = has_password ? WIFI_AUTH_WPA2_PSK
                                              : WIFI_AUTH_OPEN;

    esp_err_t ret = esp_wifi_set_config(WIFI_IF_STA, &cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "set config failed: %d", ret);
        return ret;
    }

    ret = esp_wifi_connect();
    if (ret != ESP_OK)
        ESP_LOGE(TAG, "connect failed: %d", ret);
    return ret;
}

esp_err_t wifi_client_disconnect(void)
{
    if (!s_client.initialized)
        return ESP_ERR_INVALID_STATE;

    esp_err_t ret = esp_wifi_disconnect();
    if (ret != ESP_OK)
        ESP_LOGE(TAG, "disconnect failed: %d", ret);
    return ret;
}

esp_err_t wifi_client_deinit(void)
{
    if (!s_client.initialized)
        return ESP_OK;

    if (s_client.connected)
        esp_wifi_disconnect();

    esp_wifi_stop();
    esp_wifi_deinit();

    memset(&s_client, 0, sizeof(s_client));
    ESP_LOGI(TAG, "deinitialized");
    return ESP_OK;
}
