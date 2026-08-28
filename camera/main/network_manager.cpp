#include "network_manager.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "mdns.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_softap.h"
#include "sdkconfig.h"

#include "camera_contract.hpp"

namespace noob::camera {
namespace {

constexpr char kTag[] = "noob_network";
constexpr std::uint64_t kReconnectMinUs = 1000000ULL;
constexpr std::uint64_t kReconnectMaxUs = 60000000ULL;

class LockGuard {
public:
    explicit LockGuard(SemaphoreHandle_t mutex) : mutex_(mutex) {
        xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    ~LockGuard() { xSemaphoreGive(mutex_); }

private:
    SemaphoreHandle_t mutex_;
};

template <std::size_t N>
bool bounded_secret(const char (&value)[N], std::size_t minimum,
                    std::size_t maximum) {
    std::size_t length = 0;
    while (length < N && value[length] != '\0') {
        ++length;
    }
    if (length == N) {
        return false;
    }
    return length >= minimum && length <= maximum;
}

}  // namespace

NetworkManager::NetworkManager() {
    mutex_ = xSemaphoreCreateMutex();
}

NetworkManager::~NetworkManager() {
    if (reconnect_timer_ != nullptr) {
        esp_timer_stop(reconnect_timer_);
        esp_timer_delete(reconnect_timer_);
    }
    if (mdns_started_) {
        mdns_free();
    }
    if (mutex_ != nullptr) {
        vSemaphoreDelete(mutex_);
    }
}

bool NetworkManager::configuration_is_safe() {
    return bounded_secret(CONFIG_NOOB_CAMERA_PROVISIONING_POP, 8, 96) &&
           bounded_secret(CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY, 8, 63) &&
           bounded_secret(CONFIG_NOOB_CAMERA_API_TOKEN, 32, 96);
}

esp_err_t NetworkManager::initialize(const std::string &device_id,
                                     ConnectedCallback connected_callback,
                                     void *connected_context) {
    if (mutex_ == nullptr || !configuration_is_safe()) {
        ESP_LOGE(kTag,
                 "Per-device provisioning/API secrets are missing or unsafe; "
                 "network services remain disabled");
        return ESP_ERR_INVALID_STATE;
    }
    device_id_ = device_id;
    connected_callback_ = connected_callback;
    connected_context_ = connected_context;

    esp_timer_create_args_t timer_args{};
    timer_args.callback = reconnect_timer_entry;
    timer_args.arg = this;
    timer_args.dispatch_method = ESP_TIMER_TASK;
    timer_args.name = "noob-wifi-reconnect";
    ESP_RETURN_ON_ERROR(esp_timer_create(&timer_args, &reconnect_timer_), kTag,
                        "create reconnect timer");

    ESP_RETURN_ON_ERROR(esp_event_handler_register(
                            NETWORK_PROV_EVENT, ESP_EVENT_ANY_ID,
                            event_handler_entry, this),
                        kTag, "register provisioning events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(
                            WIFI_EVENT, ESP_EVENT_ANY_ID, event_handler_entry,
                            this),
                        kTag, "register Wi-Fi events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(
                            IP_EVENT, IP_EVENT_STA_GOT_IP, event_handler_entry,
                            this),
                        kTag, "register IP events");

    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&wifi_config), kTag, "initialize Wi-Fi");

    network_prov_mgr_config_t provisioning_config{};
    provisioning_config.scheme = network_prov_scheme_softap;
    // The zero-initialized event-handler pair is the documented "none" state.
    // Avoid assigning the convenience aggregate macro here so this remains
    // valid C++17 as well as valid C.
    ESP_RETURN_ON_ERROR(network_prov_mgr_init(provisioning_config), kTag,
                        "initialize provisioning manager");
    provisioning_manager_initialized_ = true;

    bool provisioned = false;
    ESP_RETURN_ON_ERROR(network_prov_mgr_is_wifi_provisioned(&provisioned), kTag,
                        "read provisioning state");
    {
        LockGuard lock(mutex_);
        status_.provisioned = provisioned;
    }

    if (!provisioned) {
        char service_name[32]{};
        std::snprintf(service_name, sizeof(service_name), "NOOB-CAM-%s",
                      device_id_.substr(device_id_.size() - 6).c_str());
        {
            LockGuard lock(mutex_);
            status_.state = "connecting";
            status_.provisioning_active = true;
        }
        ESP_LOGI(kTag,
                 "Starting protected local SoftAP provisioning for %s; "
                 "credentials are intentionally not logged",
                 service_name);
        ESP_RETURN_ON_ERROR(
            network_prov_mgr_start_provisioning(
                NETWORK_PROV_SECURITY_1,
                static_cast<const void *>(CONFIG_NOOB_CAMERA_PROVISIONING_POP),
                service_name, CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY),
            kTag, "start protected provisioning");
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(network_prov_mgr_deinit(), kTag,
                        "deinitialize provisioning manager");
    provisioning_manager_initialized_ = false;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), kTag,
                        "set station mode");
    {
        LockGuard lock(mutex_);
        status_.state = "connecting";
    }
    return esp_wifi_start();
}

