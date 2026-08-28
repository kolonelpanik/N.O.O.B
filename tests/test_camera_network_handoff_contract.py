from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_SOURCE = ROOT / "camera" / "main" / "network_manager.cpp"
NETWORK_HEADER = ROOT / "camera" / "main" / "network_manager.hpp"
APP_MAIN = ROOT / "camera" / "main" / "app_main.cpp"


def _function(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def test_first_provision_waits_for_manager_deinit_before_private_services() -> None:
    source = NETWORK_SOURCE.read_text(encoding="utf-8")
    branch = source[source.index("case NETWORK_PROV_END:") : source.index("default:", source.index("case NETWORK_PROV_END:"))]

    deinit = branch.index("network_prov_mgr_deinit()")
    clear_initialized = branch.index("provisioning_manager_initialized_ = false")
    clear_active = branch.index("status_.provisioning_active = false")
    start_private = branch.index("maybe_start_private_services()")
    assert deinit < clear_initialized < clear_active < start_private
    assert "if (error != ESP_OK)" in branch
    assert branch.index("break;", branch.index("if (error != ESP_OK)")) < clear_initialized


def test_got_ip_uses_the_same_fail_closed_handoff_gate() -> None:
    source = NETWORK_SOURCE.read_text(encoding="utf-8")
    got_ip = source[source.index("if (event_base == IP_EVENT") : source.index("void NetworkManager::maybe_start_private_services")]
    assert "maybe_start_private_services();" in got_ip
    assert "start_mdns()" not in got_ip
    assert "connected_callback_(" not in got_ip

    gate = _function(
        source,
        "void NetworkManager::maybe_start_private_services()",
        "esp_err_t NetworkManager::start_mdns()",
    )
    assert 'status_.state != "connected"' in gate
    assert "status_.provisioning_active" in gate
    assert "provisioning_manager_initialized_" in gate
    assert "private_services_started_" in gate
    assert gate.index("connected_callback_(") < gate.index("start_mdns()")


def test_callback_reports_api_bind_result_and_state_is_explicit() -> None:
    header = NETWORK_HEADER.read_text(encoding="utf-8")
    app = APP_MAIN.read_text(encoding="utf-8")
    assert "using ConnectedCallback = esp_err_t (*)(void *context);" in header
    assert "bool provisioning_manager_initialized_{false};" in header
    assert "bool private_services_started_{false};" in header
    assert "esp_err_t start_api_after_ip(void *)" in app
    assert "return error;" in app
