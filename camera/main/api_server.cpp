#include "api_server.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>

#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "camera_contract.hpp"

namespace noob::camera {
namespace {

constexpr char kTag[] = "noob_api";
constexpr char kCameraComponentVersion[] = "2.1.7";
constexpr char kStreamBoundary[] = "noob-camera-boundary";
constexpr char kHttp201[] = "201 Created";
constexpr char kHttp202[] = "202 Accepted";
constexpr char kHttp401[] = "401 Unauthorized";
constexpr char kHttp409[] = "409 Conflict";
constexpr char kHttp429[] = "429 Too Many Requests";
constexpr char kHttp503[] = "503 Service Unavailable";
constexpr char kHttp507[] = "507 Insufficient Storage";

std::uint64_t uptime_ms() {
    return static_cast<std::uint64_t>(esp_timer_get_time() / 1000ULL);
}

ApiServer *self(httpd_req_t *request) {
    return static_cast<ApiServer *>(request->user_ctx);
}

esp_err_t send_json(httpd_req_t *request, cJSON *root,
                    const char *status = HTTPD_200) {
    if (root == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    char *encoded = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (encoded == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    httpd_resp_set_status(request, status);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    const esp_err_t result = httpd_resp_send(request, encoded, HTTPD_RESP_USE_STRLEN);
    cJSON_free(encoded);
    return result;
}

esp_err_t send_error(httpd_req_t *request, const char *status,
                     const char *code, const char *message) {
    cJSON *root = cJSON_CreateObject();
    cJSON *error = cJSON_AddObjectToObject(root, "error");
    cJSON_AddStringToObject(error, "code", code);
    cJSON_AddStringToObject(error, "message", message);
    return send_json(request, root, status);
}

bool request_has_query_token(httpd_req_t *request) {
    const std::size_t length = httpd_req_get_url_query_len(request);
    if (length == 0 || length > 256) {
        return length > 256;
    }
    std::array<char, 257> query{};
    if (httpd_req_get_url_query_str(request, query.data(), query.size()) != ESP_OK) {
        return false;
    }
    return std::strstr(query.data(), "token=") != nullptr ||
           std::strstr(query.data(), "access_token=") != nullptr;
}

bool constant_time_equal(const char *left, std::size_t left_length,
                         const char *right, std::size_t right_length) {
    const std::size_t maximum = std::max(left_length, right_length);
    unsigned difference = static_cast<unsigned>(left_length ^ right_length);
    for (std::size_t index = 0; index < maximum; ++index) {
        const unsigned a = index < left_length ? static_cast<unsigned char>(left[index]) : 0;
        const unsigned b = index < right_length ? static_cast<unsigned char>(right[index]) : 0;
        difference |= a ^ b;
    }
    return difference == 0;
}

cJSON *read_json_body(httpd_req_t *request) {
    if (request->content_len == 0 || request->content_len > kMaxRequestBodyBytes) {
        return nullptr;
    }
    std::array<char, kMaxRequestBodyBytes + 1> body{};
    std::size_t received_total = 0;
    while (received_total < request->content_len) {
        const int received = httpd_req_recv(
            request, body.data() + received_total,
            request->content_len - received_total);
        if (received <= 0) {
            return nullptr;
        }
        received_total += static_cast<std::size_t>(received);
    }
    const char *parse_end = nullptr;
    return cJSON_ParseWithLengthOpts(body.data(), received_total + 1,
                                     &parse_end, true);
}

std::size_t object_field_count(const cJSON *object) {
    std::size_t count = 0;
    for (const cJSON *item = object == nullptr ? nullptr : object->child;
         item != nullptr; item = item->next) {
        ++count;
    }
    return count;
}

bool exact_state_request(cJSON *root, bool *enabled,
                         std::uint32_t *expected_generation) {
    if (!cJSON_IsObject(root) || object_field_count(root) != 2) {
        return false;
    }
    cJSON *enabled_json = cJSON_GetObjectItemCaseSensitive(root, "enabled");
    cJSON *generation_json =
        cJSON_GetObjectItemCaseSensitive(root, "expected_generation");
    if (!cJSON_IsBool(enabled_json) || !cJSON_IsNumber(generation_json) ||
        generation_json->valuedouble < 0 || generation_json->valuedouble > UINT32_MAX ||
        std::floor(generation_json->valuedouble) != generation_json->valuedouble) {
        return false;
    }
    *enabled = cJSON_IsTrue(enabled_json);
    *expected_generation = static_cast<std::uint32_t>(generation_json->valuedouble);
    return true;
}

bool exact_generation_request(cJSON *root, std::uint32_t *generation) {
    if (!cJSON_IsObject(root) || object_field_count(root) != 1) {
        return false;
    }
    cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "expected_generation");
    if (!cJSON_IsNumber(value) || value->valuedouble < 0 ||
        value->valuedouble > UINT32_MAX ||
        std::floor(value->valuedouble) != value->valuedouble) {
        return false;
    }
    *generation = static_cast<std::uint32_t>(value->valuedouble);
    return true;
}

bool exact_clip_request(cJSON *root, std::uint32_t *duration_ms,
                        std::uint32_t *fps,
                        std::uint32_t *expected_generation) {
    if (!cJSON_IsObject(root) || object_field_count(root) != 3) {
        return false;
    }
    cJSON *duration = cJSON_GetObjectItemCaseSensitive(root, "duration_ms");
    cJSON *rate = cJSON_GetObjectItemCaseSensitive(root, "fps");
    cJSON *generation =
        cJSON_GetObjectItemCaseSensitive(root, "expected_generation");
    auto valid_integer = [](const cJSON *value, double minimum, double maximum) {
        return cJSON_IsNumber(value) && value->valuedouble >= minimum &&
               value->valuedouble <= maximum &&
               std::floor(value->valuedouble) == value->valuedouble;
    };
    if (!valid_integer(duration, 1000, kMaxClipDurationMs) ||
        !valid_integer(rate, 1, kMaxClipFps) ||
        !valid_integer(generation, 0, UINT32_MAX)) {
        return false;
    }
    *duration_ms = static_cast<std::uint32_t>(duration->valuedouble);
    *fps = static_cast<std::uint32_t>(rate->valuedouble);
    *expected_generation = static_cast<std::uint32_t>(generation->valuedouble);
    const std::uint64_t frames = std::max<std::uint64_t>(
        1, (static_cast<std::uint64_t>(*duration_ms) * *fps) / 1000);
    return frames <= kMaxClipFrames;
}

cJSON *storage_json(const StorageStatus &storage) {
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "state", storage.state.c_str());
    cJSON_AddBoolToObject(root, "mounted", storage.mounted);
    cJSON_AddBoolToObject(root, "writable", storage.writable);
    if (storage.has_capacity) {
        cJSON_AddNumberToObject(root, "total_bytes",
                                static_cast<double>(storage.total_bytes));
        cJSON_AddNumberToObject(root, "free_bytes",
                                static_cast<double>(storage.free_bytes));
    } else {
        cJSON_AddNullToObject(root, "total_bytes");
        cJSON_AddNullToObject(root, "free_bytes");
    }
    cJSON_AddNumberToObject(root, "reserve_bytes",
                            static_cast<double>(storage.reserve_bytes));
    cJSON_AddNumberToObject(root, "media_count", storage.media_count);
    if (storage.active_job_id.empty()) {
        cJSON_AddNullToObject(root, "active_job_id");
    } else {
        cJSON_AddStringToObject(root, "active_job_id",
                               storage.active_job_id.c_str());
    }
    cJSON *limits = cJSON_AddObjectToObject(root, "limits");
    cJSON_AddNumberToObject(limits, "max_media_items", storage.max_media_items);
    cJSON_AddNumberToObject(limits, "max_total_bytes",
                            static_cast<double>(storage.max_total_bytes));
    cJSON_AddNumberToObject(limits, "max_clip_duration_ms", kMaxClipDurationMs);
    cJSON_AddNumberToObject(limits, "max_clip_fps", kMaxClipFps);
    cJSON_AddNumberToObject(limits, "max_clip_frames", kMaxClipFrames);
    if (storage.last_error.empty()) {
        cJSON_AddNullToObject(root, "last_error");
    } else {
        cJSON_AddStringToObject(root, "last_error", storage.last_error.c_str());
    }
    return root;
}

cJSON *media_item_json(const MediaItem &item) {
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "id", item.id.c_str());
    cJSON_AddStringToObject(root, "kind", item.kind.c_str());
    cJSON_AddStringToObject(root, "state", "complete");
    if (item.created_at.empty()) {
        cJSON_AddNullToObject(root, "created_at");
    } else {
        cJSON_AddStringToObject(root, "created_at", item.created_at.c_str());
    }
    cJSON_AddNumberToObject(root, "created_uptime_ms",
                            static_cast<double>(item.created_uptime_ms));
    cJSON_AddNumberToObject(root, "size_bytes", static_cast<double>(item.size_bytes));
    cJSON_AddNumberToObject(root, "width", item.width);
    cJSON_AddNumberToObject(root, "height", item.height);
    cJSON_AddNumberToObject(root, "frame_count", item.frame_count);
    if (item.kind == "clip") {
        cJSON_AddNumberToObject(root, "fps", item.fps);
    } else {
        cJSON_AddNullToObject(root, "fps");
    }
    cJSON_AddNumberToObject(root, "duration_ms", item.duration_ms);
    cJSON_AddStringToObject(root, "content_type",
                            item.kind == "snapshot"
                                ? "image/jpeg"
                                : "application/vnd.noob.clip+json");
    return root;
}

cJSON *job_json(const JobStatus &job) {
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "job_id", job.id.c_str());
    cJSON_AddStringToObject(root, "kind", job.kind.c_str());
    cJSON_AddStringToObject(root, "state", job.state.c_str());
    cJSON_AddNumberToObject(root, "created_uptime_ms",
                            static_cast<double>(job.created_uptime_ms));
    cJSON_AddNumberToObject(root, "frames_written", job.frames_written);
    cJSON_AddNumberToObject(root, "frames_target", job.frames_target);
    if (job.media_id.empty()) {
        cJSON_AddNullToObject(root, "media_id");
    } else {
        cJSON_AddStringToObject(root, "media_id", job.media_id.c_str());
    }
    if (job.error_code.empty()) {
        cJSON_AddNullToObject(root, "error_code");
    } else {
        cJSON_AddStringToObject(root, "error_code", job.error_code.c_str());
    }
    return root;
}

