idf_component_register(SRCS "main.c" "wifi/packet_scanner.c"
                    PRIV_REQUIRES esp_wifi nvs_flash esp_event
                    INCLUDE_DIRS ".")
