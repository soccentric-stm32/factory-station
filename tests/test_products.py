"""Unit registry, product rules, plan/write service, workspace scope and CLI.

Author: Sandesh Ghimire <sandesh@soccentric.com>
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hwval_factory import cli
from hwval_factory.core import service
from hwval_factory.core.generators import GENERATORS
from hwval_factory.core.rules import SAMPLE, Rules, RulesError
from hwval_factory.core.units import Registry, RegistryError, can_move
from hwval_factory.core.workspace import Workspace, WorkspaceError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def sys_data():
    return service.load("sys", EXAMPLES / "sys.csv")


@pytest.fixture
def rules(tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text(SAMPLE)
    return Rules.load(path)


# ---- registry ---------------------------------------------------------------


def test_status_only_moves_forward():
    assert can_move("generated", "programmed") and can_move("programmed", "verified")
    assert not can_move("verified", "generated")
    assert can_move("shipped", "rma") and can_move("rma", "programmed")
    assert not can_move("scrapped", "generated") and can_move("scrapped", "scrapped")


def test_registry_records_units_and_round_trips(tmp_path):
    gen, data = sys_data()
    built, _ = gen.build_all(data)
    registry = Registry(tmp_path / "units.csv")
    changed = service.register(registry, data, built, product="SC767", batch="2611A")
    assert len(changed) == len(data.ids)
    registry.save()
    again = Registry.load(tmp_path / "units.csv")
    assert set(again.units) == set(registry.units)
    unit = again.units["SC767-2611-00001"]
    assert (unit.id, unit.status, unit.mac) == ("SC0001", "generated", "02:00:00:00:00:01")
    # generating again changes nothing
    assert service.register(again, data, built, product="SC767", batch="2611A") == []


def test_status_change_keeps_status_on_regenerate(tmp_path):
    gen, data = sys_data()
    built, _ = gen.build_all(data)
    registry = Registry(tmp_path / "units.csv")
    service.register(registry, data, built)
    registry.set_status("SC767-2611-00001", "programmed", "bench 3")
    service.register(registry, data, built)
    unit = registry.units["SC767-2611-00001"]
    assert unit.status == "programmed" and unit.note == "bench 3"
    with pytest.raises(RegistryError):
        registry.set_status("SC767-2611-00001", "generated")
    with pytest.raises(RegistryError):
        registry.set_status("NOPE", "verified")


def test_a_serial_or_mac_is_never_reused(tmp_path):
    gen, data = sys_data()
    registry = Registry(tmp_path / "units.csv")
    built, _ = gen.build_all(data)
    service.register(registry, data, built)
    # the same data again is fine
    assert registry.conflicts([data.groups[i][0] for i in data.ids]) == []
    # same serial under another id, and same MAC under another serial
    clash = [{"id": "OTHER", "serial": "SC767-2611-00001", "mac": "02:00:00:00:00:09"},
             {"id": "NEW", "serial": "SC767-2611-99999", "mac": "02-00-00-00-00-02"}]
    messages = [i.message for i in registry.conflicts(clash)]
    assert any("serial SC767-2611-00001 is already unit 'SC0001'" in m for m in messages)
    assert any("MAC 02:00:00:00:00:02 already belongs to serial" in m for m in messages)
    with pytest.raises(RegistryError):
        registry.record_generated(clash[:1])


def test_find_and_next_free(tmp_path):
    gen, data = sys_data()
    registry = Registry(tmp_path / "units.csv")
    service.register(registry, data, gen.build_all(data)[0], batch="2611A")
    assert [u.id for u in registry.find("00003")] == ["SC0003"]
    assert len(registry.find("020000000001")) == 1       # MAC without colons
    assert registry.next_free("SC767-2611-", 5) == "SC767-2611-00013"


def test_registry_rejects_a_bad_file(tmp_path):
    path = tmp_path / "units.csv"
    path.write_text("serial,status\nA,generated\nA,generated\n")
    with pytest.raises(RegistryError, match="duplicate"):
        Registry.load(path)
    path.write_text("serial,status\nA,flying\n")
    with pytest.raises(RegistryError, match="unknown status"):
        Registry.load(path)


# ---- rules --------------------------------------------------------------------


def test_example_data_satisfies_the_sample_rules(rules):
    for kind in ("sys", "cal", "diag"):
        gen, data = service.load(kind, EXAMPLES / f"{kind}.csv")
        assert rules.check(gen, data) == [], kind


def test_rules_catch_what_the_format_allows(tmp_path, rules):
    csv_path = tmp_path / "sys.csv"
    csv_path.write_text(
        "id,serial,product,hw_version,fw_version,batch,mfg_date,mac,notes\n"
        "U1,SC767-2611-00001,OTHER,D,9.9,2611A,2026-10-26,AA:00:00:00:00:01,\n"
        "U2,BADSERIAL,SC767,B,0.1.0,2611A,2026-10-26,02:00:00:00:00:02,\n")
    gen, data = service.load("sys", csv_path)
    messages = [i.message for i in rules.check(gen, data)]
    assert any("product must be 'SC767'" in m for m in messages)
    assert any("hw_version 'D'" in m for m in messages)
    assert any("fw_version '9.9'" in m for m in messages)
    assert any("MAC AA:00:00:00:00:01 does not start with 02:00" in m for m in messages)
    assert any("serial 'BADSERIAL' does not match" in m for m in messages)
    assert all(i.severity == "error" for i in rules.check(gen, data))


def test_cal_rules_check_channels_gain_and_offset(tmp_path, rules):
    csv_path = tmp_path / "cal.csv"
    csv_path.write_text(
        "id,channel,gain,offset,temp_ref,date,operator\n"
        "U1,0,1.5,0,200,2026-01-01,me\nU1,1,1.0,99,,,\n")
    gen, data = service.load("cal", csv_path)
    messages = [i.message for i in rules.check(gen, data)]
    assert any("2 calibration channels, the product has 8" in m for m in messages)
    assert any("channel 0: gain 1.5 is outside 0.9..1.1" in m for m in messages)
    assert any("channel 1: offset 99 is outside +-50" in m for m in messages)
    assert any("temperature reference 200" in m for m in messages)


def test_rules_file_errors(tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text("key,value\nflavour,mint\n")
    with pytest.raises(RulesError, match="unknown rule"):
        Rules.load(path)
    path.write_text("key,value\nserial_pattern,[\n")
    with pytest.raises(RulesError, match="serial_pattern"):
        Rules.load(path)
    path.write_text("key,value\ngain_min,lots\n")
    with pytest.raises(RulesError, match="not a number"):
        Rules.load(path)
    assert not Rules.load(tmp_path / "missing.csv")        # no file = no rules


# ---- plan and write -----------------------------------------------------------


def test_plan_shows_new_changed_and_unchanged(tmp_path):
    gen, data = sys_data()
    first = service.plan(gen, data, tmp_path)
    assert first.summary() == "12 new, 0 changed, 0 unchanged"
    written, problems = service.write(tmp_path, first.to_write(), "sys")
    assert len(written) == 12 and not problems
    assert service.plan(gen, data, tmp_path).summary() == "0 new, 0 changed, 12 unchanged"
    # a different revision changes every file
    assert service.plan(gen, data, tmp_path, revision=2).summary() == "0 new, 12 changed, 0 unchanged"
    # one file edited behind the tool's back is "changed"
    victim = next(iter(first.files))
    (tmp_path / victim).write_bytes(b"junk")
    assert service.plan(gen, data, tmp_path).state[victim] == "changed"
    assert service.plan(gen, data, tmp_path, blocked={first.owner[victim]}).errors


def test_check_adds_rules_and_registry(tmp_path, rules):
    gen, data = sys_data()
    registry = Registry(tmp_path / "units.csv")
    assert service.check(gen, data, rules=rules, registry=registry) == list(
        gen.validate(data))
    registry.record_generated([{"id": "ELSE", "serial": "SC767-2611-00001"}])
    issues = service.check(gen, data, rules=rules, registry=registry)
    assert any("serials are never reused" in i.message for i in issues)


# ---- workspace scope ------------------------------------------------------------


def test_workspace_products_and_batches(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.products() == [] and ws.base == tmp_path / "factory/factory-data-gui"
    made = ws.create_batch("SC767", "2611A")
    assert "products/SC767/rules.csv" in made
    assert ws.base == tmp_path / "factory/factory-data-gui/products/SC767/2611A"
    assert (ws.csv_path_path("sys")).is_file() and ws.out_dir_path.is_dir()
    assert (ws.product, ws.batch) == ("SC767", "2611A")
    assert ws.rules_path.is_file()
    ws.create_batch("SC767", "2612A")
    assert ws.products() == ["SC767"] and ws.batches("SC767") == ["2611A", "2612A"]
    ws.select("", "")
    assert ws.scope == Path() and ws.rules_path.name == "rules.csv"
    assert ws.units_path == tmp_path / "factory/factory-data-gui/units.csv"
    for bad in ("", "..", "a/b", "x y"):
        with pytest.raises(WorkspaceError):
            ws.create_batch("SC767", bad)


# ---- command line ---------------------------------------------------------------


def test_cli_generate_register_verify_inspect(tmp_path, capsys):
    out, units, rules_path = tmp_path / "out", tmp_path / "units.csv", tmp_path / "rules.csv"
    rules_path.write_text(SAMPLE)
    base = ["generate", "sys", str(EXAMPLES / "sys.csv"), "--out", str(out),
            "--units", str(units), "--rules", str(rules_path), "--batch", "2611A"]
    assert cli.main(["check", "sys", str(EXAMPLES / "sys.csv"), "--rules", str(rules_path)]) == 0
    assert cli.main([*base, "--dry-run"]) == 0 and not out.exists()
    assert cli.main(base) == 0
    assert len(list(out.glob("*.sys"))) == 12 and units.is_file()
    capsys.readouterr()
    assert cli.main(base) == 0                               # nothing left to write
    assert "0 new, 0 changed, 12 unchanged" in capsys.readouterr().out
    assert cli.main(["verify", str(out)]) == 0
    assert cli.main(["inspect", str(out / "SC0001.sys")]) == 0
    assert "serial: SC767-2611-00001" in capsys.readouterr().out
    assert cli.main(["units", "--units", str(units), "set-status",
                     "SC767-2611-00001", "programmed", "--note", "bench"]) == 0
    assert cli.main(["units", "--units", str(units), "list", "--status", "programmed"]) == 0
    assert "1 unit(s)" in capsys.readouterr().out
    assert cli.main(["units", "--units", str(units), "set-status",
                     "SC767-2611-00001", "planned"]) == 2   # cannot go back
    (out / "SC0001.sys").write_bytes(b"corrupt")
    assert cli.main(["verify", str(out)]) == 1


def test_cli_refuses_records_that_break_the_rules(tmp_path, capsys):
    csv_path, out = tmp_path / "sys.csv", tmp_path / "out"
    csv_path.write_text(
        "id,serial,product,hw_version,fw_version,batch,mfg_date,mac,notes\n"
        "U1,SC767-2611-00001,SC767,B,0.1.0,2611A,2026-10-26,02:00:00:00:00:01,\n"
        "U2,WRONG,SC767,B,0.1.0,2611A,2026-10-26,02:00:00:00:00:02,\n")
    rules_path = tmp_path / "rules.csv"
    rules_path.write_text(SAMPLE)
    args = ["generate", "sys", str(csv_path), "--out", str(out), "--rules", str(rules_path)]
    assert cli.main(args) == 1 and not out.exists()
    assert cli.main([*args, "--skip-errors"]) == 0
    assert [p.name for p in out.glob("*.sys")] == ["U1.sys"]


def test_cli_capi_and_new_batch(tmp_path, capsys):
    assert cli.main(["capi", str(tmp_path / "c")]) == 0
    assert (tmp_path / "c" / "factory_data.h").is_file()
    assert cli.main(["new-batch", str(tmp_path / "repo"), "SC767", "2611A"]) == 0
    assert (tmp_path / "repo/factory/factory-data-gui/products/SC767/rules.csv").is_file()
    assert cli.main(["new-batch", str(tmp_path / "repo"), "SC767", "../x"]) == 2
    assert cli.main(["units", "--units", str(tmp_path / "u.csv"), "next", "SC-", "--digits", "3"]) == 0
    assert capsys.readouterr().out.strip().endswith("SC-001")


def test_generators_are_unchanged_by_the_service():
    assert set(GENERATORS) == {"sys", "cal", "diag"}