const char *reset_reason_name(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON:
            return "power_on";
        case ESP_RST_SW:
            return "software";
        case ESP_RST_PANIC:
            return "panic";
        case ESP_RST_INT_WDT:
            return "interrupt_watchdog";
        case ESP_RST_TASK_WDT:
            return "task_watchdog";
        case ESP_RST_WDT:
            return "watchdog";
        case ESP_RST_BROWNOUT:
            return "brownout";
        default:
            return "other";
    }
}

std::string path_without_query(httpd_req_t *request) {
    const char *question = std::strchr(request->uri, '?');
    return question == nullptr ? request->uri
                               : std::string(request->uri,
                                             static_cast<std::size_t>(question - request->uri));
}

esp_err_t send_file(httpd_req_t *request, const std::string &path,
                    const char *content_type, const std::string &download_name = {}) {
    FILE *file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return send_error(request, HTTPD_404, "media_not_found",
                          "The requested media object is unavailable");
    }
    httpd_resp_set_type(request, content_type);
    httpd_resp_set_hdr(request, "Cache-Control", "private, no-store");
    std::string disposition;
    if (!download_name.empty()) {
        disposition = "attachment; filename=\"" + download_name + "\"";
        httpd_resp_set_hdr(request, "Content-Disposition", disposition.c_str());
    }
    std::array<char, 2048> buffer{};
    esp_err_t result = ESP_OK;
    while (!std::feof(file)) {
        const std::size_t length = std::fread(buffer.data(), 1, buffer.size(), file);
        if (length > 0 &&
            httpd_resp_send_chunk(request, buffer.data(), length) != ESP_OK) {
            result = ESP_FAIL;
            break;
        }
        if (std::ferror(file)) {
            result = ESP_FAIL;
            break;
        }
    }
    std::fclose(file);
    httpd_resp_send_chunk(request, nullptr, 0);
    return result;
}

}  // namespace

