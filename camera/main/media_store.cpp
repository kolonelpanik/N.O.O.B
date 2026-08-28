#include "media_store.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <new>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

#include "cJSON.h"
#include "driver/sdmmc_host.h"
#include "esp_random.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "nvs.h"
#include "sdkconfig.h"
#include "sdmmc_cmd.h"

#include "camera_contract.hpp"
#include "camera_manager.hpp"

namespace noob::camera {
namespace {

constexpr char kContentFilename[] = "content.jpg";
constexpr char kManifestFilename[] = "manifest.json";
constexpr std::size_t kMaxJobsRemembered = 8;

class LockGuard {
public:
    explicit LockGuard(SemaphoreHandle_t mutex) : mutex_(mutex) {
        xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    ~LockGuard() { xSemaphoreGive(mutex_); }

private:
    SemaphoreHandle_t mutex_;
};

std::uint64_t uptime_ms() {
    return static_cast<std::uint64_t>(esp_timer_get_time() / 1000ULL);
}

bool has_exact_id_shape(const std::string &value, const char prefix) {
    if (value.size() != 34 || value[0] != prefix || value[1] != '_') {
        return false;
    }
    return std::all_of(value.begin() + 2, value.end(), [](char character) {
        return (character >= '0' && character <= '9') ||
               (character >= 'a' && character <= 'f');
    });
}

std::string random_id(const char prefix) {
    std::uint8_t bytes[16]{};
    esp_fill_random(bytes, sizeof(bytes));
    char output[35]{};
    output[0] = prefix;
    output[1] = '_';
    for (std::size_t index = 0; index < sizeof(bytes); ++index) {
        std::snprintf(output + 2 + index * 2, 3, "%02x", bytes[index]);
    }
    return output;
}

std::string join_path(const std::string &left, const std::string &right) {
    return left + "/" + right;
}

bool ensure_directory(const char *path) {
    return mkdir(path, 0700) == 0 || errno == EEXIST;
}

bool write_file(const std::string &path, const std::uint8_t *data,
                std::size_t length) {
    FILE *file = std::fopen(path.c_str(), "wb");
    if (file == nullptr) {
        return false;
    }
    const bool wrote = std::fwrite(data, 1, length, file) == length;
    const bool flushed = wrote && std::fflush(file) == 0 && fsync(fileno(file)) == 0;
    const bool closed = std::fclose(file) == 0;
    return flushed && closed;
}

std::string iso_timestamp_or_empty() {
    const std::time_t now = std::time(nullptr);
    if (now < 1700000000) {
        return {};
    }
    std::tm utc{};
    gmtime_r(&now, &utc);
    char output[32]{};
    if (std::strftime(output, sizeof(output), "%Y-%m-%dT%H:%M:%SZ", &utc) == 0) {
        return {};
    }
    return output;
}

std::uint64_t directory_size(const std::string &path) {
    DIR *directory = opendir(path.c_str());
    if (directory == nullptr) {
        return 0;
    }
    std::uint64_t total = 0;
    while (dirent *entry = readdir(directory)) {
        if (entry->d_name[0] == '.') {
            continue;
        }
        struct stat info{};
        const std::string child = join_path(path, entry->d_name);
        if (stat(child.c_str(), &info) == 0 && S_ISREG(info.st_mode)) {
            total += static_cast<std::uint64_t>(info.st_size);
        }
    }
    closedir(directory);
    return total;
}

cJSON *media_json(const MediaItem &item) {
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
    cJSON_AddNumberToObject(root, "ordinal", static_cast<double>(item.ordinal));
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

bool json_u64(const cJSON *root, const char *name, std::uint64_t *result) {
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, name);
    constexpr double kMaxExactJsonInteger = 9007199254740991.0;
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) ||
        value->valuedouble < 0 || value->valuedouble > kMaxExactJsonInteger ||
        std::floor(value->valuedouble) != value->valuedouble) {
        return false;
    }
    *result = static_cast<std::uint64_t>(value->valuedouble);
    return true;
}

bool regular_file_in_range(const std::string &path, std::uint64_t minimum,
                           std::uint64_t maximum) {
    struct stat info{};
    return stat(path.c_str(), &info) == 0 && S_ISREG(info.st_mode) &&
           info.st_size >= 0 &&
           static_cast<std::uint64_t>(info.st_size) >= minimum &&
           static_cast<std::uint64_t>(info.st_size) <= maximum;
}

bool json_u32(const cJSON *root, const char *name, std::uint32_t *result) {
    std::uint64_t value = 0;
    if (!json_u64(root, name, &value) || value > UINT32_MAX) {
        return false;
    }
    *result = static_cast<std::uint32_t>(value);
    return true;
}

}  // namespace

