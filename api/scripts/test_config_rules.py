"""
Config Rules evaluation, no R1 and no database:

    docker compose exec backend python scripts/test_config_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.config_rules import STARTER_RULES, build_report, clean_rule, clean_rules, extract, sections_needed


def rule(field, op, value, id="r"):
    return clean_rule({"id": id, "name": id, "field": field, "op": op, "value": value})


def state(r, capture, wlans=None):
    return build_report([r], [capture], wlans)["venues"][0]["results"][r["id"]]["state"]


def venue(section, body):
    return {"id": "v1", "name": "V", "sections": {section: extract(body, section)}, "read": [section]}


RADIO = {"radioParams24G": {"allowedChannels": ["1", "6", "11"], "channelBandwidth": "AUTO", "txPower": "MAX",
                            "method": "CHANNELFLY"},
         "radioParams50G": {"allowedIndoorChannels": ["36", "52", "144"], "channelBandwidth": "160MHz"},
         "radioParamsDual5G": {"enabled": False}}


def test_operators():
    v = venue("radio", RADIO)
    assert state(rule("venue.radio.24g.channels", "only", ["1", "6", "11"]), v) == "pass"
    assert state(rule("venue.radio.24g.channels", "only", ["1", "6"]), v) == "fail"
    assert state(rule("venue.radio.5g.channels_indoor", "excludes", ["144"]), v) == "fail"
    assert state(rule("venue.radio.5g.channels_indoor", "excludes", ["165"]), v) == "pass"
    assert state(rule("venue.radio.5g.channels_indoor", "includes", ["36", "52"]), v) == "pass"
    assert state(rule("venue.radio.24g.width", "eq", "20MHz"), v) == "fail"
    assert state(rule("venue.radio.5g.width", "in", ["20MHz", "40MHz", "80MHz"]), v) == "fail"
    assert state(rule("venue.radio.24g.tx", "not_in", ["MAX"]), v) == "fail"
    r = venue("reboot", {"gatewayLossTimeout": 1800})
    assert state(rule("venue.reboot_gateway", "range", {"min": 600, "max": 3600}), r) == "pass"
    assert state(rule("venue.reboot_gateway", "range", {"max": 600}), r) == "fail"


def test_unread_is_never_a_failure():
    missing = venue("dos", {})
    assert state(rule("venue.dos", "eq", True), missing) == "unavailable"
    errored = {"id": "v1", "name": "V", "sections": {"dos": {"ok": False, "error": "HTTP 429"}}, "read": ["dos"]}
    assert state(rule("venue.dos", "eq", True), errored) == "unavailable"
    not_read = {"id": "v1", "name": "V", "sections": {}, "read": []}
    assert state(rule("venue.dos", "eq", True), not_read) == "unavailable"
    assert state(rule("venue.dos", "eq", True), venue("dos", {"enabled": False})) == "fail"


def test_dual_5g_blocks_only_5g():
    v = venue("radio", {**RADIO, "radioParamsDual5G": {"enabled": True}})
    assert state(rule("venue.radio.5g.width", "eq", "80MHz"), v) == "unavailable"
    assert state(rule("venue.radio.24g.width", "eq", "AUTO"), v) == "pass"


def test_ap_group_inheritance():
    inherited = {"radioParams24G": {"useVenueSettings": True}}
    override = {"radioParams24G": {"useVenueSettings": False, "method": "CHANNELFLY"}}
    capture = {"id": "v1", "name": "V", "read": ["group_radio"], "groups": [
        {"name": "A", "radio": extract(inherited, "group_radio")}]}
    r = rule("group.radio.24g.method", "eq", "BACKGROUND_SCANNING")
    assert state(r, capture) == "inherited"
    capture["groups"].append({"name": "B", "radio": extract(override, "group_radio")})
    assert state(r, capture) == "fail"


def test_wlans_show_under_their_own_venues():
    net = {"wlan": {"advancedCustomization": {"radioCustomization": {"phyTypeConstraint": "NONE"},
                                              "enableJoinRSSIThreshold": False, "joinRSSIThreshold": -80}}}
    wlans = {"networks": {"n1": {"id": "n1", "name": "W", "venue_ids": ["v1"], "sections": {"wifi": extract(net, "wifi")}}}}
    here = {"id": "v1", "name": "V", "sections": {}}
    elsewhere = {"id": "v2", "name": "X", "sections": {}}
    r = rule("wlan.ofdm", "eq", "OFDM")
    assert state(r, here, wlans) == "fail"
    assert state(r, elsewhere, wlans) == "na"
    assert state(rule("wlan.join_rssi", "range", {"min": -70}), here, wlans) == "na"
    assert state(r, here, {"error": "HTTP 500"}) == "unavailable"


def test_only_catalogue_fields_are_kept():
    net = {"wlan": {"passphrase": "secret", "advancedCustomization": {"clientIsolation": True}}, "secret": "x"}
    assert "secret" not in str(extract(net, "wifi"))
    assert "secret" not in str(extract({"meshPassphrase": "secret", "enabled": True}, "dos"))


def test_bad_rules_are_refused():
    for bad in [
        {"id": "a", "name": "a", "field": "nope", "op": "eq", "value": 1},
        {"id": "a", "name": "a", "field": "venue.dos", "op": "in", "value": [True]},
        {"id": "a", "name": "a", "field": "venue.dos", "op": "eq", "value": "yes"},
        {"id": "a", "name": "a", "field": "venue.reboot_server", "op": "range", "value": {"min": 9, "max": 1}},
        {"id": "a", "name": "a", "field": "venue.radio.5g.width", "op": "in", "value": []},
    ]:
        try:
            clean_rule(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_starter_rules_are_valid_and_scope_reads():
    assert sections_needed(clean_rules(STARTER_RULES)) == {
        "radio", "smart_monitor", "dos", "venue_row", "bss_coloring", "wifi"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