ApiServer::ApiServer(const Identity &identity, CameraManager &camera,
                     MediaStore &media, const NetworkManager &network)
    : identity_(identity), camera_(camera), media_(media), network_(network) {}

ApiServer::~ApiServer() {
    if (server_ != nullptr) {
        httpd_stop(server_);
    }
}

esp_err_t ApiServer::start() {
    if (server_ != nullptr) {
        return ESP_OK;
    }
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = CONFIG_NOOB_CAMERA_HTTP_PORT;
    config.max_uri_handlers = 16;
    config.max_open_sockets = 5;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 5;
    config.send_wait_timeout = 5;
    config.uri_match_fn = httpd_uri_match_wildcard;
    ESP_RETURN_ON_ERROR(httpd_start(&server_, &config), kTag,
                        "start private API server");

    const auto route = [this](const char *uri, auto method, auto handler) {
        httpd_uri_t value{};
        value.uri = uri;
        value.method = method;
        value.handler = handler;
        value.user_ctx = this;
        return value;
    };
    const std::array<httpd_uri_t, 14> handlers = {
        route("/.well-known/noob-camera", HTTP_GET, well_known_handler),
        route("/api/v1/health", HTTP_GET, health_handler),
        route("/api/v1/status", HTTP_GET, status_handler),
        route("/api/v1/camera/state", HTTP_PUT, camera_state_handler),
        route("/api/v1/camera/snapshot.jpg", HTTP_GET, snapshot_handler),
        route("/api/v1/camera/stream.mjpg", HTTP_GET, stream_handler),
        route("/api/v1/storage", HTTP_GET, storage_handler),
        route("/api/v1/storage/snapshots", HTTP_POST,
              storage_snapshot_handler),
        route("/api/v1/storage/clips", HTTP_POST, storage_clip_handler),
        route("/api/v1/jobs/*", HTTP_GET, jobs_handler),
        route("/api/v1/jobs/*", HTTP_DELETE, jobs_handler),
        route("/api/v1/media", HTTP_GET, media_list_handler),
        route("/api/v1/media/*", HTTP_GET, media_item_handler),
        route("/api/v1/media/*", HTTP_DELETE, media_item_handler),
    };
    for (const httpd_uri_t &handler : handlers) {
        const esp_err_t route_error =
            httpd_register_uri_handler(server_, &handler);
        if (route_error != ESP_OK) {
            ESP_LOGE(kTag, "API route registration failed: %s",
                     esp_err_to_name(route_error));
            httpd_stop(server_);
            server_ = nullptr;
            return route_error;
        }
    }
    ESP_LOGI(kTag, "Private authenticated camera API is listening on port %d",
             CONFIG_NOOB_CAMERA_HTTP_PORT);
    return ESP_OK;
}

