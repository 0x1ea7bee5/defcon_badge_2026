#include <string.h>
#include <stdio.h>
#include "unity.h"
#include "mock_esp_wifi.h"

/* Max expectations tracked per function */
#define MAX_EXPECT 8

/* ---------- per-function expectation records ---------- */

typedef struct {
    bool                     expect_filter;
    wifi_promiscuous_filter_t filter_arg;
    esp_err_t                 filter_ret;
    int                       filter_calls;
} mock_set_filter_t;

typedef struct {
    bool              expect_cb;
    esp_err_t         cb_ret;
    int               cb_calls;
} mock_set_cb_t;

typedef struct {
    bool      expect_en;
    bool      en_arg;
    esp_err_t en_ret;
    int       en_calls;
    int       en_expect_count;
    bool      en_args[MAX_EXPECT];
    esp_err_t en_rets[MAX_EXPECT];
} mock_set_promisc_t;

typedef struct {
    bool               expect_chan;
    uint8_t            chan_arg;
    wifi_second_chan_t chan2_arg;
    esp_err_t          chan_ret;
    int                chan_calls;
    int                chan_expect_count;
    uint8_t            chan_args[MAX_EXPECT];
    esp_err_t          chan_rets[MAX_EXPECT];
} mock_set_chan_t;

static mock_set_filter_t  g_filter;
static mock_set_cb_t      g_cb;
static mock_set_promisc_t g_promisc;
static mock_set_chan_t     g_chan;

/* ---------- lifecycle ---------- */

void mock_esp_wifi_Init(void)
{
    memset(&g_filter,  0, sizeof(g_filter));
    memset(&g_cb,      0, sizeof(g_cb));
    memset(&g_promisc, 0, sizeof(g_promisc));
    memset(&g_chan,    0, sizeof(g_chan));
}

void mock_esp_wifi_Verify(void)
{
    if (g_filter.expect_filter)
        TEST_ASSERT_EQUAL_MESSAGE(1, g_filter.filter_calls,
            "esp_wifi_set_promiscuous_filter not called");

    if (g_cb.expect_cb)
        TEST_ASSERT_EQUAL_MESSAGE(1, g_cb.cb_calls,
            "esp_wifi_set_promiscuous_rx_cb not called");

    TEST_ASSERT_EQUAL_MESSAGE(g_promisc.en_expect_count,
        g_promisc.en_calls,
        "esp_wifi_set_promiscuous call count mismatch");

    TEST_ASSERT_EQUAL_MESSAGE(g_chan.chan_expect_count,
        g_chan.chan_calls,
        "esp_wifi_set_channel call count mismatch");
}

void mock_esp_wifi_Destroy(void)
{
    mock_esp_wifi_Init();
}

/* ---------- expectation setters ---------- */

void esp_wifi_set_promiscuous_filter_ExpectAndReturn(
    const wifi_promiscuous_filter_t *filter, esp_err_t ret)
{
    g_filter.expect_filter = true;
    if (filter)
        g_filter.filter_arg = *filter;
    g_filter.filter_ret = ret;
}

void esp_wifi_set_promiscuous_rx_cb_ExpectAndReturn(
    wifi_promiscuous_cb_t cb, esp_err_t ret)
{
    (void)cb;
    g_cb.expect_cb = true;
    g_cb.cb_ret    = ret;
}

void esp_wifi_set_promiscuous_ExpectAndReturn(bool en, esp_err_t ret)
{
    int idx = g_promisc.en_expect_count;
    TEST_ASSERT_LESS_THAN(MAX_EXPECT, idx);
    g_promisc.en_args[idx] = en;
    g_promisc.en_rets[idx] = ret;
    g_promisc.en_expect_count++;
}

void esp_wifi_set_channel_ExpectAndReturn(uint8_t primary,
                                           wifi_second_chan_t second,
                                           esp_err_t ret)
{
    (void)second;
    int idx = g_chan.chan_expect_count;
    TEST_ASSERT_LESS_THAN(MAX_EXPECT, idx);
    g_chan.chan_args[idx] = primary;
    g_chan.chan_rets[idx] = ret;
    g_chan.chan_expect_count++;
}

/* ---------- mock implementations ---------- */

esp_err_t esp_wifi_set_promiscuous_filter(
    const wifi_promiscuous_filter_t *filter)
{
    TEST_ASSERT_TRUE_MESSAGE(g_filter.expect_filter,
        "unexpected esp_wifi_set_promiscuous_filter call");
    g_filter.filter_calls++;
    return g_filter.filter_ret;
}

esp_err_t esp_wifi_set_promiscuous_rx_cb(wifi_promiscuous_cb_t cb)
{
    TEST_ASSERT_TRUE_MESSAGE(g_cb.expect_cb,
        "unexpected esp_wifi_set_promiscuous_rx_cb call");
    g_cb.cb_calls++;
    return g_cb.cb_ret;
}

esp_err_t esp_wifi_set_promiscuous(bool en)
{
    int idx = g_promisc.en_calls;
    TEST_ASSERT_LESS_THAN_MESSAGE(g_promisc.en_expect_count, idx,
        "unexpected esp_wifi_set_promiscuous call");
    TEST_ASSERT_EQUAL_MESSAGE(g_promisc.en_args[idx], en,
        "esp_wifi_set_promiscuous: wrong 'en' argument");
    g_promisc.en_calls++;
    return g_promisc.en_rets[idx];
}

esp_err_t esp_wifi_set_channel(uint8_t primary,
                                wifi_second_chan_t second)
{
    int idx = g_chan.chan_calls;
    TEST_ASSERT_LESS_THAN_MESSAGE(g_chan.chan_expect_count, idx,
        "unexpected esp_wifi_set_channel call");
    TEST_ASSERT_EQUAL_MESSAGE(g_chan.chan_args[idx], primary,
        "esp_wifi_set_channel: wrong channel argument");
    g_chan.chan_calls++;
    return g_chan.chan_rets[idx];
}
