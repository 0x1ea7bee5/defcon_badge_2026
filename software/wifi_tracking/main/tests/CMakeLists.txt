cmake_minimum_required(VERSION 3.16)
project(test_packet_scanner C)

set(CMAKE_C_STANDARD 99)

# Fetch Unity if not already present
include(FetchContent)
FetchContent_Declare(
    unity
    GIT_REPOSITORY https://github.com/ThrowTheSwitch/Unity.git
    GIT_TAG        v2.6.0
)
FetchContent_MakeAvailable(unity)

# Support dir shadows real ESP-IDF headers so the host compiler
# never looks for them in the SDK path.
set(SUPPORT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/support)
set(WIFI_DIR    ${CMAKE_CURRENT_SOURCE_DIR}/../wifi)

add_executable(test_packet_scanner
    ${WIFI_DIR}/packet_scanner.c
    ${CMAKE_CURRENT_SOURCE_DIR}/mock_esp_wifi.c
    ${CMAKE_CURRENT_SOURCE_DIR}/test_packet_scanner.c
)

target_include_directories(test_packet_scanner PRIVATE
    ${SUPPORT_DIR}   # must come first to shadow SDK headers
    ${WIFI_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(test_packet_scanner PRIVATE unity)

# Run tests as part of ctest
add_test(NAME packet_scanner COMMAND test_packet_scanner)
enable_testing()