bool ApiServer::authorized(httpd_req_t *request) const {
    if (request_has_query_token(request)) {
        send_error(request, HTTPD_400, "query_token_forbidden",
                   "Credentials are accepted only in the Authorization header");
        return false;
    }
    const std::size_t length =
        httpd_req_get_hdr_value_len(request, "Authorization");
    if (length == 0 || length > 128) {
        httpd_resp_set_hdr(request, "WWW-Authenticate", "Bearer");
        send_error(request, kHttp401, "unauthorized",
                   "A valid gateway bearer token is required");
        return false;
    }
    std::array<char, 129> header{};
    if (httpd_req_get_hdr_value_str(request, "Authorization", header.data(),
                                    header.size()) != ESP_OK) {
        send_error(request, kHttp401, "unauthorized",
                   "A valid gateway bearer token is required");
        return false;
    }
    constexpr char kPrefix[] = "Bearer ";
    if (std::strncmp(header.data(), kPrefix, sizeof(kPrefix) - 1) != 0) {
        send_error(request, kHttp401, "unauthorized",
                   "A valid gateway bearer token is required");
        return false;
    }
    const char *provided = header.data() + sizeof(kPrefix) - 1;
    const std::size_t provided_length = std::strlen(provided);
    const std::size_t expected_length = std::strlen(CONFIG_NOOB_CAMERA_API_TOKEN);
    if (!constant_time_equal(provided, provided_length, CONFIG_NOOB_CAMERA_API_TOKEN,
                             expected_length)) {
        send_error(request, kHttp401, "unauthorized",
                   "A valid gateway bearer token is required");
        return false;
    }
    return true;
}

esp_err_t ApiServer::well_known_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "api", kApiVersion);
    cJSON_AddStringToObject(root, "device_id", server->identity_.device_id().c_str());
    cJSON_AddStringToObject(root, "role", "environment");
    cJSON_AddStringToObject(root, "api_base", kApiBase);
    cJSON_AddStringToObject(root, "authentication", "bearer");
    cJSON *capabilities = cJSON_AddArrayToObject(root, "capabilities");
    cJSON_AddItemToArray(capabilities, cJSON_CreateString("stream"));
    cJSON_AddItemToArray(capabilities, cJSON_CreateString("snapshot"));
    cJSON_AddItemToArray(capabilities, cJSON_CreateString("sensor_state"));
    cJSON_AddItemToArray(capabilities, cJSON_CreateString("sd_media"));
    return send_json(request, root);
}

esp_err_t ApiServer::health_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    const CameraStatus camera = server->camera_.status();
    const StorageStatus storage = server->media_.status();
    const NetworkStatus network = server->network_.status();
    const std::uint64_t now = uptime_ms();
    const bool camera_fresh =
        camera.last_frame_uptime_ms > 0 && now >= camera.last_frame_uptime_ms &&
        now - camera.last_frame_uptime_ms <= kFreshFrameMaxAgeMs;
    const bool camera_ok = camera.pinmap_verified && camera_fresh;
    const bool storage_ok = storage.state == "mounted" || storage.state == "absent";
    const bool degraded = network.state != "connected" ||
                          (camera.enabled && !camera_ok) ||
                          !storage_ok;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "api", kApiVersion);
    cJSON_AddStringToObject(root, "status", degraded ? "degraded" : "ok");
    cJSON_AddStringToObject(root, "device_id", server->identity_.device_id().c_str());
    cJSON_AddStringToObject(root, "boot_id", server->identity_.boot_id().c_str());
    cJSON_AddNumberToObject(root, "uptime_ms", static_cast<double>(uptime_ms()));
    cJSON *components = cJSON_AddObjectToObject(root, "components");
    cJSON_AddStringToObject(components, "wifi", network.state.c_str());
    cJSON_AddStringToObject(
        components, "camera",
        camera.enabled ? (camera_ok ? "ok" : "fault") : "disabled");
    cJSON_AddStringToObject(components, "storage", storage.state.c_str());
    return send_json(request, root);
}

