idf_component_register(
    SRCS
        "app_main.cpp"
        "api_server.cpp"
        "camera_manager.cpp"
        "identity.cpp"
        "media_store.cpp"
        "network_manager.cpp"
    INCLUDE_DIRS "."
    REQUIRES
        app_update
        esp_event
        esp_http_server
        esp_netif
        esp_psram
        esp_system
        esp_timer
        esp_wifi
        fatfs
        freertos
        nvs_flash
        sdmmc
        spi_flash
        esp_driver_gpio
        esp_driver_sdmmc
        esp32-camera
        mdns
        network_provisioning
        cjson
)

target_compile_features(${COMPONENT_LIB} PRIVATE cxx_std_17)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
