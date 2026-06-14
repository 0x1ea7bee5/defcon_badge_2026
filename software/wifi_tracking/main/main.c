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
#include "state_estimation/get_matrices_cbf.h"
#include "misc/telemetry.h"

static const char *TAG = "main";

/* Default channel to monitor; change as needed */
#define MONITOR_CHANNEL 44

/*
 * print_v_matrix - Print the reconstructed V matrix for every subcarrier.
 *
 * result : (in) const wifi_cbf_result_t* parsed frame data
 */
static void print_v_matrix(const wifi_cbf_result_t *result)
{
    const uint8_t  nr = result->num_rows;
    const uint8_t  nc = result->num_streams;
    float v_real[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float v_imag[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

    for (uint16_t sc = 0; sc < result->num_subcarriers; sc++) {
        if (!reconstruct_v(result, sc, v_real, v_imag)) {
            printf("  V[sc%u]: reconstruct failed\n", sc);
            continue;
        }
        printf("  V[sc%u]:\n", sc);
        for (uint8_t r = 0; r < nr; r++) {
            printf("    row%u:", r);
            for (uint8_t c = 0; c < nc; c++) {
                uint16_t idx = (uint16_t)r * nc + c;
                printf("  %6.3f%+6.3fj",
                       v_real[idx], v_imag[idx]);
            }
            printf("\n");
        }
    }
}

/*
 * print_eigen - Print eigenvalues and eigenvectors of V*V^H per subcarrier.
 *
 * result : (in) const wifi_cbf_result_t* parsed frame data
 */
static void print_eigen(const wifi_cbf_result_t *result)
{
    const uint8_t nr = result->num_rows;
    const uint8_t nc = result->num_streams;
    float v_real[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float v_imag[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float eigenvals[WIFI_MAX_STREAMS];
    float evec_real[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];
    float evec_imag[WIFI_MAX_STREAMS * WIFI_MAX_STREAMS];

    for (uint16_t sc = 0; sc < result->num_subcarriers; sc++) {
        if (!reconstruct_v(result, sc, v_real, v_imag)) {
            printf("  eigen[sc%u]: reconstruct failed\n", sc);
            continue;
        }
        if (!get_eigendecomp(v_real, v_imag, nr, nc,
                             eigenvals, evec_real, evec_imag)) {
            printf("  eigen[sc%u]: eigendecomp failed\n", sc);
            continue;
        }
        printf("  eigen[sc%u]:\n", sc);

        printf("    eigenvalues:");
        for (uint8_t k = 0; k < nr; k++)
            printf("  %8.4f", eigenvals[k]);
        printf("\n");

        for (uint8_t k = 0; k < nr; k++) {
            printf("    evec[%u] (lam=%8.4f):", k, eigenvals[k]);
            for (uint8_t r = 0; r < nr; r++)
                printf("  %6.3f%+6.3fj",
                       evec_real[r * nr + k],
                       evec_imag[r * nr + k]);
            printf("\n");
        }
    }
}

/*
 * on_cbf - Print a beamforming feedback result to the console.
 *          Called from the promiscuous RX callback for every CBF frame.
 *
 * result : (in) const wifi_cbf_result_t* parsed frame data
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

    //print_v_matrix(result);
    telemetry_send_cbf(result);
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
    ESP_ERROR_CHECK(start_monitor(MONITOR_CHANNEL, on_cbf, NULL, NULL, NULL));

    /* Loop forever; packet_scanner delivers frames via the callback */
    while (1)
        vTaskDelay(pdMS_TO_TICKS(1000));
}