esp_err_t ApiServer::status_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    const CameraStatus camera = server->camera_.status();
    const StorageStatus storage = server->media_.status();
    const NetworkStatus network = server->network_.status();
    const std::uint64_t now = uptime_ms();
    const bool has_frame = camera.last_frame_uptime_ms > 0 && now >= camera.last_frame_uptime_ms;
    const std::uint64_t age = has_frame ? now - camera.last_frame_uptime_ms : 0;

    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "api", kApiVersion);
    cJSON_AddStringToObject(root, "device_id", server->identity_.device_id().c_str());
    cJSON_AddStringToObject(root, "boot_id", server->identity_.boot_id().c_str());
    cJSON_AddNumberToObject(root, "uptime_ms", static_cast<double>(now));
    cJSON *firmware = cJSON_AddObjectToObject(root, "firmware");
    cJSON_AddStringToObject(firmware, "version", CONFIG_NOOB_CAMERA_FIRMWARE_VERSION);
    cJSON_AddStringToObject(firmware, "idf_version", esp_get_idf_version());
    cJSON_AddStringToObject(firmware, "camera_component", kCameraComponentVersion);
    cJSON *provisioning = cJSON_AddObjectToObject(root, "provisioning");
    cJSON_AddBoolToObject(provisioning, "provisioned", network.provisioned);
    cJSON_AddBoolToObject(provisioning, "active", network.provisioning_active);
    cJSON *wifi = cJSON_AddObjectToObject(root, "wifi");
    cJSON_AddStringToObject(wifi, "state", network.state.c_str());
    if (network.has_rssi) {
        cJSON_AddNumberToObject(wifi, "rssi_dbm", network.rssi_dbm);
    } else {
        cJSON_AddNullToObject(wifi, "rssi_dbm");
    }
    if (network.ipv4.empty()) {
        cJSON_AddNullToObject(wifi, "ipv4");
    } else {
        cJSON_AddStringToObject(wifi, "ipv4", network.ipv4.c_str());
    }
    cJSON *camera_json = cJSON_AddObjectToObject(root, "camera");
    cJSON_AddStringToObject(camera_json, "configured_pinmap", kConfiguredPinmap);
    cJSON_AddBoolToObject(camera_json, "pinmap_verified", camera.pinmap_verified);
    cJSON_AddBoolToObject(camera_json, "enabled", camera.enabled);
    cJSON_AddBoolToObject(camera_json, "initialized", camera.initialized);
    cJSON_AddNumberToObject(camera_json, "generation", camera.generation);
    cJSON *sensor = cJSON_AddObjectToObject(camera_json, "sensor");
    cJSON_AddBoolToObject(sensor, "detected", camera.sensor.detected);
    if (camera.sensor.name.empty()) {
        cJSON_AddNullToObject(sensor, "name");
        cJSON_AddNullToObject(sensor, "pid");
    } else {
        cJSON_AddStringToObject(sensor, "name", camera.sensor.name.c_str());
        cJSON_AddNumberToObject(sensor, "pid", camera.sensor.pid);
    }
    cJSON_AddBoolToObject(sensor, "ov2640_verified", camera.sensor.ov2640_verified);
    cJSON_AddBoolToObject(sensor, "supported_sensor_verified",
                          camera.sensor.supported_sensor_verified);
    cJSON *psram = cJSON_AddObjectToObject(camera_json, "psram");
    cJSON_AddBoolToObject(psram, "initialized", camera.psram.initialized);
    cJSON_AddNumberToObject(psram, "size_bytes",
                            static_cast<double>(camera.psram.size_bytes));
    if (camera.width == 0 || camera.height == 0) {
        cJSON_AddNullToObject(camera_json, "width");
        cJSON_AddNullToObject(camera_json, "height");
        cJSON_AddNullToObject(camera_json, "pixel_format");
    } else {
        cJSON_AddNumberToObject(camera_json, "width", camera.width);
        cJSON_AddNumberToObject(camera_json, "height", camera.height);
        cJSON_AddStringToObject(camera_json, "pixel_format", "jpeg");
    }
    cJSON_AddNumberToObject(camera_json, "frame_sequence", camera.frame_sequence);
    if (has_frame) {
        cJSON_AddNumberToObject(camera_json, "last_frame_age_ms",
                                static_cast<double>(age));
    } else {
        cJSON_AddNullToObject(camera_json, "last_frame_age_ms");
    }
    cJSON_AddBoolToObject(camera_json, "fresh",
                          camera.enabled && has_frame && age <= kFreshFrameMaxAgeMs);
    if (camera.last_error.empty()) {
        cJSON_AddNullToObject(camera_json, "last_error");
    } else {
        cJSON_AddStringToObject(camera_json, "last_error", camera.last_error.c_str());
    }
    cJSON_AddItemToObject(root, "storage", storage_json(storage));
    cJSON_AddStringToObject(root, "reset_reason", reset_reason_name(esp_reset_reason()));
    cJSON *heap = cJSON_AddObjectToObject(root, "heap");
    cJSON_AddNumberToObject(heap, "internal_free_bytes",
                            heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(heap, "internal_min_free_bytes",
                            heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(heap, "psram_free_bytes",
                            heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    return send_json(request, root);
}

esp_err_t ApiServer::camera_state_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    cJSON *body = read_json_body(request);
    bool enabled = false;
    std::uint32_t expected_generation = 0;
    const bool valid = exact_state_request(body, &enabled, &expected_generation);
    cJSON_Delete(body);
    if (!valid) {
        return send_error(request, HTTPD_400, "invalid_request",
                          "Expected exactly enabled and expected_generation");
    }
    const CameraStatus before = server->camera_.status();
    if (before.generation != expected_generation) {
        return send_error(request, kHttp409, "generation_conflict",
                          "The camera state changed; refresh status and retry intentionally");
    }
    if (!server->media_.status().active_job_id.empty()) {
        return send_error(request, kHttp409, "recording_active",
                          "Camera state cannot change while a bounded clip is active");
    }
    CameraStatus after;
    const esp_err_t error =
        server->camera_.set_enabled(enabled, expected_generation, &after);
    if (error != ESP_OK) {
        return send_error(request, kHttp503, "camera_transition_failed",
                          "The requested camera state could not be verified");
    }
    cJSON *root = cJSON_CreateObject();
    cJSON_AddBoolToObject(root, "enabled", after.enabled);
    cJSON_AddNumberToObject(root, "generation", after.generation);
    cJSON_AddBoolToObject(root, "initialized", after.initialized);
    return send_json(request, root);
}

esp_err_t ApiServer::snapshot_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    const CameraStatus status = server->camera_.status();
    if (!status.enabled) {
        return send_error(request, kHttp409, "camera_disabled",
                          "The environmental camera is disabled");
    }
    OwnedFrame frame;
    if (!server->camera_.copy_latest(&frame) ||
        uptime_ms() - frame.captured_uptime_ms > kFreshFrameMaxAgeMs) {
        return send_error(request, kHttp503, "fresh_frame_unavailable",
                          "No fresh validated JPEG is available");
    }
    char sequence[16]{};
    std::snprintf(sequence, sizeof(sequence), "%lu",
                  static_cast<unsigned long>(frame.sequence));
    httpd_resp_set_type(request, "image/jpeg");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "X-NOOB-Frame-Sequence", sequence);
    httpd_resp_set_hdr(request, "X-NOOB-Boot-ID",
                       server->identity_.boot_id().c_str());
    return httpd_resp_send(request,
                           reinterpret_cast<const char *>(frame.bytes.data()),
                           frame.bytes.size());
}

