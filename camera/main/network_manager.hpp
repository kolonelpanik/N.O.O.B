#pragma once

#include <cstdint>
#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_err.h"
#include "esp_event.h"
#include "esp_timer.h"

#include "models.hpp"

namespace noob::camera {

class NetworkManager {
public:
    using ConnectedCallback = esp_err_t (*)(void *context);

    NetworkManager();
    ~NetworkManager();

    NetworkManager(const NetworkManager &) = delete;
    NetworkManager &operator=(const NetworkManager &) = delete;

    esp_err_t initialize(const std::string &device_id,
                         ConnectedCallback connected_callback,
                         void *connected_context);
    NetworkStatus status() const;

    static bool configuration_is_safe();

private:
    static void event_handler_entry(void *context, esp_event_base_t event_base,
                                    std::int32_t event_id, void *event_data);
    static void reconnect_timer_entry(void *context);
    void handle_event(esp_event_base_t event_base, std::int32_t event_id,
                      void *event_data);
    void schedule_reconnect_locked();
    void maybe_start_private_services();
    esp_err_t start_mdns();

    mutable SemaphoreHandle_t mutex_{nullptr};
    NetworkStatus status_;
    std::string device_id_;
    ConnectedCallback connected_callback_{nullptr};
    void *connected_context_{nullptr};
    esp_timer_handle_t reconnect_timer_{nullptr};
    bool mdns_started_{false};
    bool provisioning_manager_initialized_{false};
    bool private_services_started_{false};
};

}  // namespace noob::camera
