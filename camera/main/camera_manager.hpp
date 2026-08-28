#pragma once

#include <atomic>
#include <cstdint>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_err.h"

#include "models.hpp"

namespace noob::camera {

class CameraManager {
public:
    CameraManager();
    ~CameraManager();

    CameraManager(const CameraManager &) = delete;
    CameraManager &operator=(const CameraManager &) = delete;

    esp_err_t initialize(bool enable_after_probe);
    esp_err_t set_enabled(bool enabled, std::uint32_t expected_generation,
                          CameraStatus *result);

    CameraStatus status() const;
    bool copy_latest(OwnedFrame *frame) const;
    bool wait_for_frame(std::uint32_t after_sequence, std::uint32_t timeout_ms,
                        OwnedFrame *frame) const;

private:
    static void capture_task_entry(void *context);
    void capture_task();
    esp_err_t initialize_sensor_locked();
    void deinitialize_sensor_locked();
    void set_error_locked(const char *code);

    mutable SemaphoreHandle_t mutex_{nullptr};
    TaskHandle_t capture_task_handle_{nullptr};
    CameraStatus status_;
    std::vector<std::uint8_t> latest_jpeg_;
    std::uint64_t latest_uptime_ms_{0};
    std::atomic<bool> stop_{false};
    bool enable_after_probe_{true};
};

}  // namespace noob::camera
