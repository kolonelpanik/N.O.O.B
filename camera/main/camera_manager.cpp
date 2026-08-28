#include "camera_manager.hpp"

#include <algorithm>

#include "driver/gpio.h"
#include "esp_camera.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "sensor.h"
#include "sdkconfig.h"

#include "camera_contract.hpp"

namespace noob::camera {
namespace {

constexpr char kTag[] = "noob_camera";
constexpr std::uint32_t kDiagnosticFrameTimeoutMs = 5000;

// Candidate map only. A successful SCCB probe with an allowlisted sensor
// name/PID pair, working PSRAM, and a valid fresh JPEG are all required before
// pinmap_verified becomes true.
constexpr int kPinPwdn = 32;
constexpr int kPinReset = -1;
constexpr int kPinXclk = 0;
constexpr int kPinSccbSda = 26;
constexpr int kPinSccbScl = 27;
constexpr int kPinD7 = 35;
constexpr int kPinD6 = 34;
constexpr int kPinD5 = 39;
constexpr int kPinD4 = 36;
constexpr int kPinD3 = 21;
constexpr int kPinD2 = 19;
constexpr int kPinD1 = 18;
constexpr int kPinD0 = 5;
constexpr int kPinVsync = 25;
constexpr int kPinHref = 23;
constexpr int kPinPclk = 22;

std::uint64_t uptime_ms() {
    return static_cast<std::uint64_t>(esp_timer_get_time() / 1000ULL);
}

bool valid_jpeg(const camera_fb_t *frame) {
    return frame != nullptr && frame->buf != nullptr &&
           frame->format == PIXFORMAT_JPEG && frame->width == 640 &&
           frame->height == 480 && frame->len >= 4 &&
           frame->len <= kMaxJpegBytes && frame->buf[0] == 0xff &&
           frame->buf[1] == 0xd8 && frame->buf[frame->len - 2] == 0xff &&
           frame->buf[frame->len - 1] == 0xd9;
}

class LockGuard {
public:
    explicit LockGuard(SemaphoreHandle_t mutex) : mutex_(mutex) {
        xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    ~LockGuard() { xSemaphoreGive(mutex_); }

private:
    SemaphoreHandle_t mutex_;
};

}  // namespace

CameraManager::CameraManager() {
    mutex_ = xSemaphoreCreateMutex();
    latest_jpeg_.reserve(kMaxJpegBytes);
}

CameraManager::~CameraManager() {
    stop_.store(true);
    if (capture_task_handle_ != nullptr) {
        xTaskNotifyGive(capture_task_handle_);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    {
        LockGuard lock(mutex_);
        deinitialize_sensor_locked();
    }
    if (mutex_ != nullptr) {
        vSemaphoreDelete(mutex_);
    }
}

esp_err_t CameraManager::initialize(bool enable_after_probe) {
    if (mutex_ == nullptr) {
        return ESP_ERR_NO_MEM;
    }

    enable_after_probe_ = enable_after_probe;
    esp_err_t probe_error = ESP_OK;
    {
        LockGuard lock(mutex_);
        status_.psram.initialized = esp_psram_is_initialized();
        status_.psram.size_bytes =
            status_.psram.initialized ? esp_psram_get_size() : 0;
        if (!status_.psram.initialized || status_.psram.size_bytes == 0) {
            set_error_locked("psram_required");
            return ESP_ERR_NOT_SUPPORTED;
        }

        probe_error = initialize_sensor_locked();
    }

    // Keep the capture arbiter alive even when the first hardware probe fails.
    // The authenticated management plane can then expose the failure and a
    // later intentional enable can retry after a physical correction/reseat.
    BaseType_t created = xTaskCreatePinnedToCore(
        capture_task_entry, "noob-capture", 6144, this, 7,
        &capture_task_handle_, 1);
    if (created != pdPASS) {
        LockGuard lock(mutex_);
        deinitialize_sensor_locked();
        set_error_locked("capture_task_create_failed");
        return ESP_ERR_NO_MEM;
    }
    if (probe_error != ESP_OK) {
        return probe_error;
    }

    // Initialization is not successful until the capture arbiter has proved
    // one exact-VGA, marker-valid JPEG. pinmap_verified persists across the
    // optional boot-time sensor standby transition, so polling status works
    // for both boot-enabled and boot-disabled policy.
    const std::uint64_t deadline = uptime_ms() + kDiagnosticFrameTimeoutMs;
    while (uptime_ms() < deadline) {
        if (status().pinmap_verified) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    {
        LockGuard lock(mutex_);
        set_error_locked("diagnostic_frame_timeout");
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t CameraManager::initialize_sensor_locked() {
    if (status_.initialized) {
        return ESP_OK;
    }

    gpio_set_direction(static_cast<gpio_num_t>(kPinPwdn), GPIO_MODE_OUTPUT);
    gpio_set_level(static_cast<gpio_num_t>(kPinPwdn), 0);
    vTaskDelay(pdMS_TO_TICKS(20));

    camera_config_t config{};
    config.pin_pwdn = kPinPwdn;
    config.pin_reset = kPinReset;
    config.pin_xclk = kPinXclk;
    config.pin_sccb_sda = kPinSccbSda;
    config.pin_sccb_scl = kPinSccbScl;
    config.pin_d7 = kPinD7;
    config.pin_d6 = kPinD6;
    config.pin_d5 = kPinD5;
    config.pin_d4 = kPinD4;
    config.pin_d3 = kPinD3;
    config.pin_d2 = kPinD2;
    config.pin_d1 = kPinD1;
    config.pin_d0 = kPinD0;
    config.pin_vsync = kPinVsync;
    config.pin_href = kPinHref;
    config.pin_pclk = kPinPclk;
    config.xclk_freq_hz = 20000000;
    config.ledc_timer = LEDC_TIMER_0;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;
    config.sccb_i2c_port = -1;

    const esp_err_t error = esp_camera_init(&config);
    if (error != ESP_OK) {
        set_error_locked("camera_init_failed");
        gpio_set_level(static_cast<gpio_num_t>(kPinPwdn), 1);
        return error;
    }

    status_.initialized = true;
    status_.enabled = true;
    ++status_.generation;

    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == nullptr) {
        set_error_locked("sensor_probe_failed");
        deinitialize_sensor_locked();
        return ESP_ERR_NOT_FOUND;
    }

    status_.sensor.detected = true;
    status_.sensor.pid = sensor->id.PID;
    camera_sensor_info_t *info = esp_camera_sensor_get_info(&sensor->id);
    status_.sensor.name = info != nullptr && info->name != nullptr ? info->name : "unknown";
    const bool is_ov2640 = status_.sensor.pid == OV2640_PID &&
                           status_.sensor.name == "OV2640";
    const bool is_ov3660 = status_.sensor.pid == OV3660_PID &&
                           status_.sensor.name == "OV3660";
    status_.sensor.ov2640_verified = is_ov2640;
    status_.sensor.supported_sensor_verified = is_ov2640 || is_ov3660;
    if (!status_.sensor.supported_sensor_verified) {
        ESP_LOGE(kTag,
                 "Detected unsupported sensor name=%s PID=0x%04x",
                 status_.sensor.name.c_str(),
                 static_cast<unsigned>(status_.sensor.pid));
        set_error_locked("sensor_not_supported");
        deinitialize_sensor_locked();
        return ESP_ERR_NOT_SUPPORTED;
    }

    status_.last_error.clear();
    return ESP_OK;
}

void CameraManager::deinitialize_sensor_locked() {
    if (status_.initialized) {
        const esp_err_t error = esp_camera_deinit();
        if (error != ESP_OK) {
            ESP_LOGW(kTag, "Camera deinit returned %s", esp_err_to_name(error));
        }
    }
    gpio_set_direction(static_cast<gpio_num_t>(kPinPwdn), GPIO_MODE_OUTPUT);
    gpio_set_level(static_cast<gpio_num_t>(kPinPwdn), 1);
    status_.initialized = false;
    status_.enabled = false;
    status_.width = 0;
    status_.height = 0;
    status_.last_frame_uptime_ms = 0;
    latest_jpeg_.clear();
    latest_uptime_ms_ = 0;
}

esp_err_t CameraManager::set_enabled(bool enabled,
                                     std::uint32_t expected_generation,
                                     CameraStatus *result) {
    LockGuard lock(mutex_);
    if (expected_generation != status_.generation) {
        if (result != nullptr) {
            *result = status_;
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (enabled == status_.enabled) {
        if (result != nullptr) {
            *result = status_;
        }
        return ESP_OK;
    }

    esp_err_t error = ESP_OK;
    if (enabled) {
        // This is an explicit post-boot enable, not the boot-time diagnostic
        // probe. A successfully recovered sensor must remain enabled.
        enable_after_probe_ = true;
        error = initialize_sensor_locked();
    } else {
        deinitialize_sensor_locked();
        ++status_.generation;
        status_.last_error.clear();
    }
    if (result != nullptr) {
        *result = status_;
    }
    return error;
}

CameraStatus CameraManager::status() const {
    LockGuard lock(mutex_);
    return status_;
}

bool CameraManager::copy_latest(OwnedFrame *frame) const {
    if (frame == nullptr) {
        return false;
    }
    LockGuard lock(mutex_);
    if (!status_.enabled || !status_.pinmap_verified || latest_jpeg_.empty()) {
        return false;
    }
    frame->bytes = latest_jpeg_;
    frame->sequence = status_.frame_sequence;
    frame->width = status_.width;
    frame->height = status_.height;
    frame->captured_uptime_ms = latest_uptime_ms_;
    return true;
}

bool CameraManager::wait_for_frame(std::uint32_t after_sequence,
                                   std::uint32_t timeout_ms,
                                   OwnedFrame *frame) const {
    const std::uint64_t deadline = uptime_ms() + timeout_ms;
    do {
        if (copy_latest(frame) && frame->sequence != after_sequence) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    } while (uptime_ms() < deadline);
    return false;
}

void CameraManager::set_error_locked(const char *code) {
    status_.last_error = code == nullptr ? "unknown" : code;
}

void CameraManager::capture_task_entry(void *context) {
    static_cast<CameraManager *>(context)->capture_task();
}

void CameraManager::capture_task() {
    esp_task_wdt_add(nullptr);
    const TickType_t frame_delay =
        pdMS_TO_TICKS(1000 / std::max(1, CONFIG_NOOB_CAMERA_CAPTURE_FPS));

    while (!stop_.load()) {
        bool captured = false;
        {
            // This mutex is also used by enable/disable. Holding it across the
            // bounded driver get/return pair prevents deinit from racing the
            // one hardware-owning capture task.
            LockGuard lock(mutex_);
            if (status_.enabled && status_.initialized) {
                camera_fb_t *frame = esp_camera_fb_get();
                if (!valid_jpeg(frame)) {
                    if (frame != nullptr) {
                        esp_camera_fb_return(frame);
                    }
                    set_error_locked("invalid_jpeg_frame");
                } else {
                    latest_jpeg_.assign(frame->buf, frame->buf + frame->len);
                    status_.width = static_cast<std::uint32_t>(frame->width);
                    status_.height = static_cast<std::uint32_t>(frame->height);
                    ++status_.frame_sequence;
                    latest_uptime_ms_ = uptime_ms();
                    status_.last_frame_uptime_ms = latest_uptime_ms_;
                    status_.pinmap_verified =
                        status_.sensor.supported_sensor_verified &&
                        status_.psram.initialized;
                    status_.last_error.clear();
                    captured = true;
                    esp_camera_fb_return(frame);
                }
            }
        }
        if (!captured) {
            esp_task_wdt_reset();
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(250));
            continue;
        }

        if (!enable_after_probe_) {
            LockGuard lock(mutex_);
            enable_after_probe_ = true;
            deinitialize_sensor_locked();
            ++status_.generation;
        }

        esp_task_wdt_reset();
        vTaskDelay(frame_delay);
    }

    esp_task_wdt_delete(nullptr);
    capture_task_handle_ = nullptr;
    vTaskDelete(nullptr);
}

}  // namespace noob::camera