esp_err_t ApiServer::stream_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    if (!server->camera_.status().enabled) {
        return send_error(request, kHttp409, "camera_disabled",
                          "The environmental camera is disabled");
    }
    bool expected = false;
    if (!server->stream_claimed_.compare_exchange_strong(expected, true)) {
        return send_error(request, kHttp429, "stream_busy",
                          "The N.O.O.B. gateway already owns the upstream stream");
    }

    // A synchronous MJPEG handler monopolizes the ESP HTTP server task and
    // would make status, camera-off, and recording-stop unreachable for the
    // lifetime of the stream. Transfer this request to a bounded worker so the
    // authenticated control plane remains responsive.
    httpd_req_t *async_request = nullptr;
    const esp_err_t begin_error =
        httpd_req_async_handler_begin(request, &async_request);
    if (begin_error != ESP_OK) {
        server->stream_claimed_.store(false);
        return send_error(request, kHttp503, "stream_worker_unavailable",
                          "The bounded MJPEG worker could not be allocated");
    }
    auto *context = new (std::nothrow) StreamContext{server, async_request};
    if (context == nullptr ||
        xTaskCreate(stream_task_entry, "noob-mjpeg", 6144, context, 5,
                    nullptr) != pdPASS) {
        delete context;
        send_error(async_request, kHttp503, "stream_worker_unavailable",
                   "The bounded MJPEG worker could not be allocated");
        httpd_req_async_handler_complete(async_request);
        server->stream_claimed_.store(false);
    }
    return ESP_OK;
}

void ApiServer::stream_task_entry(void *context) {
    auto *owned = static_cast<StreamContext *>(context);
    ApiServer *server = owned->server;
    httpd_req_t *request = owned->request;
    delete owned;
    server->stream_task(request);
    vTaskDelete(nullptr);
}

void ApiServer::stream_task(httpd_req_t *request) {
    esp_task_wdt_add(nullptr);

    std::string content_type =
        std::string("multipart/x-mixed-replace; boundary=") + kStreamBoundary;
    httpd_resp_set_type(request, content_type.c_str());
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    std::uint32_t previous_sequence = 0;
    while (camera_.status().enabled) {
        OwnedFrame frame;
        if (!camera_.wait_for_frame(previous_sequence, kStreamWaitMs, &frame)) {
            esp_task_wdt_reset();
            continue;
        }
        previous_sequence = frame.sequence;
        char header[160]{};
        const int header_length = std::snprintf(
            header, sizeof(header),
            "\r\n--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n"
            "X-NOOB-Frame-Sequence: %lu\r\n\r\n",
            kStreamBoundary, static_cast<unsigned>(frame.bytes.size()),
            static_cast<unsigned long>(frame.sequence));
        if (header_length <= 0 ||
            httpd_resp_send_chunk(request, header,
                                  static_cast<std::size_t>(header_length)) != ESP_OK ||
            httpd_resp_send_chunk(
                request, reinterpret_cast<const char *>(frame.bytes.data()),
                frame.bytes.size()) != ESP_OK) {
            break;
        }
        esp_task_wdt_reset();
    }
    httpd_resp_send_chunk(request, nullptr, 0);
    esp_task_wdt_reset();
    esp_task_wdt_delete(nullptr);
    httpd_req_async_handler_complete(request);
    stream_claimed_.store(false);
}