NetworkStatus NetworkManager::status() const {
    LockGuard lock(mutex_);
    NetworkStatus result = status_;
    if (result.state == "connected") {
        wifi_ap_record_t record{};
        if (esp_wifi_sta_get_ap_info(&record) == ESP_OK) {
            result.rssi_dbm = record.rssi;
            result.has_rssi = true;
        }
    }
    return result;
}

void NetworkManager::event_handler_entry(void *context,
                                         esp_event_base_t event_base,
                                         std::int32_t event_id,
                                         void *event_data) {
    static_cast<NetworkManager *>(context)->handle_event(event_base, event_id,
                                                         event_data);
}

void NetworkManager::reconnect_timer_entry(void *context) {
    auto *manager = static_cast<NetworkManager *>(context);
    {
        LockGuard lock(manager->mutex_);
        manager->status_.state = "connecting";
    }
    const esp_err_t error = esp_wifi_connect();
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Bounded Wi-Fi reconnect start failed: %s",
                 esp_err_to_name(error));
    }
}

void NetworkManager::schedule_reconnect_locked() {
    const std::uint32_t exponent = std::min<std::uint32_t>(status_.disconnect_count, 6);
    const std::uint64_t base = std::min<std::uint64_t>(
        kReconnectMaxUs, kReconnectMinUs << exponent);
    const std::uint64_t jitter = esp_random() % 250000ULL;
    esp_timer_stop(reconnect_timer_);
    esp_timer_start_once(reconnect_timer_, std::min(kReconnectMaxUs, base + jitter));
}

