#pragma once

#include <atomic>
#include <string>

#include "esp_err.h"
#include "esp_http_server.h"

#include "camera_manager.hpp"
#include "identity.hpp"
#include "media_store.hpp"
#include "network_manager.hpp"

namespace noob::camera {

class ApiServer {
public:
    ApiServer(const Identity &identity, CameraManager &camera,
              MediaStore &media, const NetworkManager &network);
    ~ApiServer();

    ApiServer(const ApiServer &) = delete;
    ApiServer &operator=(const ApiServer &) = delete;

    esp_err_t start();

private:
    struct StreamContext {
        ApiServer *server;
        httpd_req_t *request;
    };

    static esp_err_t well_known_handler(httpd_req_t *request);
    static esp_err_t health_handler(httpd_req_t *request);
    static esp_err_t status_handler(httpd_req_t *request);
    static esp_err_t camera_state_handler(httpd_req_t *request);
    static esp_err_t snapshot_handler(httpd_req_t *request);
    static esp_err_t stream_handler(httpd_req_t *request);
    static esp_err_t storage_handler(httpd_req_t *request);
    static esp_err_t storage_snapshot_handler(httpd_req_t *request);
    static esp_err_t storage_clip_handler(httpd_req_t *request);
    static esp_err_t jobs_handler(httpd_req_t *request);
    static esp_err_t media_list_handler(httpd_req_t *request);
    static esp_err_t media_item_handler(httpd_req_t *request);
    static void stream_task_entry(void *context);
    void stream_task(httpd_req_t *request);

    bool authorized(httpd_req_t *request) const;

    const Identity &identity_;
    CameraManager &camera_;
    MediaStore &media_;
    const NetworkManager &network_;
    httpd_handle_t server_{nullptr};
    std::atomic<bool> stream_claimed_{false};
};

}  // namespace noob::camera