esp_err_t ApiServer::storage_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    return send_json(request, storage_json(server->media_.status()));
}

esp_err_t ApiServer::storage_snapshot_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    cJSON *body = read_json_body(request);
    std::uint32_t generation = 0;
    const bool valid = exact_generation_request(body, &generation);
    cJSON_Delete(body);
    if (!valid) {
        return send_error(request, HTTPD_400, "invalid_request",
                          "Expected exactly expected_generation");
    }
    const CameraStatus camera_status = server->camera_.status();
    if (!camera_status.enabled) {
        return send_error(request, kHttp409, "camera_disabled",
                          "The environmental camera is disabled");
    }
    if (camera_status.generation != generation) {
        return send_error(request, kHttp409, "generation_conflict",
                          "The camera state changed; refresh status first");
    }
    MediaItem item;
    const esp_err_t error = server->media_.store_snapshot(generation, &item);
    if (error == ESP_ERR_NO_MEM) {
        return send_error(request, kHttp507, "insufficient_storage",
                          "Retention could not recover enough writable space");
    }
    if (error != ESP_OK) {
        return send_error(request, kHttp503, "snapshot_store_failed",
                          "A completed atomic snapshot could not be verified");
    }
    cJSON *root = cJSON_CreateObject();
    cJSON_AddItemToObject(root, "item", media_item_json(item));
    return send_json(request, root, kHttp201);
}

esp_err_t ApiServer::storage_clip_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    cJSON *body = read_json_body(request);
    std::uint32_t duration_ms = 0;
    std::uint32_t fps = 0;
    std::uint32_t generation = 0;
    const bool valid = exact_clip_request(body, &duration_ms, &fps, &generation);
    cJSON_Delete(body);
    if (!valid) {
        return send_error(request, HTTPD_400, "invalid_request",
                          "Clip duration/fps/generation is outside the bounded contract");
    }
    const CameraStatus camera_status = server->camera_.status();
    if (!camera_status.enabled) {
        return send_error(request, kHttp409, "camera_disabled",
                          "The environmental camera is disabled");
    }
    if (camera_status.generation != generation) {
        return send_error(request, kHttp409, "generation_conflict",
                          "The camera state changed; refresh status first");
    }
    JobStatus job;
    const esp_err_t error =
        server->media_.start_clip(duration_ms, fps, generation, &job);
    if (error == ESP_ERR_NO_MEM) {
        return send_error(request, kHttp507, "insufficient_storage",
                          "Retention could not reserve the bounded clip budget");
    }
    if (error == ESP_FAIL) {
        return send_error(request, kHttp503, "clip_worker_unavailable",
                          "The bounded clip worker could not be allocated");
    }
    if (error != ESP_OK) {
        return send_error(request, kHttp409, "clip_unavailable",
                          "Storage or the single clip worker is unavailable");
    }
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "job_id", job.id.c_str());
    cJSON_AddStringToObject(root, "state", "queued");
    return send_json(request, root, kHttp202);
}

esp_err_t ApiServer::jobs_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    const std::string path = path_without_query(request);
    constexpr char prefix[] = "/api/v1/jobs/";
    const std::string id = path.substr(sizeof(prefix) - 1);
    if (request->method == HTTP_DELETE) {
        JobStatus cancelled;
        const esp_err_t cancel_error = server->media_.cancel_job(id, &cancelled);
        if (cancel_error == ESP_ERR_NOT_FOUND || cancel_error == ESP_ERR_INVALID_ARG) {
            return send_error(request, HTTPD_404, "job_not_found",
                              "The bounded job is unknown or expired");
        }
        if (cancel_error != ESP_OK) {
            return send_error(request, kHttp409, "job_not_active",
                              "Only the active bounded clip can be cancelled");
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "job_id", cancelled.id.c_str());
        cJSON_AddStringToObject(root, "state", cancelled.state.c_str());
        return send_json(request, root);
    }
    JobStatus job;
    if (!valid_job_id(id) || !server->media_.get_job(id, &job)) {
        return send_error(request, HTTPD_404, "job_not_found",
                          "The bounded job is unknown or expired");
    }
    return send_json(request, job_json(job));
}

