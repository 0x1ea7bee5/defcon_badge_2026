#ifndef MOCK_ESP_WIFI_H
#define MOCK_ESP_WIFI_H

/*
 * Manual CMock-style mock for esp_wifi functions used by packet_scanner.c.
 * Follows the CMock naming convention: FunctionName_ExpectAndReturn.
 *
 * Usage in tests:
 *   mock_esp_wifi_Init();
 *   esp_wifi_set_promiscuous_ExpectAndReturn(true, ESP_OK);
 *   // ... call code under test ...
 *   mock_esp_wifi_Verify();
 *   mock_esp_wifi_Destroy();
 */

#include "support/stub_esp_idf.h"

/* Initialise the mock state; call in setUp(). */
void mock_esp_wifi_Init(void);

/* Assert all expected calls were made; call in tearDown(). */
void mock_esp_wifi_Verify(void);

/* Free mock resources; call in tearDown(). */
void mock_esp_wifi_Destroy(void);

/* Expectation setters */
void esp_wifi_set_promiscuous_filter_ExpectAndReturn(
    const wifi_promiscuous_filter_t *filter, esp_err_t ret);

void esp_wifi_set_promiscuous_rx_cb_ExpectAndReturn(
    wifi_promiscuous_cb_t cb, esp_err_t ret);

void esp_wifi_set_promiscuous_ExpectAndReturn(bool en, esp_err_t ret);

void esp_wifi_set_channel_ExpectAndReturn(uint8_t primary,
                                           wifi_second_chan_t second,
                                           esp_err_t ret);

#endif /* MOCK_ESP_WIFI_H */
