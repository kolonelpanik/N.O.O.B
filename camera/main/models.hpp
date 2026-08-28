#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace noob::camera {

struct OwnedFrame {
    std::vector<std::uint8_t> bytes;
    std::uint32_t sequence{0};
    std::uint32_t width{0};
    std::uint32_t height{0};
    std::uint64_t captured_uptime_ms{0};
};

struct SensorStatus {
    bool detected{false};
    std::string name;
    std::uint32_t pid{0};
    bool ov2640_verified{false};
};

struct PsramStatus {
    bool initialized{false};
    std::size_t size_bytes{0};
};

struct CameraStatus {
    bool pinmap_verified{false};
    bool enabled{false};
    bool initialized{false};
    std::uint32_t generation{0};
    SensorStatus sensor;
    PsramStatus psram;
    std::uint32_t width{0};
    std::uint32_t height{0};
    std::uint32_t frame_sequence{0};
    std::uint64_t last_frame_uptime_ms{0};
    std::string last_error;
};

struct NetworkStatus {
    std::string state{"disabled"};
    bool provisioned{false};
    bool provisioning_active{false};
    int rssi_dbm{0};
    bool has_rssi{false};
    std::string ipv4;
    std::uint32_t disconnect_count{0};
};

struct StorageStatus {
    std::string state{"unconfigured"};
    bool mounted{false};
    bool writable{false};
    std::uint64_t total_bytes{0};
    std::uint64_t free_bytes{0};
    bool has_capacity{false};
    std::uint64_t reserve_bytes{0};
    std::size_t media_count{0};
    std::string active_job_id;
    std::size_t max_media_items{0};
    std::uint64_t max_total_bytes{0};
    std::string last_error;
};

struct MediaItem {
    std::string id;
    std::string kind;
    std::string created_at;
    std::uint64_t created_uptime_ms{0};
    std::uint64_t ordinal{0};
    std::uint64_t size_bytes{0};
    std::uint32_t width{0};
    std::uint32_t height{0};
    std::uint32_t frame_count{0};
    std::uint32_t fps{0};
    std::uint32_t duration_ms{0};
};

struct JobStatus {
    std::string id;
    std::string kind{"clip"};
    std::string state{"queued"};
    std::uint64_t created_uptime_ms{0};
    std::uint32_t frames_written{0};
    std::uint32_t frames_target{0};
    std::string media_id;
    std::string error_code;
};

}  // namespace noob::camera
