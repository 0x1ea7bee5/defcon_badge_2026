#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "wifi/packet_scanner.h"
#include "wifi/wifi_defs.h"
#include "misc/telemetry.h"
#include "state_estimation/get_matrices_cbf.h"

static const char *TAG = "main";

#define MONITOR_CHANNEL   44
#define SCAN_INTERVAL_MS  5000
#define SCAN_INTERVAL_CBF 5000
#define SCAN_INTERVAL_CSI 500

/* ------------------------------------------------------------------ */
/* Packet callbacks                                                    */
/* ------------------------------------------------------------------ */

/*
 * print_cbf_matrices - Reconstruct and log the V beamforming matrix for
 *                      the first two subcarriers of a CBF result.
 *
 * Each element is printed as "re+j*im" via ESP_LOGI.
 *
 * cbf : (in) const wifi_cbf_result_t* — parsed CBF result
 */
static void print_cbf_matrices(const wifi_cbf_result_t *cbf)
{
    float v_r[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float v_i[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    const uint16_t n = cbf->num_subcarriers < 2u
                       ? cbf->num_subcarriers : 2u;

    for (uint16_t sc = 0; sc < n; sc++) {
        if (!reconstruct_v(cbf, sc, v_r, v_i)) {
            ESP_LOGI(TAG, "SC%u: reconstruct_v failed", sc);
            continue;
        }
        ESP_LOGI(TAG, "V[SC%u] (%ux%u):",
                 sc, cbf->num_rows, cbf->num_streams);
        for (uint8_t r = 0; r < cbf->num_rows; r++) {
            for (uint8_t c = 0; c < cbf->num_streams; c++) {
                const uint16_t idx =
                    (uint16_t)r * cbf->num_streams + c;
                ESP_LOGI(TAG, "  [%u,%u] %+.4f%+.4fj",
                         r, c, v_r[idx], v_i[idx]);
            }
        }
    }
}

/*
 * on_cbf - Log and serialize a compressed beamforming feedback frame.
 *
 * result : (in) const wifi_cbf_result_t*
 */
static void on_cbf(const wifi_cbf_result_t *result)
{
    ESP_LOGI(TAG, "CBF %s %s mac=%02X:%02X:%02X:%02X:%02X:%02X "
             "sc=%u streams=%u rows=%u rssi=%d",
             result->phy_type == WIFI_PHY_VHT ? "VHT" : "HE",
             result->is_mu ? "MU" : "SU",
             result->src_mac[0], result->src_mac[1], result->src_mac[2],
             result->src_mac[3], result->src_mac[4], result->src_mac[5],
             result->num_subcarriers, result->num_streams,
             result->num_rows, (int)result->rssi);
    print_cbf_matrices(result);
    telemetry_send_cbf(result);
}

/*
 * on_csi - Log and serialize a CSI measurement.
 *
 * result : (in) const wifi_csi_result_t*
 */
static void on_csi(const wifi_csi_result_t *result)
{
    ESP_LOGI(TAG,
             "CSI src=%02X:%02X:%02X:%02X:%02X:%02X "
             "dst=%02X:%02X:%02X:%02X:%02X:%02X "
             "samples=%u rssi=%d ch=%u",
             result->src_mac[0], result->src_mac[1], result->src_mac[2],
             result->src_mac[3], result->src_mac[4], result->src_mac[5],
             result->dst_mac[0], result->dst_mac[1], result->dst_mac[2],
             result->dst_mac[3], result->dst_mac[4], result->dst_mac[5],
             result->num_samples, (int)result->rssi,
             (unsigned)result->channel);
    telemetry_send_csi(result);
}

/* ------------------------------------------------------------------ */
/* Mode-switching task                                                 */
/* ------------------------------------------------------------------ */

/*
 * scan_switch_task - Alternates between CBF and CSI scan modes every
 *                   SCAN_INTERVAL_MS milliseconds.
 *
 * Starts in CBF mode (already running when the task is created).
 * Each iteration: wait, stop the current mode, restart with the other.
 *
 * arg : (in) unused
 */
static void scan_switch_task(void *arg)
{
    bool cbf_active = true;

    while (1) {
        if (cbf_active){
            vTaskDelay(pdMS_TO_TICKS(SCAN_INTERVAL_CBF));
        }
        else{
            vTaskDelay(pdMS_TO_TICKS(SCAN_INTERVAL_CSI));
        }
        //vTaskDelay(pdMS_TO_TICKS(SCAN_INTERVAL_MS));

        stop_monitor();
        cbf_active = !cbf_active;

        if (cbf_active) {
            ESP_LOGI(TAG, "Switching to CBF mode ch=%d", MONITOR_CHANNEL);
            start_monitor(MONITOR_CHANNEL, on_cbf, NULL, NULL, NULL);
        } else {
            ESP_LOGI(TAG, "Switching to CSI mode ch=%d", MONITOR_CHANNEL);
            start_monitor(MONITOR_CHANNEL, NULL, NULL, NULL, on_csi);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Entry point                                                         */
/* ------------------------------------------------------------------ */

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_NULL));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* Start in CBF mode; the switch task will alternate from here. */
    ESP_LOGI(TAG, "Starting CBF mode on channel %d", MONITOR_CHANNEL);
    ESP_ERROR_CHECK(
        start_monitor(MONITOR_CHANNEL, on_cbf, NULL, NULL, NULL));

    xTaskCreate(scan_switch_task, "scan_switch", 4096, NULL, 5, NULL);

    while (1)
        vTaskDelay(pdMS_TO_TICKS(1000));
}