esp_err_t ApiServer::media_list_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    std::string cursor;
    std::size_t limit = 20;
    const std::size_t query_length = httpd_req_get_url_query_len(request);
    if (query_length > 0 && query_length <= 256) {
        std::array<char, 257> query{};
        if (httpd_req_get_url_query_str(request, query.data(), query.size()) == ESP_OK) {
            std::array<char, 129> cursor_value{};
            if (httpd_query_key_value(query.data(), "cursor", cursor_value.data(),
                                      cursor_value.size()) == ESP_OK) {
                cursor = cursor_value.data();
                if (!valid_media_id(cursor)) {
                    return send_error(request, HTTPD_400, "invalid_cursor",
                                      "The media cursor is invalid or expired");
                }
            }
            std::array<char, 8> limit_value{};
            if (httpd_query_key_value(query.data(), "limit", limit_value.data(),
                                      limit_value.size()) == ESP_OK) {
                char *end = nullptr;
                errno = 0;
                const long parsed = std::strtol(limit_value.data(), &end, 10);
                if (errno != 0 || end == limit_value.data() || *end != '\0' ||
                    parsed < 1 || parsed > static_cast<long>(kMaxMediaPage)) {
                    return send_error(request, HTTPD_400, "invalid_limit",
                                      "Media limit must be between 1 and 50");
                }
                limit = static_cast<std::size_t>(parsed);
            }
        }
    }
    std::string next_cursor;
    const std::vector<MediaItem> items =
        server->media_.list(cursor, limit, &next_cursor);
    if (!cursor.empty() && items.empty()) {
        MediaItem cursor_item;
        if (!server->media_.get_item(cursor, &cursor_item)) {
            return send_error(request, HTTPD_400, "invalid_cursor",
                              "The media cursor is invalid or expired");
        }
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *array = cJSON_AddArrayToObject(root, "items");
    for (const MediaItem &item : items) {
        cJSON_AddItemToArray(array, media_item_json(item));
    }
    if (next_cursor.empty()) {
        cJSON_AddNullToObject(root, "next_cursor");
    } else {
        cJSON_AddStringToObject(root, "next_cursor", next_cursor.c_str());
    }
    return send_json(request, root);
}

esp_err_t ApiServer::media_item_handler(httpd_req_t *request) {
    ApiServer *server = self(request);
    if (!server->authorized(request)) {
        return ESP_OK;
    }
    const std::string path = path_without_query(request);
    constexpr char prefix[] = "/api/v1/media/";
    const std::string remainder = path.substr(sizeof(prefix) - 1);
    const std::size_t slash = remainder.find('/');
    const std::string id = remainder.substr(0, slash);
    if (!valid_media_id(id)) {
        return send_error(request, HTTPD_404, "media_not_found",
                          "The requested media object is unknown");
    }

    if (request->method == HTTP_DELETE) {
        if (slash != std::string::npos) {
            return send_error(request, HTTPD_404, "media_not_found",
                              "The requested media object is unknown");
        }
        const esp_err_t error = server->media_.remove_item(id);
        if (error == ESP_ERR_NOT_FOUND || error == ESP_ERR_INVALID_ARG) {
            return send_error(request, HTTPD_404, "media_not_found",
                              "The requested media object is unknown");
        }
        if (error != ESP_OK) {
            return send_error(request, kHttp503, "media_delete_failed",
                              "The completed media object could not be removed");
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "id", id.c_str());
        cJSON_AddBoolToObject(root, "deleted", true);
        return send_json(request, root);
    }

    if (slash == std::string::npos) {
        MediaItem item;
        if (!server->media_.get_item(id, &item)) {
            return send_error(request, HTTPD_404, "media_not_found",
                              "The requested media object is unknown");
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddItemToObject(root, "item", media_item_json(item));
        return send_json(request, root);
    }

    const std::string suffix = remainder.substr(slash + 1);
    if (suffix == "content") {
        std::string content_path;
        if (!server->media_.snapshot_content_path(id, &content_path)) {
            return send_error(request, HTTPD_404, "media_not_found",
                              "Snapshot content is unavailable");
        }
        return send_file(request, content_path, "image/jpeg", id + ".jpg");
    }

    constexpr char frames_prefix[] = "frames/";
    if (suffix.rfind(frames_prefix, 0) == 0 &&
        suffix.size() > sizeof(frames_prefix) - 1 + 4 &&
        suffix.substr(suffix.size() - 4) == ".jpg") {
        const std::string index_text = suffix.substr(
            sizeof(frames_prefix) - 1,
            suffix.size() - (sizeof(frames_prefix) - 1) - 4);
        char *end = nullptr;
        errno = 0;
        const unsigned long parsed = std::strtoul(index_text.c_str(), &end, 10);
        if (errno != 0 || end == index_text.c_str() || *end != '\0' ||
            parsed >= kMaxClipFrames) {
            return send_error(request, HTTPD_404, "frame_not_found",
                              "The requested clip frame is unavailable");
        }
        std::string frame_path;
        if (!server->media_.clip_frame_path(id, static_cast<std::uint32_t>(parsed),
                                            &frame_path)) {
            return send_error(request, HTTPD_404, "frame_not_found",
                              "The requested clip frame is unavailable");
        }
        char filename[64]{};
        std::snprintf(filename, sizeof(filename), "%s-%06lu.jpg", id.c_str(), parsed);
        return send_file(request, frame_path, "image/jpeg", filename);
    }

    return send_error(request, HTTPD_404, "media_not_found",
                      "The requested media representation is unknown");
}

}  // namespace noob::camera