void NetworkManager::handle_event(esp_event_base_t event_base,
                                  std::int32_t event_id, void *event_data) {
    if (event_base == NETWORK_PROV_EVENT) {
        switch (event_id) {
            case NETWORK_PROV_START: {
                LockGuard lock(mutex_);
                status_.provisioning_active = true;
                break;
            }
            case NETWORK_PROV_WIFI_CRED_RECV:
                // Never log the event payload: it contains the SSID and password.
                ESP_LOGI(kTag, "Received protected Wi-Fi provisioning payload");
                break;
            case NETWORK_PROV_WIFI_CRED_FAIL:
                ESP_LOGW(kTag, "Provisioned Wi-Fi credentials were rejected");
                break;
            case NETWORK_PROV_WIFI_CRED_SUCCESS: {
                LockGuard lock(mutex_);
                status_.provisioned = true;
                break;
            }
            case NETWORK_PROV_END: {
                const esp_err_t error = network_prov_mgr_deinit();
                if (error != ESP_OK) {
                    ESP_LOGW(kTag, "Provisioning deinit failed: %s",
                             esp_err_to_name(error));
                    // Fail closed: the provisioning HTTP transport can still
                    // own the API port, so do not start or advertise the
                    // private service until deinitialization has succeeded.
                    break;
                }
                provisioning_manager_initialized_ = false;
                {
                    LockGuard lock(mutex_);
                    status_.provisioning_active = false;
                }
                maybe_start_private_services();
                break;
            }
            default:
                break;
        }
        return;
    }

    if (event_base == WIFI_EVENT) {
        if (event_id == WIFI_EVENT_STA_START) {
            const esp_err_t error = esp_wifi_connect();
            if (error != ESP_OK) {
                ESP_LOGW(kTag, "Initial Wi-Fi connect failed: %s",
                         esp_err_to_name(error));
            }
        } else if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
            LockGuard lock(mutex_);
            status_.state = "disconnected";
            status_.has_rssi = false;
            status_.ipv4.clear();
            ++status_.disconnect_count;
            if (status_.provisioned && !status_.provisioning_active) {
                schedule_reconnect_locked();
            }
        }
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        auto *got_ip = static_cast<ip_event_got_ip_t *>(event_data);
        char address[IP4ADDR_STRLEN_MAX]{};
        esp_ip4addr_ntoa(&got_ip->ip_info.ip, address, sizeof(address));
        {
            LockGuard lock(mutex_);
            status_.state = "connected";
            status_.ipv4 = address;
            status_.disconnect_count = 0;
        }
        esp_timer_stop(reconnect_timer_);
        maybe_start_private_services();
    }
}

void NetworkManager::maybe_start_private_services() {
    {
        LockGuard lock(mutex_);
        if (status_.state != "connected" || status_.provisioning_active ||
            provisioning_manager_initialized_ || private_services_started_) {
            return;
        }
        // Reserve the transition before leaving the lock. Event callbacks run
        // on the system event loop, but this also prevents a future dispatch
        // path from starting the server twice.
        private_services_started_ = true;
    }

    if (connected_callback_ != nullptr) {
        const esp_err_t error = connected_callback_(connected_context_);
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Private API start failed after provisioning teardown: %s",
                     esp_err_to_name(error));
            LockGuard lock(mutex_);
            private_services_started_ = false;
            return;
        }
    }

    // Advertise only after the authenticated API has successfully bound its
    // port. A provisioning endpoint is never advertised as the private API.
    if (!mdns_started_ && start_mdns() != ESP_OK) {
        ESP_LOGW(kTag, "mDNS advertisement failed");
    }
}

esp_err_t NetworkManager::start_mdns() {
    esp_err_t error = mdns_init();
    if (error != ESP_OK) {
        return error;
    }
    auto fail = [&](esp_err_t failure) {
        mdns_free();
        mdns_started_ = false;
        return failure;
    };
    const std::string hostname = "noobcam-" + device_id_.substr(4);
    error = mdns_hostname_set(hostname.c_str());
    if (error != ESP_OK) {
        return fail(error);
    }
    error = mdns_instance_name_set("N.O.O.B. Environmental Camera");
    if (error != ESP_OK) {
        return fail(error);
    }
    error = mdns_service_add("N.O.O.B. Environmental Camera", kMdnsService,
                             kMdnsProtocol, CONFIG_NOOB_CAMERA_HTTP_PORT,
                             nullptr, 0);
    if (error != ESP_OK) {
        return fail(error);
    }
    mdns_txt_item_t items[] = {
        {"api", "1"},
        {"role", "environment"},
        {"fw", CONFIG_NOOB_CAMERA_FIRMWARE_VERSION},
        {"caps", "stream,snapshot,sd"},
        {"id", device_id_.c_str()},
    };
    error = mdns_service_txt_set(kMdnsService, kMdnsProtocol, items,
                                 sizeof(items) / sizeof(items[0]));
    if (error != ESP_OK) {
        return fail(error);
    }
    mdns_started_ = true;
    return ESP_OK;
}

}  // namespace noob::camera
