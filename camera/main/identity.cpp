#include "identity.hpp"

#include <cstdio>
#include <cstdint>

#include "esp_random.h"
#include "nvs.h"

namespace noob::camera {
namespace {

std::uint64_t random_u64() {
    std::uint64_t value = 0;
    do {
        esp_fill_random(&value, sizeof(value));
    } while (value == 0);
    return value;
}

std::string formatted_id(const char *prefix, std::uint64_t value) {
    char buffer[32]{};
    std::snprintf(buffer, sizeof(buffer), "%s%016llx", prefix,
                  static_cast<unsigned long long>(value));
    return buffer;
}

}  // namespace

esp_err_t Identity::initialize() {
    nvs_handle_t handle = 0;
    esp_err_t error = nvs_open("noobcam", NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }

    std::uint64_t persistent_id = 0;
    error = nvs_get_u64(handle, "device_id", &persistent_id);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        persistent_id = random_u64();
        error = nvs_set_u64(handle, "device_id", persistent_id);
        if (error == ESP_OK) {
            error = nvs_commit(handle);
        }
    }
    nvs_close(handle);
    if (error != ESP_OK) {
        return error;
    }

    device_id_ = formatted_id("cam_", persistent_id);
    boot_id_ = formatted_id("b_", random_u64());
    return ESP_OK;
}

}  // namespace noob::camera
