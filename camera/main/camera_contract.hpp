#pragma once

#include <cstddef>
#include <cstdint>

namespace noob::camera {

inline constexpr int kApiVersion = 1;
inline constexpr char kApiBase[] = "/api/v1";
inline constexpr char kConfiguredPinmap[] = "ai_thinker_candidate";
inline constexpr char kMdnsService[] = "_noobcam";
inline constexpr char kMdnsProtocol[] = "_tcp";
inline constexpr char kMountPoint[] = "/sdcard";
inline constexpr char kMediaRoot[] = "/sdcard/NOOB/media";

inline constexpr std::size_t kMaxRequestBodyBytes = 256;
inline constexpr std::size_t kMaxJpegBytes = 256 * 1024;
inline constexpr std::size_t kMaxMediaPage = 50;
inline constexpr std::uint32_t kFreshFrameMaxAgeMs = 2000;
inline constexpr std::uint32_t kMaxClipDurationMs = 30000;
inline constexpr std::uint32_t kMaxClipFps = 5;
inline constexpr std::uint32_t kMaxClipFrames = 150;
inline constexpr std::uint32_t kStreamWaitMs = 100;

inline constexpr char kMediaIdPrefix[] = "m_";
inline constexpr char kJobIdPrefix[] = "j_";

}  // namespace noob::camera
