#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_err.h"

#include "models.hpp"

namespace noob::camera {

class CameraManager;

class MediaStore {
public:
    explicit MediaStore(CameraManager &camera);
    ~MediaStore();

    MediaStore(const MediaStore &) = delete;
    MediaStore &operator=(const MediaStore &) = delete;

    esp_err_t initialize();
    StorageStatus status();

    esp_err_t store_snapshot(std::uint32_t expected_generation,
                             MediaItem *result);
    esp_err_t start_clip(std::uint32_t duration_ms, std::uint32_t fps,
                         std::uint32_t expected_generation,
                         JobStatus *result);
    esp_err_t cancel_job(const std::string &job_id, JobStatus *result);
    bool get_job(const std::string &job_id, JobStatus *result) const;

    std::vector<MediaItem> list(const std::string &cursor, std::size_t limit,
                                std::string *next_cursor) const;
    bool get_item(const std::string &media_id, MediaItem *result) const;
    esp_err_t remove_item(const std::string &media_id);

    bool snapshot_content_path(const std::string &media_id,
                               std::string *path) const;
    bool clip_frame_path(const std::string &media_id, std::uint32_t frame_index,
                         std::string *path) const;

private:
    struct ClipContext {
        MediaStore *store;
        std::string job_id;
        std::string media_id;
        std::uint32_t duration_ms;
        std::uint32_t fps;
        std::uint32_t expected_generation;
    };

    static void clip_task_entry(void *context);
    void clip_task(ClipContext context);

    esp_err_t mount_card_locked();
    void scan_media_locked();
    void refresh_capacity_locked();
    void enforce_retention_locked();
    esp_err_t write_manifest_locked(const std::string &directory,
                                    const MediaItem &item);
    bool load_manifest_locked(const std::string &directory,
                              MediaItem *item) const;
    std::uint64_t next_ordinal_locked();
    void update_job_locked(const JobStatus &job);
    bool remove_tree_locked(const std::string &directory) const;

    CameraManager &camera_;
    mutable SemaphoreHandle_t mutex_{nullptr};
    StorageStatus status_;
    std::vector<MediaItem> items_;
    std::vector<JobStatus> jobs_;
    std::string cancel_job_id_;
    std::uint64_t next_ordinal_{1};
};

bool valid_media_id(const std::string &value);
bool valid_job_id(const std::string &value);

}  // namespace noob::camera
