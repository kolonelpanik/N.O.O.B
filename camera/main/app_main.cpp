#include <memory>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "api_server.hpp"
#include "camera_manager.hpp"
#include "identity.hpp"
#include "media_store.hpp"
#include "network_manager.hpp"

namespace noob::camera {
namespace {

constexpr char kTag[] = "noob_camera_main";
constexpr std::uint32_t kRequiredFlashBytes = 4U * 1024U * 1024U;

std::unique_ptr<Identity> identity;
std::unique_ptr<CameraManager> camera;
std::unique_ptr<MediaStore> media;
std::unique_ptr<NetworkManager> network;
std::unique_ptr<ApiServer> api;

esp_err_t start_api_after_ip(void *) {
    if (api == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t error = api->start();
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Authenticated local API start failed: %s",
                 esp_err_to_name(error));
        return error;
    }

    // Do not bless a pending OTA image merely because the management plane came
    // up. The diagnostic probe must also have observed the configured pin map,
    // OV2640 identity, PSRAM, and one bounded marker-valid JPEG. SD remains an
    // optional/degraded subsystem and is intentionally not an OTA validity gate.
    if (camera == nullptr || !camera->status().pinmap_verified) {
        ESP_LOGW(kTag,
                 "Pending OTA image remains unconfirmed because the camera "
                 "diagnostic gate has not passed");
        return ESP_OK;
    }
    const esp_err_t ota_error = esp_ota_mark_app_valid_cancel_rollback();
    if (ota_error != ESP_OK && ota_error != ESP_ERR_NOT_FOUND &&
        ota_error != ESP_ERR_INVALID_STATE &&
        ota_error != ESP_ERR_OTA_ROLLBACK_INVALID_STATE) {
        ESP_LOGW(kTag, "OTA validity mark returned %s",
                 esp_err_to_name(ota_error));
    }
    return ESP_OK;
}

esp_err_t initialize_nvs() {
    esp_err_t error = nvs_flash_init();
    if (error == ESP_ERR_NVS_NO_FREE_PAGES ||
        error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(kTag,
                 "NVS requires bounded recovery erase; Wi-Fi provisioning and "
                 "the nonsecret device identity will need to be recreated");
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), kTag, "erase incompatible NVS");
        error = nvs_flash_init();
    }
    return error;
}

}  // namespace
}  // namespace noob::camera

extern "C" void app_main(void) {
    using namespace noob::camera;

    ESP_ERROR_CHECK(initialize_nvs());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    std::uint32_t flash_size = 0;
    ESP_ERROR_CHECK(esp_flash_get_physical_size(nullptr, &flash_size));
    if (flash_size != kRequiredFlashBytes) {
        ESP_LOGE(kTag,
                 "Refusing the 4 MiB/two-OTA firmware contract on detected "
                 "flash size %lu bytes",
                 static_cast<unsigned long>(flash_size));
        return;
    }

    identity = std::make_unique<Identity>();
    ESP_ERROR_CHECK(identity->initialize());

    camera = std::make_unique<CameraManager>();
#ifdef CONFIG_NOOB_CAMERA_BOOT_ENABLED
    constexpr bool kEnableAfterProbe = true;
#else
    constexpr bool kEnableAfterProbe = false;
#endif
    const esp_err_t camera_error = camera->initialize(kEnableAfterProbe);
    if (camera_error != ESP_OK) {
        ESP_LOGW(kTag,
                 "Diagnostic camera probe did not pass (%s); management may "
                 "still expose the observed failure after Wi-Fi provisioning",
                 esp_err_to_name(camera_error));
    }

    media = std::make_unique<MediaStore>(*camera);
    const esp_err_t media_error = media->initialize();
    if (media_error != ESP_OK) {
        ESP_LOGW(kTag,
                 "Optional 1-bit SDMMC media store is degraded (%s); no "
                 "format operation was attempted",
                 esp_err_to_name(media_error));
    }

    network = std::make_unique<NetworkManager>();
    api = std::make_unique<ApiServer>(*identity, *camera, *media, *network);
    const esp_err_t network_error = network->initialize(
        identity->device_id(), start_api_after_ip, nullptr);
    if (network_error != ESP_OK) {
        ESP_LOGE(kTag,
                 "Protected local networking was not started (%s). Configure "
                 "unique untracked provisioning/API secrets before flashing.",
                 esp_err_to_name(network_error));
    }
}
