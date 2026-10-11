"""Which (script, variant) belongs to which probe case, for the probeB batch (buses, hierarchy,
naming, units-pads). Taken from coarse/caseidx/<category>.json scenario_script fields: a script
listed without variants contributes every scenario it runs; scripts that make no tool call
(censuses, ERC or model readouts) are listed in NO_CALL with the reason.
"""

# "orig" below is a provenance label only: the scratch directory the original probe scripts lived
# in (not durable, never read), written as a relative label and not as a path.

SCRIPTS: dict = {}


def add(sid, cat, folder, mapping):
    SCRIPTS[sid] = {"cat": cat, "orig": f"pr57pt/{folder}/{sid}.py", "v": mapping}


def same(vids, cases):
    return {v: list(cases) for v in vids}


# ------------------------------------------------------------------------------------- buses
B = "probe-buses"
add("s01_stub_vs_bus", "buses", B, same(["s01a", "s01b", "s01c", "s01d", "s01e"], ["BUS-OK1"]))
add("s02_stub_vs_entry", "buses", B, {
    "s02a": ["BUS-OK1"], "s02b": ["BUS-OK1"], "s02c": ["BUS-P8", "BUS-OK1"], "s02d": ["BUS-OK1"],
    "s02e": ["BUS-OK1"]})
add("s03_pin_on_bus", "buses", B, {
    "s03a": ["BUS-B2"], "s03b2": ["BUS-B2"], "s03c": ["BUS-B2"], "s03d": ["BUS-P5"],
    "s03e": ["BUS-P5"]})
add("s04_member_workflow", "buses", B, {
    "s04a": ["BUS-M1"], "s04b": ["BUS-M1"], "s04d": ["BUS-M1"], "s04f": ["BUS-M1", "BUS-M2"]})
add("s05_shared_entry_point", "buses", B, {
    "s05a": ["BUS-P4"], "s05b": ["BUS-P1"], "s05c": ["BUS-P1", "BUS-M2"], "s05d": ["BUS-P2"],
    "s05u": ["BUS-P1"]})
add("s06_bus_names", "buses", B, same(["s06a", "s06c", "s06d"], ["BUS-P7"]))
add("s07_member_leak", "buses", B, same(["s07a", "s07b", "s07c", "s07d"], ["BUS-B1"]))
add("s08_connect_pins_bus", "buses", B, {
    "s08a": ["BUS-P6", "BUS-OK1"], "s08b": ["BUS-P6", "BUS-OK1"], "s08c": ["BUS-P6", "BUS-OK1"],
    "s08d": ["BUS-OK1"], "s08e": ["BUS-P6", "BUS-OK1"]})
# s09: dynamic variants, matched by prefix (see cases_for)
add("s09_demo_sweep", "buses", B, {"PartA": ["BUS-M1"], "PartB": ["BUS-P1", "BUS-M2"]})
add("c05_partB_fullproject", "buses", B, {"c05": ["BUS-P1"]})
add("s10_multi_and_cap", "buses", B, {
    "s10a": ["BUS-P3", "BUS-M1"], "s10b": ["BUS-OK1"], "s10c": ["BUS-OK1"], "s10d": ["BUS-P6"]})
add("s12_royalblue_member", "buses", B, {"s12a": ["BUS-M1", "BUS-P4"], "s12b": ["BUS-P1"]})
add("s13_global_bus_member", "buses", B, same(["s13a", "s13c"], ["BUS-B1"]))
add("s14_entry_symmetry", "buses", B, {
    "s14a": ["BUS-OK1"], "s14b": ["BUS-OK1"], "s14c": ["BUS-P1"], "s14d": ["BUS-P6", "BUS-OK1"]})

# --------------------------------------------------------------------------------- hierarchy
H = "probe-hierarchy"
h01 = {}
for var in ("power", "global", "local", "plain"):
    for N in ("FRESH", "N1", "GND"):
        cs = ["HIER-01"]
        if N == "FRESH":
            cs.append("HIER-04")
        if N == "FRESH" and var in ("local", "plain"):
            cs.append("HIER-05")
        if var == "plain":
            cs.append("HIER-08")
        h01[f"h01_{var}_{N}"] = cs
