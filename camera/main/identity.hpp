#pragma once

#include <string>

#include "esp_err.h"

namespace noob::camera {

class Identity {
public:
    esp_err_t initialize();

    const std::string &device_id() const { return device_id_; }
    const std::string &boot_id() const { return boot_id_; }

private:
    std::string device_id_;
    std::string boot_id_;
};

}  // namespace noob::camera
