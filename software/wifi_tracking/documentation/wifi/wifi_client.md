# wifi_client.c / wifi_client.h

Station-mode WiFi client: channel scanning, association, disassociation,
and internet connectivity for the ESP32-C5.

---

## Design Overview

A single static `s_client` struct holds all state. Two event handlers
(`on_wifi_event`, `on_ip_event`) are registered with the ESP-IDF default
event loop and translate driver events into the user-supplied callbacks.

Scan results are buffered in a module-level `s_scan_buf` array
(max `WIFI_CLIENT_MAX_SCAN_APS` = 20 entries) and are valid only for the
duration of the `scan_done_cb_t` callback.

---

## Public API

### `wifi_client_init`

```c
esp_err_t wifi_client_init(scan_done_cb_t on_scan_done,
                            assoc_cb_t     on_assoc,
                            disassoc_cb_t  on_disassoc,
                            ip_ready_cb_t  on_ip);
```

Initializes NVS flash, the TCP/IP stack, the default event loop, a
default station netif, and the WiFi driver in station mode. Must be
called once before all other client functions.

Any callback may be NULL. Returns `ESP_ERR_INVALID_STATE` if called a
second time without an intervening `wifi_client_deinit`.

**Pitfall:** NVS flash is erased automatically if its partition is full
or has an incompatible version. This is standard ESP-IDF practice but
will discard any previously stored NVS data.

### `wifi_client_scan`

```c
esp_err_t wifi_client_scan(void);
```

Starts a passive scan across all channels. Non-blocking — returns
immediately. Results arrive via `scan_done_cb_t` when scanning completes.

### `wifi_client_connect`

```c
esp_err_t wifi_client_connect(const char *ssid, const char *password);
```

Configures station credentials and calls `esp_wifi_connect`. Non-blocking.
Association result arrives via `assoc_cb_t` on success or `disassoc_cb_t`
on failure. An IP address arrives via `ip_ready_cb_t` after DHCP completes.

Pass `NULL` or an empty string for `password` to connect to an open
(unauthenticated) network. Authenticated networks use `WIFI_AUTH_WPA2_PSK`.

### `wifi_client_disconnect`

```c
esp_err_t wifi_client_disconnect(void);
```

Disassociates from the current AP. The `disassoc_cb_t` callback fires
when the driver confirms disconnection.

### `wifi_client_deinit`

```c
esp_err_t wifi_client_deinit(void);
```

Disconnects if connected, stops and deinitializes the WiFi driver, and
resets all state. After this returns, `wifi_client_init` may be called
again.

---

## Callbacks

| Callback | When fired | Key arguments |
|---|---|---|
| `scan_done_cb_t` | Passive scan completes | `aps[]`, `count` |
| `assoc_cb_t` | Station associates with AP | `bssid[6]`, `channel` |
| `disassoc_cb_t` | Station disconnects | `reason` (driver reason code) |
| `ip_ready_cb_t` | DHCP assigns IP | `ip`, `gw` (host byte order) |

---

## Data: `wifi_client_ap_t`

Populated by the scan and delivered to `scan_done_cb_t`.

| Field | Type | Description |
|---|---|---|
| `ssid` | `char[33]` | Null-terminated SSID |
| `bssid` | `uint8_t[6]` | AP MAC address |
| `rssi` | `int8_t` | Signal strength (dBm) |
| `channel` | `uint8_t` | Primary channel |
| `authmode` | `uint8_t` | `wifi_auth_mode_t` cast to uint8 |

---

## Known Limitations

- Only WPA2-PSK and open authentication modes are set automatically.
  WPA3, enterprise, or custom PMF settings require extending
  `wifi_client_connect`.
- `wifi_client_scan` results are capped at `WIFI_CLIENT_MAX_SCAN_APS`
  (20). Increase this constant if more APs need to be reported.
- Data transfer (TCP/UDP sockets) is handled by the lwIP stack included
  with ESP-IDF and requires no additional client code; the `ip_ready_cb_t`
  signals when the stack is ready for socket use.
- The module cannot be used simultaneously with `packet_scanner` monitor
  mode. Monitor mode must be stopped (`stop_monitor`) before calling
  `wifi_client_init`, and vice versa.


### Example for sending HTTP requests

// 1. Register ip_ready callback
void on_ip(uint32_t ip, uint32_t gw) {
    // IP is live — safe to make HTTP requests now
    do_http_request();
}

wifi_client_init(on_scan, on_assoc, on_disassoc, on_ip);
wifi_client_connect("MySSID", "password");

// 2. In do_http_request():
void do_http_request(void) {
    esp_http_client_config_t cfg = {
        .url = "http://example.com/api",
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    esp_http_client_perform(client);
    esp_http_client_cleanup(client);
}




idf_component_register(SRCS ...
    PRIV_REQUIRES esp_wifi nvs_flash esp_http_client)