add("h01_h13_sheetpin_carrier", "hierarchy", H, h01)
add("h02_label_equals_sheetpin", "hierarchy", H, {
    "h02_power_alone": ["HIER-04", "HIER-08"], "h02_plain_alone": ["HIER-05", "HIER-08"],
    "h02_power_r5": ["HIER-08"], "h02_plain_r5": ["HIER-08"]})
add("h03_alias_two_calls", "hierarchy", H, {"h03_alias": ["HIER-04"]})
add("h04_sheetpin_on_wire_interior", "hierarchy", H, same(
    ["h04_edge_GND", "h04_edge_N1", "h04_into_GND", "h04_into_N1"], ["HIER-06"]))
add("h04b_connect_pins_interior", "hierarchy", H, {"h04b_edge_connect": ["HIER-06"]})
h05 = {}
for var in ("power", "plain"):
    for other in ("R3", "R6", "R4"):
        cs = ["HIER-02"]
        if var == "plain" and other == "R4":
            cs.append("HIER-05")
        if var == "plain":
            cs.append("HIER-08")
        h05[f"h05_{var}_R1_{other}"] = cs
add("h05_connect_pins_ports", "hierarchy", H, h05)
h06 = {}
for tag in ("single", "multi"):
    for c in ("R11_G", "R11_p5V", "R11_H", "R11_FRESH", "R16_G", "R16_H"):
        h06[f"h06_{tag}_{c}"] = ["HIER-16"]
h06["h06_multi_R21_H"] = ["HIER-16", "HIER-17"]
add("h06_child_hier_label", "hierarchy", H, h06)
add("h07_duplicate_sheet_names", "hierarchy", H, same(
    ["h07a_ctl", "h07a_dup", "h07b_ctl", "h07b_dup"], ["HIER-09"]))
add("h08_cross_sheet_open", "hierarchy", H, same(
    [f"h08_{w}_{N}" for w in ("only_root", "with") for N in ("GND", "SDA", "VCC")], ["HIER-10"]))
add("h09_cross_sheet_identity", "hierarchy", H, same(["h09_9a", "h09_9b"], ["HIER-12"]))
add("h10_child_alone_nested", "hierarchy", H, {
    "h10a_nested_N1": ["HIER-03"], "h10b_nested_GND": ["HIER-03"],
    "h10c_pwrflag_multi": ["HIER-03", "HIER-19"]})
add("h11_decoupling_cap_subsheet", "hierarchy", H, {
    "h11_11a_empty_child_run": ["HIER-10"], "h11_11c_multi_instance_run": ["HIER-15"],
    "h11_11d_refusal_rollback_run": ["HIER-19"]})
add("h12_multichannel_demo", "hierarchy", H, {
    "h12a_root_MIX": ["HIER-05", "HIER-08", "HIER-13"], "h12b_root_GND": ["HIER-08", "HIER-13"],
    "h12c_child_OUT_pin": ["HIER-13", "HIER-16"],
    "h12d_child_unnamed_vbias": ["HIER-13", "HIER-16"]})
add("h13_sheetpin_name_open", "hierarchy", H, same(["h13_plain", "h13_power"], ["HIER-11"]))
add("h14_port_net_already_on_n", "hierarchy", H, same(["h14a_single", "h14b_batch"], ["HIER-07"]))
add("h15_units_across_sheets", "hierarchy", H, same(
    ["15a_root_only_p5V", "15b_both_sheets_p5V", "15c_both_sheets_VDD"], ["HIER-14"]))
add("h16_k10_local_power_decisions", "hierarchy", H, same(
    ["a_root_R1_N1", "b_child_R32_3V3L", "c_child_R31_X"], ["HIER-18"]))

