#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "wifi/packet_scanner.h"
#include "wifi/wifi_defs.h"

static const char *TAG = "main";

/* Default channel to monitor; change as needed */
#define MONITOR_CHANNEL 40

/*
 * on_cbf - Print a beamforming feedback result to the console.
 *          Called from the promiscuous RX callback for every CBF frame.
 *
 * result : (in) const wifi_cbf_result_t* parsed frame data
 */
static void on_cbf(const wifi_cbf_result_t *result)
{
    const char *phy = (result->phy_type == WIFI_PHY_VHT) ? "VHT" : "HE";

    ESP_LOGI(TAG, "--- CBF frame (%s) ---", phy);
    ESP_LOGI(TAG, "  src:    %02X:%02X:%02X:%02X:%02X:%02X",
             result->src_mac[0], result->src_mac[1], result->src_mac[2],
             result->src_mac[3], result->src_mac[4], result->src_mac[5]);
    ESP_LOGI(TAG, "  token:  %u  streams: %u  rows: %u",
             result->dialog_token, result->num_streams, result->num_rows);
    ESP_LOGI(TAG, "  bw:     %u  subcarriers: %u  rssi: %d dBm",
             (unsigned)result->bandwidth, result->num_subcarriers,
             (int)result->rssi);
    ESP_LOGI(TAG, "  type:   %s",
             result->is_mu ? "MU" : "SU");
    if (result->phy_type == WIFI_PHY_HE)
        ESP_LOGI(TAG, "  codebook: %u", (unsigned)result->codebook);

    /* SNR per stream */
    for (uint8_t s = 0; s < result->num_streams; s++)
        ESP_LOGI(TAG, "  snr[%u]: %.2f dB", s,
                 result->snr[s] * 0.25f);

    /* Phi angles (first 4 subcarriers for brevity) */
    uint16_t print_sc = result->num_subcarriers < 8
                        ? result->num_subcarriers : 8;
    printf("phi count: %u\n", result->phi_count);
    printf("psi count: %u\n", result->psi_count);
    for (uint16_t sc = 0; sc < print_sc; sc++) {
        printf("  phi[sc%u]:", sc);
        for (uint8_t a = 0; a < result->phi_count; a++)
            printf(" %d", result->phi[sc * result->phi_count + a]);
        printf("\n");
    }

    /* Psi angles (first 4 subcarriers, only if present) */
    if (result->psi && result->psi_count > 0) {
        for (uint16_t sc = 0; sc < print_sc; sc++) {
            printf("  psi[sc%u]:", sc);
            for (uint8_t a = 0; a < result->psi_count; a++)
                printf(" %d", result->psi[sc * result->psi_count + a]);
            printf("\n");
        }
    }
}

void app_main(void)
{
    /* NVS required by the WiFi driver */
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

    ESP_LOGI(TAG, "Starting CBF monitor on channel %d", MONITOR_CHANNEL);
    ESP_ERROR_CHECK(start_monitor(MONITOR_CHANNEL, on_cbf, NULL, NULL));

    /* Loop forever; packet_scanner delivers frames via the callback */
    while (1)
        vTaskDelay(pdMS_TO_TICKS(1000));
}