bool valid_media_id(const std::string &value) {
    return has_exact_id_shape(value, 'm');
}

bool valid_job_id(const std::string &value) {
    return has_exact_id_shape(value, 'j');
}

MediaStore::MediaStore(CameraManager &camera) : camera_(camera) {
    mutex_ = xSemaphoreCreateMutex();
    status_.reserve_bytes =
        static_cast<std::uint64_t>(CONFIG_NOOB_CAMERA_MIN_FREE_MIB) * 1024ULL * 1024ULL;
    status_.max_media_items = CONFIG_NOOB_CAMERA_MAX_MEDIA_ITEMS;
    status_.max_total_bytes =
        static_cast<std::uint64_t>(CONFIG_NOOB_CAMERA_MAX_MEDIA_MIB) * 1024ULL * 1024ULL;
}

MediaStore::~MediaStore() {
    if (mutex_ != nullptr) {
        vSemaphoreDelete(mutex_);
    }
}

esp_err_t MediaStore::initialize() {
    if (mutex_ == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    LockGuard lock(mutex_);
    return mount_card_locked();
}

esp_err_t MediaStore::mount_card_locked() {
    status_.state = "mounting";

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 1;  // CLK=14, CMD=15, D0=2. D1/GPIO4 and D2/GPIO12 stay unused.

    esp_vfs_fat_sdmmc_mount_config_t mount_config{};
    mount_config.format_if_mount_failed = false;
    mount_config.max_files = 8;
    mount_config.allocation_unit_size = 16 * 1024;
    mount_config.disk_status_check_enable = true;

    sdmmc_card_t *card = nullptr;
    const esp_err_t error =
        esp_vfs_fat_sdmmc_mount(kMountPoint, &host, &slot, &mount_config, &card);
    if (error != ESP_OK) {
        status_.state = error == ESP_ERR_NOT_FOUND ? "absent" : "error";
        status_.last_error = "sd_mount_failed";
        return error;
    }

    status_.mounted = true;
    if (!ensure_directory("/sdcard/NOOB") || !ensure_directory(kMediaRoot)) {
        status_.state = "error";
        status_.last_error = "media_directory_failed";
        return ESP_FAIL;
    }

    const std::string probe = "/sdcard/NOOB/.write-probe";
    static constexpr std::uint8_t kProbe[] = {'N', 'O', 'O', 'B'};
    status_.writable = write_file(probe, kProbe, sizeof(kProbe));
    if (status_.writable) {
        unlink(probe.c_str());
        status_.state = "mounted";
        status_.last_error.clear();
    } else {
        status_.state = "read_only";
        status_.last_error = "sd_not_writable";
    }

    nvs_handle_t handle = 0;
    if (nvs_open("noobcam", NVS_READWRITE, &handle) == ESP_OK) {
        std::uint64_t stored = 0;
        if (nvs_get_u64(handle, "media_seq", &stored) == ESP_OK && stored > 0) {
            next_ordinal_ = stored;
        }
        nvs_close(handle);
    }

    scan_media_locked();
    refresh_capacity_locked();
    if (status_.writable) {
        enforce_retention_locked();
    }
    return status_.writable ? ESP_OK : ESP_ERR_INVALID_STATE;
}

void MediaStore::scan_media_locked() {
    items_.clear();
    DIR *directory = opendir(kMediaRoot);
    if (directory == nullptr) {
        status_.media_count = 0;
        return;
    }

    while (dirent *entry = readdir(directory)) {
        const std::string name = entry->d_name;
        const std::string path = join_path(kMediaRoot, name);
        if (name.rfind(".partial-", 0) == 0) {
            if (!remove_tree_locked(path)) {
                status_.state = "error";
                status_.writable = false;
                status_.last_error = "partial_cleanup_failed";
            }
            continue;
        }
        if (!valid_media_id(name)) {
            continue;
        }
        MediaItem item;
        if (load_manifest_locked(path, &item) && item.id == name) {
            item.size_bytes = directory_size(path);
            items_.push_back(std::move(item));
        }
    }
    closedir(directory);
    std::sort(items_.begin(), items_.end(), [](const MediaItem &left,
                                               const MediaItem &right) {
        return left.ordinal < right.ordinal;
    });
    if (!items_.empty()) {
        next_ordinal_ = std::max(next_ordinal_, items_.back().ordinal + 1);
    }
    status_.media_count = items_.size();
}

void MediaStore::refresh_capacity_locked() {
    std::uint64_t total = 0;
    std::uint64_t free = 0;
    if (status_.mounted && esp_vfs_fat_info(kMountPoint, &total, &free) == ESP_OK) {
        status_.total_bytes = total;
        status_.free_bytes = free;
        status_.has_capacity = true;
        if (free == 0) {
            status_.state = "full";
        } else if (status_.writable &&
                   (status_.state == "full" || status_.state == "error")) {
            status_.state = "mounted";
            status_.last_error.clear();
        }
    } else {
        status_.has_capacity = false;
        if (status_.mounted) {
            status_.writable = false;
            status_.state = "error";
            status_.last_error = "sd_status_failed";
        }
    }
}

std::uint64_t MediaStore::next_ordinal_locked() {
    const std::uint64_t value = next_ordinal_++;
    nvs_handle_t handle = 0;
    if (nvs_open("noobcam", NVS_READWRITE, &handle) == ESP_OK) {
        if (nvs_set_u64(handle, "media_seq", next_ordinal_) == ESP_OK) {
            nvs_commit(handle);
        }
        nvs_close(handle);
    }
    return value;
}

esp_err_t MediaStore::write_manifest_locked(const std::string &directory,
                                            const MediaItem &item) {
    cJSON *root = media_json(item);
    if (root == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    char *encoded = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (encoded == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    const std::string path = join_path(directory, kManifestFilename);
    const bool wrote = write_file(path, reinterpret_cast<std::uint8_t *>(encoded),
                                  std::strlen(encoded));
    cJSON_free(encoded);
    return wrote ? ESP_OK : ESP_FAIL;
}

bool MediaStore::load_manifest_locked(const std::string &directory,
                                      MediaItem *item) const {
    const std::string path = join_path(directory, kManifestFilename);
    FILE *file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return false;
    }
    char buffer[1024]{};
    const std::size_t length = std::fread(buffer, 1, sizeof(buffer) - 1, file);
    const bool complete = !std::ferror(file) && std::feof(file);
    std::fclose(file);
    if (!complete || length == 0) {
        return false;
    }

    cJSON *root = cJSON_ParseWithLength(buffer, length);
    if (root == nullptr) {
        return false;
    }
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(root, "id");
    const cJSON *kind = cJSON_GetObjectItemCaseSensitive(root, "kind");
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(root, "state");
    const cJSON *created_at = cJSON_GetObjectItemCaseSensitive(root, "created_at");
    bool valid = cJSON_IsString(id) && valid_media_id(id->valuestring) &&
                 cJSON_IsString(kind) &&
                 (std::strcmp(kind->valuestring, "snapshot") == 0 ||
                  std::strcmp(kind->valuestring, "clip") == 0) &&
                 cJSON_IsString(state) &&
                 std::strcmp(state->valuestring, "complete") == 0 &&
                 (cJSON_IsNull(created_at) ||
                  (cJSON_IsString(created_at) &&
                   std::strlen(created_at->valuestring) <= 31));
    if (valid) {
        item->id = id->valuestring;
        item->kind = kind->valuestring;
        item->created_at = cJSON_IsString(created_at) ? created_at->valuestring : "";
        valid = json_u64(root, "created_uptime_ms", &item->created_uptime_ms) &&
                json_u64(root, "ordinal", &item->ordinal) &&
                json_u64(root, "size_bytes", &item->size_bytes) &&
                json_u32(root, "width", &item->width) &&
                json_u32(root, "height", &item->height) &&
                json_u32(root, "frame_count", &item->frame_count) &&
                json_u32(root, "duration_ms", &item->duration_ms);
        const cJSON *fps = cJSON_GetObjectItemCaseSensitive(root, "fps");
        if (item->kind == "snapshot") {
            item->fps = 0;
            valid = valid && cJSON_IsNull(fps) && item->ordinal > 0 &&
                    item->size_bytes > 0 && item->width == 640 &&
                    item->height == 480 && item->frame_count == 1 &&
                    item->duration_ms == 0 &&
                    regular_file_in_range(join_path(directory, kContentFilename),
                                          4, kMaxJpegBytes);
        } else {
            std::uint32_t parsed_fps = 0;
            const bool fps_valid = json_u32(root, "fps", &parsed_fps) &&
                                   parsed_fps >= 1 && parsed_fps <= kMaxClipFps;
            item->fps = parsed_fps;
            char first_name[24]{};
            char last_name[24]{};
            std::snprintf(first_name, sizeof(first_name), "%06u.jpg", 0U);
            std::snprintf(last_name, sizeof(last_name), "%06lu.jpg",
                          static_cast<unsigned long>(
                              item->frame_count == 0 ? 0 : item->frame_count - 1));
            valid = valid && fps_valid && item->ordinal > 0 &&
                    item->size_bytes > 0 && item->width == 640 &&
                    item->height == 480 && item->frame_count >= 1 &&
                    item->frame_count <= kMaxClipFrames &&
                    item->duration_ms <= kMaxClipDurationMs &&
                    regular_file_in_range(join_path(directory, first_name), 4,
                                          kMaxJpegBytes) &&
                    regular_file_in_range(join_path(directory, last_name), 4,
                                          kMaxJpegBytes);
        }
    }
    cJSON_Delete(root);
    return valid;
}

esp_err_t MediaStore::store_snapshot(std::uint32_t expected_generation,
                                     MediaItem *result) {
    const CameraStatus camera_status = camera_.status();
    if (!camera_status.enabled || !camera_status.pinmap_verified) {
        return ESP_ERR_INVALID_STATE;
    }
    if (camera_status.generation != expected_generation) {
        return ESP_ERR_INVALID_VERSION;
    }

    OwnedFrame frame;
    const std::uint64_t now = uptime_ms();
    if (!camera_.copy_latest(&frame) || now < frame.captured_uptime_ms ||
        now - frame.captured_uptime_ms > kFreshFrameMaxAgeMs) {
        return ESP_ERR_TIMEOUT;
    }

    LockGuard lock(mutex_);
    if (!status_.mounted || !status_.writable) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!status_.active_job_id.empty()) {
        return ESP_ERR_INVALID_STATE;
    }
    const CameraStatus camera_recheck = camera_.status();
    if (!camera_recheck.enabled || !camera_recheck.pinmap_verified ||
        camera_recheck.generation != expected_generation) {
        return ESP_ERR_INVALID_VERSION;
    }

    enforce_retention_locked();
    refresh_capacity_locked();
    if (status_.has_capacity && status_.free_bytes <= status_.reserve_bytes + frame.bytes.size()) {
        status_.state = "full";
        status_.last_error = "insufficient_storage";
        return ESP_ERR_NO_MEM;
    }

    MediaItem item;
    item.id = random_id('m');
    item.kind = "snapshot";
    item.created_at = iso_timestamp_or_empty();
    item.created_uptime_ms = uptime_ms();
    item.ordinal = next_ordinal_locked();
    item.width = frame.width;
    item.height = frame.height;
    item.frame_count = 1;
    item.duration_ms = 0;

    const std::string partial = join_path(kMediaRoot, ".partial-" + item.id);
    const std::string complete = join_path(kMediaRoot, item.id);
    remove_tree_locked(partial);
    if (mkdir(partial.c_str(), 0700) != 0 ||
        !write_file(join_path(partial, kContentFilename), frame.bytes.data(),
                    frame.bytes.size())) {
        remove_tree_locked(partial);
        status_.last_error = "snapshot_write_failed";
        return ESP_FAIL;
    }
    item.size_bytes = frame.bytes.size();
    if (write_manifest_locked(partial, item) != ESP_OK ||
        rename(partial.c_str(), complete.c_str()) != 0) {
        remove_tree_locked(partial);
        status_.last_error = "snapshot_commit_failed";
        return ESP_FAIL;
    }

    item.size_bytes = directory_size(complete);
    items_.push_back(item);
    status_.media_count = items_.size();
    refresh_capacity_locked();
    enforce_retention_locked();
    if (result != nullptr) {
        *result = item;
    }
    return ESP_OK;
}

esp_err_t MediaStore::start_clip(std::uint32_t duration_ms, std::uint32_t fps,
                                 std::uint32_t expected_generation,
                                 JobStatus *result) {
    if (duration_ms < 1000 || duration_ms > kMaxClipDurationMs || fps < 1 ||
        fps > kMaxClipFps ||
        ((duration_ms + 999) / 1000) * fps > kMaxClipFrames) {
        return ESP_ERR_INVALID_ARG;
    }
    const CameraStatus camera_status = camera_.status();
    if (!camera_status.enabled || !camera_status.pinmap_verified) {
        return ESP_ERR_INVALID_STATE;
    }
    if (camera_status.generation != expected_generation) {
        return ESP_ERR_INVALID_VERSION;
    }

    LockGuard lock(mutex_);
    if (!status_.mounted || !status_.writable || !status_.active_job_id.empty()) {
        return ESP_ERR_INVALID_STATE;
    }
    const CameraStatus camera_recheck = camera_.status();
    if (!camera_recheck.enabled || !camera_recheck.pinmap_verified ||
        camera_recheck.generation != expected_generation) {
        return ESP_ERR_INVALID_VERSION;
    }

    enforce_retention_locked();
    refresh_capacity_locked();
    const std::uint32_t frames_target = std::min<std::uint32_t>(
        kMaxClipFrames,
        std::max<std::uint32_t>(
            1, static_cast<std::uint32_t>(
                   (static_cast<std::uint64_t>(duration_ms) * fps) / 1000)));
    const std::uint64_t conservative_bytes =
        static_cast<std::uint64_t>(frames_target) * kMaxJpegBytes;
    if (status_.has_capacity &&
        status_.free_bytes <= status_.reserve_bytes + conservative_bytes) {
        status_.state = "full";
        status_.last_error = "insufficient_storage";
        return ESP_ERR_NO_MEM;
    }

    JobStatus job;
    job.id = random_id('j');
    job.created_uptime_ms = uptime_ms();
    job.frames_target = frames_target;
    update_job_locked(job);
    status_.active_job_id = job.id;

    auto *context = new (std::nothrow)
        ClipContext{this, job.id, random_id('m'), duration_ms, fps,
                    expected_generation};
    if (context == nullptr) {
        status_.active_job_id.clear();
        job.state = "failed";
        job.error_code = "clip_worker_unavailable";
        update_job_locked(job);
        return ESP_FAIL;
    }
    if (xTaskCreate(clip_task_entry, "noob-sd-clip", 6144, context, 4, nullptr) != pdPASS) {
        delete context;
        status_.active_job_id.clear();
        job.state = "failed";
        job.error_code = "clip_worker_unavailable";
        update_job_locked(job);
        return ESP_FAIL;
    }
    if (result != nullptr) {
        *result = job;
    }
    return ESP_OK;
}

void MediaStore::clip_task_entry(void *context) {
    auto *owned = static_cast<ClipContext *>(context);
    ClipContext copy = std::move(*owned);
    delete owned;
    copy.store->clip_task(std::move(copy));
}

void MediaStore::clip_task(ClipContext context) {
    esp_task_wdt_add(nullptr);
    JobStatus job;
    {
        LockGuard lock(mutex_);
        auto found = std::find_if(jobs_.begin(), jobs_.end(), [&](const JobStatus &candidate) {
            return candidate.id == context.job_id;
        });
        if (found != jobs_.end()) {
            job = *found;
        } else {
            job.id = context.job_id;
            job.created_uptime_ms = uptime_ms();
        }
        if (job.state != "cancelling") {
            job.state = "running";
        }
        update_job_locked(job);
    }

    const std::string partial = join_path(kMediaRoot, ".partial-" + context.media_id);
    const std::string complete = join_path(kMediaRoot, context.media_id);
    bool success = mkdir(partial.c_str(), 0700) == 0;
    std::uint64_t payload_bytes = 0;
    std::uint32_t previous_sequence = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    const std::uint32_t interval_ms = 1000 / context.fps;
    const std::uint64_t deadline_ms = uptime_ms() + context.duration_ms;

    for (std::uint32_t index = 0; success && index < job.frames_target; ++index) {
        const std::uint64_t before_frame_ms = uptime_ms();
        if (before_frame_ms >= deadline_ms) {
            break;
        }
        {
            LockGuard lock(mutex_);
            if (cancel_job_id_ == context.job_id) {
                job.state = "cancelled";
                success = false;
                break;
            }
        }
        if (camera_.status().generation != context.expected_generation) {
            job.error_code = "camera_generation_changed";
            success = false;
            break;
        }
        OwnedFrame frame;
        const std::uint32_t wait_ms = static_cast<std::uint32_t>(
            std::min<std::uint64_t>(1000, deadline_ms - before_frame_ms));
        if (!camera_.wait_for_frame(previous_sequence, wait_ms, &frame)) {
            job.error_code = "camera_frame_timeout";
            success = false;
            break;
        }
        const std::uint64_t frame_now = uptime_ms();
        if (frame_now >= deadline_ms) {
            break;
        }
        if (frame_now < frame.captured_uptime_ms ||
            frame_now - frame.captured_uptime_ms > kFreshFrameMaxAgeMs) {
            job.error_code = "camera_frame_stale";
            success = false;
            break;
        }
        previous_sequence = frame.sequence;
        width = frame.width;
        height = frame.height;
        char filename[24]{};
        std::snprintf(filename, sizeof(filename), "%06lu.jpg",
                      static_cast<unsigned long>(index));
        success = write_file(join_path(partial, filename), frame.bytes.data(),
                             frame.bytes.size());
        if (!success) {
            job.error_code = "clip_write_failed";
            break;
        }
        payload_bytes += frame.bytes.size();
        job.frames_written = index + 1;
        {
            LockGuard lock(mutex_);
            if (cancel_job_id_ == context.job_id) {
                job.state = "cancelled";
                success = false;
            } else {
                update_job_locked(job);
            }
        }
        if (!success) {
            break;
        }
        esp_task_wdt_reset();
        if (index + 1 < job.frames_target) {
            const std::uint64_t after_write_ms = uptime_ms();
            if (after_write_ms >= deadline_ms) {
                break;
            }
            const std::uint32_t delay_ms = static_cast<std::uint32_t>(
                std::min<std::uint64_t>(interval_ms,
                                        deadline_ms - after_write_ms));
            vTaskDelay(pdMS_TO_TICKS(delay_ms));
        }
    }

    MediaItem item;
    if (success && job.frames_written > 0) {
        item.id = context.media_id;
        item.kind = "clip";
        item.created_at = iso_timestamp_or_empty();
        item.created_uptime_ms = job.created_uptime_ms;
        item.size_bytes = payload_bytes;
        item.width = width;
        item.height = height;
        item.frame_count = job.frames_written;
        item.fps = context.fps;
        item.duration_ms =
            static_cast<std::uint32_t>((1000ULL * item.frame_count) / context.fps);
    }

    // Cancellation and publication share this one final critical section. A
    // successful DELETE that linearizes before this section prevents rename;
    // once rename and the completed job state are published, later DELETEs see
    // a non-active job and cannot misleadingly report cancellation accepted.
    {
        LockGuard lock(mutex_);
        if (cancel_job_id_ == context.job_id) {
            job.state = "cancelled";
            success = false;
        }

        if (success && job.frames_written > 0) {
            item.ordinal = next_ordinal_locked();
            success = write_manifest_locked(partial, item) == ESP_OK &&
                      rename(partial.c_str(), complete.c_str()) == 0;
            if (success) {
                item.size_bytes = directory_size(complete);
                items_.push_back(item);
                status_.media_count = items_.size();
            }
            if (!success && job.error_code.empty()) {
                job.error_code = "clip_commit_failed";
            }
        } else if (job.state != "cancelled" && job.error_code.empty()) {
            job.error_code = "clip_empty";
        }

        if (!success) {
            if (!remove_tree_locked(partial)) {
                job.state = "failed";
                job.error_code = "partial_cleanup_failed";
                status_.state = "error";
                status_.writable = false;
                status_.last_error = "partial_cleanup_failed";
            }
            if (job.state != "cancelled") {
                job.state = "failed";
            }
        } else {
            job.state = "complete";
            job.media_id = item.id;
            refresh_capacity_locked();
            enforce_retention_locked();
        }
        if (cancel_job_id_ == context.job_id) {
            cancel_job_id_.clear();
        }
        status_.active_job_id.clear();
        update_job_locked(job);
    }
    esp_task_wdt_reset();
    esp_task_wdt_delete(nullptr);
    vTaskDelete(nullptr);
}

esp_err_t MediaStore::cancel_job(const std::string &job_id, JobStatus *result) {
    if (!valid_job_id(job_id)) {
        return ESP_ERR_INVALID_ARG;
    }
    LockGuard lock(mutex_);
    auto found = std::find_if(jobs_.begin(), jobs_.end(), [&](const JobStatus &job) {
        return job.id == job_id;
    });
    if (found == jobs_.end()) {
        return ESP_ERR_NOT_FOUND;
    }
    if (found->state == "cancelled" || found->state == "cancelling") {
        if (result != nullptr) {
            *result = *found;
        }
        return ESP_OK;
    }
    if (status_.active_job_id != job_id ||
        (found->state != "queued" && found->state != "running")) {
        return ESP_ERR_INVALID_STATE;
    }
    cancel_job_id_ = job_id;
    found->state = "cancelling";
    if (result != nullptr) {
        *result = *found;
    }
    return ESP_OK;
}

void MediaStore::update_job_locked(const JobStatus &job) {
    auto found = std::find_if(jobs_.begin(), jobs_.end(), [&](const JobStatus &candidate) {
        return candidate.id == job.id;
    });
    if (found == jobs_.end()) {
        jobs_.push_back(job);
        if (jobs_.size() > kMaxJobsRemembered) {
            jobs_.erase(jobs_.begin());
        }
    } else {
        *found = job;
    }
}

bool MediaStore::get_job(const std::string &job_id, JobStatus *result) const {
    if (!valid_job_id(job_id) || result == nullptr) {
        return false;
    }
    LockGuard lock(mutex_);
    auto found = std::find_if(jobs_.begin(), jobs_.end(), [&](const JobStatus &job) {
        return job.id == job_id;
    });
    if (found == jobs_.end()) {
        return false;
    }
    *result = *found;
    return true;
}

StorageStatus MediaStore::status() {
    LockGuard lock(mutex_);
    refresh_capacity_locked();
    return status_;
}

std::vector<MediaItem> MediaStore::list(const std::string &cursor,
                                        std::size_t limit,
                                        std::string *next_cursor) const {
    LockGuard lock(mutex_);
    const std::size_t bounded = std::max<std::size_t>(1, std::min(limit, kMaxMediaPage));
    std::vector<MediaItem> newest(items_.rbegin(), items_.rend());
    std::size_t start = 0;
    if (!cursor.empty()) {
        auto found = std::find_if(newest.begin(), newest.end(), [&](const MediaItem &item) {
            return item.id == cursor;
        });
        if (found == newest.end()) {
            return {};
        }
        start = static_cast<std::size_t>(std::distance(newest.begin(), found)) + 1;
    }
    const std::size_t end = std::min(newest.size(), start + bounded);
    std::vector<MediaItem> page(newest.begin() + std::min(start, newest.size()),
                                newest.begin() + end);
    if (next_cursor != nullptr) {
        *next_cursor = end < newest.size() && !page.empty() ? page.back().id : "";
    }
    return page;
}

bool MediaStore::get_item(const std::string &media_id, MediaItem *result) const {
    if (!valid_media_id(media_id) || result == nullptr) {
        return false;
    }
    LockGuard lock(mutex_);
    auto found = std::find_if(items_.begin(), items_.end(), [&](const MediaItem &item) {
        return item.id == media_id;
    });
    if (found == items_.end()) {
        return false;
    }
    *result = *found;
    return true;
}

esp_err_t MediaStore::remove_item(const std::string &media_id) {
    if (!valid_media_id(media_id)) {
        return ESP_ERR_INVALID_ARG;
    }
    LockGuard lock(mutex_);
    auto found = std::find_if(items_.begin(), items_.end(), [&](const MediaItem &item) {
        return item.id == media_id;
    });
    if (found == items_.end()) {
        return ESP_ERR_NOT_FOUND;
    }
    if (!remove_tree_locked(join_path(kMediaRoot, media_id))) {
        status_.state = "error";
        status_.writable = false;
        status_.last_error = "media_delete_failed";
        return ESP_FAIL;
    }
    items_.erase(found);
    status_.media_count = items_.size();
    refresh_capacity_locked();
    return ESP_OK;
}

bool MediaStore::snapshot_content_path(const std::string &media_id,
                                       std::string *path) const {
    MediaItem item;
    if (path == nullptr || !get_item(media_id, &item) || item.kind != "snapshot") {
        return false;
    }
    *path = join_path(join_path(kMediaRoot, media_id), kContentFilename);
    return true;
}

bool MediaStore::clip_frame_path(const std::string &media_id,
                                 std::uint32_t frame_index,
                                 std::string *path) const {
    MediaItem item;
    if (path == nullptr || !get_item(media_id, &item) || item.kind != "clip" ||
        frame_index >= item.frame_count || frame_index >= kMaxClipFrames) {
        return false;
    }
    char filename[24]{};
    std::snprintf(filename, sizeof(filename), "%06lu.jpg",
                  static_cast<unsigned long>(frame_index));
    *path = join_path(join_path(kMediaRoot, media_id), filename);
    return true;
}

bool MediaStore::remove_tree_locked(const std::string &directory) const {
    bool removed = true;
    DIR *handle = opendir(directory.c_str());
    if (handle != nullptr) {
        while (dirent *entry = readdir(handle)) {
            if (std::strcmp(entry->d_name, ".") == 0 ||
                std::strcmp(entry->d_name, "..") == 0) {
                continue;
            }
            if (unlink(join_path(directory, entry->d_name).c_str()) != 0 &&
                errno != ENOENT) {
                removed = false;
            }
        }
        closedir(handle);
    } else if (errno != ENOENT) {
        removed = false;
    }
    if (rmdir(directory.c_str()) != 0 && errno != ENOENT) {
        removed = false;
    }
    return removed;
}

void MediaStore::enforce_retention_locked() {
    refresh_capacity_locked();
    auto total_media_bytes = [&]() {
        std::uint64_t total = 0;
        for (const MediaItem &item : items_) {
            total += item.size_bytes;
        }
        return total;
    };

    while (!items_.empty() &&
           (items_.size() > status_.max_media_items ||
            total_media_bytes() > status_.max_total_bytes ||
            (status_.has_capacity && status_.free_bytes < status_.reserve_bytes))) {
        const MediaItem oldest = items_.front();
        if (!remove_tree_locked(join_path(kMediaRoot, oldest.id))) {
            status_.state = "error";
            status_.writable = false;
            status_.last_error = "retention_delete_failed";
            break;
        }
        items_.erase(items_.begin());
        refresh_capacity_locked();
    }
    status_.media_count = items_.size();
}

}  // namespace noob::camera