# ------------------------------------------------------------------------------------ naming
N_ = "probe-naming"
add("n01_autolabel_workflow", "naming", N_, same(["v1", "v2", "v3"], ["NAM-01"]))
add("n01b_autolabel_piecewise", "naming", N_, {"n01b": ["NAM-01"]})
add("n01c_name_then_connect", "naming", N_, {"n01c": ["NAM-01"]})
add("n02_autolabel_sheetpin", "naming", N_, {
    "plain": ["NAM-03"], "promoC": ["NAM-04"], "promoW": ["NAM-04"], "gnd": ["NAM-02"]})
add("n02b_wire_sheetpin_chain", "naming", N_, same(["s", "n"], ["NAM-03"]))
add("n03_autolabel_midwire_hier", "naming", N_, {
    "a_exact": ["NAM-19"], "b_1iu": ["NAM-06"], "d_root": ["NAM-19"], "c_kid": ["NAM-19"]})
add("n03b_1iu_label_isolated", "naming", N_, same(["W", "C"], ["NAM-06"]))
add("n04_noop_detached_label", "naming", N_, same(
    ["a_two_labels", "b_pin_end", "c_nc_flag"], ["NAM-05"]))
add("n05_global_labels", "naming", N_, {
    "a_target_global": ["NAM-20"], "b_global_is_N": ["NAM-20"],
    "c_child_global": ["NAM-12"], "d_child_power": ["NAM-12"]})
add("n06_hidden_power_628128", "naming", N_, same(
    ["a", "b", "c", "d1", "d2", "e", "f", "g"], ["NAM-17"]))
add("n06b_hidden_power_demo_files", "naming", N_, same(
    [str(i) for i in range(7)], ["NAM-16", "NAM-17"]))
add("n07_power_value_case", "naming", N_, same(["a1", "a2", "b", "c1", "c2", "d", "e"], ["NAM-18"]))
add("n08_text_variables", "naming", N_, same(["t1", "t2", "t3", "t4", "t5", "t6"], ["NAM-13"]))
add("n09_label_text_validation", "naming", N_, {
    "e": ["NAM-07"], "e3": ["NAM-07"], "n": ["NAM-08"], "u": ["NAM-08"], "v": ["NAM-09"],
    "g": ["NAM-09"], "s": ["NAM-18"]})
add("n10_case_only", "naming", N_, same(["c1", "c2", "c3", "c4"], ["NAM-18"]))
add("n11c_frontpanel_vcc", "naming", N_, same(["P1V8", "VCC"], ["NAM-14"]))
add("n12_label_named_like_sheet_pin", "naming", N_, same(["a", "b"], ["NAM-20"]))
add("n13_transitive_names", "naming", N_, same(["w", "c"], ["NAM-14"]))
add("n14_hidden_pin_alternate", "naming", N_, same(["a", "b"], ["NAM-11"]))
add("n15_autolabel_text_clash", "naming", N_, {"n15": ["NAM-10"]})
add("n16_sheetpin_with_parent_label", "naming", N_, same(["a", "b"], ["NAM-15"]))

# -------------------------------------------------------------------------------- units-pads
U_ = "probe-units-pads"
add("u1_rnet_common_pin", "units-pads", U_, {
    "a": ["UP-01"], "b": ["UP-02"], "c": ["UP-03"], "d": ["UP-04", "UP-14"],
    "e": ["UP-04", "UP-14"]})
add("u1f_nc_on_spare_copy", "units-pads", U_, same(["f1", "f2"], ["UP-06", "UP-13"]))
add("u2_ieee_power_pins", "units-pads", U_, {
    "a": ["UP-01"], "b": ["UP-01"], "c": ["UP-03"], "d": ["UP-02"]})
add("x_duplicate_numbers", "units-pads", U_, {
    "X1a": ["UP-01"], "X1b": ["UP-02"], "X1c": ["UP-01"], "X1d": ["UP-04", "UP-14"],
    "X2a": ["UP-01", "UP-15"], "X2b": ["UP-01", "UP-15"]})
add("c_coincident", "units-pads", U_, {
    "C1": ["UP-17"], "C2": ["UP-17"], "C3": ["UP-17"], "C4": ["UP-08", "UP-11"],
    "C5": ["UP-07"], "C5b": ["UP-07"]})
add("d_demorgan", "units-pads", U_, same(["D1", "D2", "D3", "D4", "D4b"], ["UP-16"]))
add("s_stacked", "units-pads", U_, {
    "S1a": ["UP-17"], "S1b": ["UP-17"], "S1c": ["UP-17"], "S2a": ["UP-17"], "S2b": ["UP-17"],
    "S3a": ["UP-08", "UP-11"], "S3b": ["UP-08"], "S3c": ["UP-11"], "S4a": ["UP-17"],
    "S4b": ["UP-12"]})
add("s5_nc_type_target_stock", "units-pads", U_, same(["S5a", "S5b"], ["UP-08"]))
add("h_units_two_sheets", "units-pads", U_, {"H": ["UP-05"]})
add("h3_units_two_sheets_plus5v", "units-pads", U_, {"H3": ["UP-05"]})
add("r_reused_sheet_units", "units-pads", U_, {"R1": ["UP-10"], "R2": ["UP-09"]})

# Scripts named in a case's scenario_script that make no design tool call (nothing to re-run).
NO_CALL = {
    "BUS-P1": [("c02_shared_entry_shapes.py", "census, no tool call"),
               ("c03_busside_items.py", "census, no tool call")],
    "BUS-M1": [("c01_demo_census.py", "census, no tool call")],
    "BUS-M2": [("s05m_literal_M.py", "model readout of mine_impl, no tool call")],
    "BUS-P5": [("s11_label_on_pin_on_bus.py", "raw label measurement, no design under test")],
    "BUS-P6": [("bl.py (_PText)", "P's touch-rule wrapper, no scenario of its own")],
    "BUS-P7": [("s06e_erc.py", "ERC on P's outputs, no tool call")],
    "BUS-B2": [("s11_label_on_pin_on_bus.py", "raw label measurement, no design under test")],
    "HIER-01": [("h01b_erc_on_short.py", "ERC on P's output, no tool call"),
                ("census_h13_demos.py", "census, no tool call"),
                ("census_h13_nested.py", "census, no tool call")],
    "HIER-02": [("repeat_export.py", "oracle determinism check, no tool call")],
    "HIER-06": [("h04c_model_check.py", "model readout, no tool call"),
                ("census_sheetpin_interior.py", "census, no tool call")],
    "HIER-07": [("census_port_surface.py", "census, no tool call")],
    "HIER-13": [("h12e_junction_readback.py", "readback of P's output, no tool call")],
    "HIER-15": [("h11e_instances_readback.py", "read-back of P/M/B outputs, no tool call; the same"
                 " read-back is recorded here for M2/K as cap_instances")],
    "HIER-19": [("h10d_pwrflag_erc.py", "ERC on P/B outputs, no tool call")],
    "NAM-17": [("n11_census_name_sources.py", "census, no tool call"),
               ("n11b_bare_hide_check.py", "model readout, no tool call")],
    "UP-01": [("erc_on_split.py", "ERC on P/B outputs, no tool call")],
    "UP-12": [("scan_names.py", "library census, no tool call")],
}

# Scenarios a listed script runs that no case of this batch names (not run).
NOT_LISTED = {
    "s03_pin_on_bus": ["s03b"], "s04_member_workflow": ["s04c", "s04e"], "s05_shared_entry_point": [],
    "s06_bus_names": ["s06b"], "h11_decoupling_cap_subsheet": ["11b"],
    "n02_autolabel_sheetpin": ["promoS"], "n03_autolabel_midwire_hier": ["b_1iu_W"],
    "u1_rnet_common_pin": ["b2"], "u2_ieee_power_pins": ["a2"],
}


def cases_for(sid, vid):
    s = SCRIPTS.get(sid)
    if s is None:
        return []
    if vid in s["v"]:
        return s["v"][vid]
    head = str(vid).split(":", 1)[0]
    return s["v"].get(head, [])
