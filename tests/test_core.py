"""Tests for the file format and the CSV generators (no Qt needed)."""

from __future__ import annotations

import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hwval_factory.core import fileformat as ff
from hwval_factory.core import spec
from hwval_factory.core.generators import GENERATORS, CsvError
from hwval_factory.core.manifest import MANIFEST_NAME, manifest_csv, verify_folder

SYS, CAL, DIAG = GENERATORS["sys"], GENERATORS["cal"], GENERATORS["diag"]


def load(tmp_path, gen, text=None):
    path = tmp_path / f"{gen.kind}.csv"
    path.write_text(gen.sample() if text is None else text, encoding="utf-8")
    return gen.load(path)


def test_header_layout_and_crcs(tmp_path):
    data = load(tmp_path, SYS)
    blob = SYS.build(data, "SC0001", revision=7)
    magic, fmt, revision, length, payload_crc, uid, header_crc = spec.FILE_HEADER.unpack_from(blob)
    assert magic == spec.MAGIC_SYS and fmt == 1 and revision == 7
    assert length == len(blob) - 32 and uid == bytes(12)
    assert payload_crc == ff.crc32(blob[32:]) and header_crc == ff.crc32(blob[:28])


def test_sys_round_trip(tmp_path):
    parsed = ff.parse_file(SYS.build(load(tmp_path, SYS), "SC0003"), "sys")
    fields = dict(ff.describe(parsed))
    assert fields["serial"] == "SC767-2611-00003"
    assert fields["mac"] == "02:00:00:00:00:03"
    assert fields["mfg_date"] == "2026-10-26"
    assert fields["notes"] == "Rework: U12 replaced"


def test_cal_round_trip_and_channels(tmp_path):
    parsed = ff.parse_file(CAL.build(load(tmp_path, CAL), "SC0002"), "cal")
    fields = dict(ff.describe(parsed))
    assert fields["channel 0"] == "gain 0.99958, offset -0.401"
    assert fields["temperature reference"] == "25 °C"
    assert fields["operator"] == "sandesh"
    channels = [struct.unpack("<Bff", v) for t, v in parsed.items if t == spec.CAL_CHANNEL]
    assert [c[0] for c in channels] == list(range(8))


def test_diag_shared_rows_merge_into_every_unit(tmp_path):
    data = load(tmp_path, DIAG)
    assert data.ids == [f"SC{n:04d}" for n in range(1, 13)]
    plain = dict(ff.describe(ff.parse_file(DIAG.build(data, "SC0003"), "diag")))
    assert "fault 0x0001" in plain and plain["counter 7"] == "initial 1"
    assert plain["log"] == "depth 64, flags 0x0000"
    logged = dict(ff.describe(ff.parse_file(DIAG.build(data, "SC0009"), "diag")))
    assert logged["log"] == "depth 128, flags 0x0001"
    assert "fault 0x0070" in dict(ff.describe(ff.parse_file(DIAG.build(data, "SC0012"), "diag")))


def test_uid_from_argument_and_from_csv(tmp_path):
    uid = bytes(range(1, 13))
    data = load(tmp_path, SYS)
    assert ff.parse_file(SYS.build(data, "SC0001", uid=uid)).uid == uid
    text = SYS.sample().replace("notes", "notes,uid").replace(
        "02:00:00:00:00:01,", f"02:00:00:00:00:01,,{uid.hex()}", 1)
    bound = load(tmp_path, SYS, text)
    assert ff.parse_file(SYS.build(bound, "SC0001")).uid == uid
    assert not ff.parse_file(SYS.build(bound, "SC0002")).bound


def test_parse_uid_variants():
    raw = bytes(range(12))
    assert ff.parse_uid(raw.hex()) == raw
    assert ff.parse_uid("0x" + raw.hex().upper()) == raw
    assert ff.parse_uid(":".join(f"{b:02x}" for b in raw)) == raw
    assert ff.parse_uid("") == bytes(12)
    with pytest.raises(ff.FileFormatError):
        ff.parse_uid("abc")


def test_corruption_is_detected(tmp_path):
    blob = bytearray(SYS.build(load(tmp_path, SYS), "SC0001"))
    blob[-1] ^= 0xFF
    with pytest.raises(ff.FileFormatError, match="payload CRC"):
        ff.parse_file(bytes(blob))
    with pytest.raises(ff.FileFormatError, match="expected a .cal"):
        ff.parse_file(SYS.build(load(tmp_path, SYS), "SC0001"), "cal")


@pytest.mark.parametrize(
    ("gen", "text", "message"),
    [
        (SYS, "id,serial\nA,1\nA,2\n", "duplicate id"),
        (SYS, "id,product\nA,x\n", "missing columns: serial"),
        (SYS, "id,serial\n,1\n", "id is empty"),
        (CAL, "id,channel,gain,offset\nA,0,1,0\nA,0,2,0\n", "appears twice"),
        (CAL, "id,channel,gain,offset\nA,300,1,0\n", "out of range"),
        (CAL, "id,channel,gain,offset\nA,0,abc,0\n", "not a number"),
        (DIAG, "id,type,code\nA,bogus,1\n", "not fault, counter or log"),
        (DIAG, "id,type,code,severity\nA,fault,70000,1\n", "out of range"),
    ],
)
def test_bad_csv_is_reported(tmp_path, gen, text, message):
    path = tmp_path / "bad.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CsvError, match=message):
        gen.build(gen.load(path), "A")


def test_comments_and_blank_lines_are_skipped(tmp_path):
    data = load(tmp_path, SYS, "# units\n\nid,serial\nA,1\n# done\n")
    assert data.ids == ["A"]


def test_non_ascii_text_is_rejected(tmp_path):
    data = load(tmp_path, SYS, "id,serial,notes\nA,1,café\n")
    with pytest.raises(CsvError, match="must be ASCII"):
        SYS.build(data, "A")


def test_oversized_file_is_rejected(tmp_path):
    rows = "".join(f"A,{n},1,0\n" for n in range(256))
    data = load(tmp_path, CAL, f"id,channel,gain,offset\n{rows}")
    assert len(CAL.build(data, "A")) < spec.MAX_FILE_SIZE
    notes = "x" * 4100
    big = load(tmp_path, SYS, f"id,serial,notes\nA,1,{notes}\n")
    with pytest.raises(CsvError, match="limit is 4096"):
        SYS.build(big, "A")


def test_filename_is_sanitised():
    assert SYS.filename("SC 0001/x") == "SC_0001_x.sys"
    assert DIAG.filename("../..") == "file.diag"


def test_files_match_the_factory_station_format(tmp_path):
    """The factory station's own parser must accept what this tool writes."""
    station = pathlib.Path(__file__).resolve().parents[2] / "factory-station" / "src"
    if not station.is_dir():
        pytest.skip("factory-station checkout not next to this repository")
    sys.path.insert(0, str(station))
    from hwval_factory.bench import files as station_files

    for gen in (SYS, CAL, DIAG):
        data = load(tmp_path, gen)
        for ident in data.ids:
            parsed = station_files.parse_file(gen.build(data, ident), gen.kind)
            assert parsed.kind == gen.kind


# -- line numbers and validation ----------------------------------------------


def test_line_numbers_count_comments_and_blank_lines(tmp_path):
    data = load(tmp_path, SYS, "# units\n\nid,serial\nA,1\n# skipped\nB,2\n")
    assert data.lines == [4, 6]
    assert data.line_of("B") == 6


def test_load_errors_name_the_file_line(tmp_path):
    with pytest.raises(CsvError, match=r"line 5: the id is empty"):
        load(tmp_path, SYS, "# c\nid,serial\nA,1\n\n,2\n")
    with pytest.raises(CsvError, match=r"line 4: duplicate id 'A' \(first on line 3\)"):
        load(tmp_path, SYS, "id,serial\n\nA,1\nA,2\n")
    with pytest.raises(CsvError, match="shared rows"):
        load(tmp_path, SYS, "id,serial\n*,1\n")


def test_validate_reports_each_bad_record_with_its_line(tmp_path):
    data = load(tmp_path, CAL, "id,channel,gain,offset,temp_ref,date,operator\n"
        "A,0,1,0,25,2026-01-01,x\nB,0,abc,0,25,2026-01-01,x\nC,0,nan,0,25,2026-01-01,x\n")
    issues = CAL.validate(data)
    errors = {(i.ident, i.line): i.message for i in issues if i.severity == "error"}
    assert "not a number" in errors[("B", 3)]
    assert "finite" in errors[("C", 4)]
    assert not any(i.ident == "A" for i in issues)


def test_validate_catches_file_name_collisions(tmp_path):
    data = load(tmp_path, SYS, "id,serial\nA/B,1\nA_B,2\n")
    issues = SYS.validate(data)
    assert any(i.severity == "error" and i.ident == "A_B" and "also used" in i.message for i in issues)


def test_validate_warns_about_duplicates_and_multicast(tmp_path):
    data = load(
        tmp_path, SYS,
        "id,serial,mac\nA,1,02:00:00:00:00:01\nB,1,02:00:00:00:00:01\nC,3,01:00:00:00:00:02\n",
    )
    text = [i.message for i in SYS.validate(data) if i.severity == "warning"]
    assert any("serial number 1 is also used by 'A'" in t for t in text)
    assert any("MAC address" in t and "'A'" in t for t in text)
    assert any("multicast" in t for t in text)


def test_validate_diag_duplicates_and_unknown_columns(tmp_path):
    text = ("id,type,code,severity,counter,extra\nA,fault,1,1,,x\nA,fault,1,2,,x\n"
            "A,counter,,,5,x\nA,counter,,,5,x\n")
    issues = DIAG.validate(load(tmp_path, DIAG, text))
    messages = [i.message for i in issues]
    assert "unknown columns are ignored: extra" in messages
    assert "fault 0x0001 appears twice" in messages
    assert "counter 5 appears twice" in messages
    assert not any(i.severity == "error" for i in issues)


def test_validate_flags_a_file_near_the_size_limit(tmp_path):
    issues = SYS.validate(load(tmp_path, SYS, f"id,serial,notes\nA,1,{'x' * 3700}\n"))
    assert any(i.severity == "warning" and "close to the limit" in i.message for i in issues)


def test_clean_sample_has_no_issues(tmp_path):
    for gen in (SYS, CAL, DIAG):
        assert gen.validate(load(tmp_path, gen)) == []


def test_build_all_separates_files_from_errors(tmp_path):
    data = load(tmp_path, CAL, "id,channel,gain,offset\nA,0,1,0\nB,0,x,0\n")
    files, errors = CAL.build_all(data)
    assert list(files) == ["A"] and "not a number" in errors["B"]


# -- layout, hex view, manifest -------------------------------------------------


def test_layout_covers_the_whole_file(tmp_path):
    blob = SYS.build(load(tmp_path, SYS), "SC0001")
    rows = ff.layout(blob)
    assert rows[0][:3] == (0, 4, "magic") and rows[6][:3] == (28, 4, "header CRC-32")
    end = max(offset + size for offset, size, _, _ in rows)
    assert end == len(blob)
    assert any(name == "TLV 0x01 serial" for _, _, name, _ in rows)


def test_hexdump_format():
    text = ff.hexdump(bytes(range(0x30, 0x42)))
    first, second = text.splitlines()
    assert first.startswith("0000  30 31 32") and first.endswith("0123456789:;<=>?")
    assert second.startswith("0010  40 41") and second.endswith("@A")


def test_manifest_and_verify_folder(tmp_path):
    data = load(tmp_path, SYS)
    files = {SYS.filename(i): SYS.build(data, i) for i in data.ids}
    out = tmp_path / "out"
    out.mkdir()
    for name, blob in files.items():
        (out / name).write_bytes(blob)
    (out / MANIFEST_NAME).write_text(manifest_csv(files))
    assert manifest_csv(files).splitlines()[0] == "file,kind,revision,uid,size,crc32,sha256"
    assert all(v.ok for v in verify_folder(out))

    changed = out / "SC0001.sys"
    changed.write_bytes(SYS.build(data, "SC0001", revision=9))
    (out / "SC0002.sys").unlink()
    (out / "extra.cal").write_bytes(b"junk")
    verdicts = {v.name: v for v in verify_folder(out)}
    assert "changed" in verdicts["SC0001.sys"].detail
    assert "missing" in verdicts["SC0002.sys"].detail
    assert not verdicts["extra.cal"].ok


# -- validation added for realistic data ---------------------------------------

def messages(gen, tmp_path, text):
    return [i.message for i in gen.validate(load(tmp_path, gen, text)) if i.severity == "warning"]


def test_cal_warns_about_implausible_values(tmp_path):
    text = (
        "id,channel,gain,offset,temp_ref,date,operator\n"
        "A,0,1.5,80,200,2026-02-30,\n"
        "A,2,1,0,,,\n"
    )
    warned = " | ".join(messages(CAL, tmp_path, text))
    for expected in ("gain 1.5", "offset 80", "temp_ref 200", "not a calendar date",
                     "operator is empty", "gaps"):
        assert expected in warned


def test_cal_warns_when_channel_counts_differ(tmp_path):
    base = "id,channel,gain,offset,temp_ref,date,operator\n"
    rows = "".join(f"{u},{c},1,0,25,2026-01-01,x\n" for u in "AB" for c in range(4))
    rows += "C,0,1,0,25,2026-01-01,x\n"
    assert any("1 channels, most units have 4" in m for m in messages(CAL, tmp_path, base + rows))


def test_diag_warns_about_bad_severity_flags_and_log_depth(tmp_path):
    text = (
        "id,type,code,severity,flags,text,counter,initial,depth,log_flags\n"
        "A,fault,1,9,0x10,,,,,\n"
        "A,log,,,,,,,100,0\n"
    )
    warned = " | ".join(messages(DIAG, tmp_path, text))
    for expected in ("severity is above 3", "flag bits", "has no text", "power of two"):
        assert expected in warned


def test_sys_warns_about_dates_and_empty_fields(tmp_path):
    text = "id,serial,mac,mfg_date\nA,1,00:00:00:00:00:00,2999-01-01\n"
    warned = " | ".join(messages(SYS, tmp_path, text))
    assert "all zeros" in warned and "in the future" in warned and "product is empty" in warned


# -- batch generator ------------------------------------------------------------

def test_batch_counts_ids_serials_and_macs(tmp_path):
    from hwval_factory.core import batch
    rows = batch.sys_batch(
        count=3, first_id="SC0099", first_serial="SC767-2611-00099", product="P",
        hw_version="B", fw_version="1", batch="b", mfg_date="2026-01-01",
        first_mac="02:00:00:00:00:FF",
    )
    assert [r["id"] for r in rows] == ["SC0099", "SC0100", "SC0101"]
    assert rows[2]["serial"] == "SC767-2611-00101"
    assert [r["mac"] for r in rows] == ["02:00:00:00:00:FF", "02:00:00:00:01:00", "02:00:00:00:01:01"]


def test_batch_append_refuses_duplicates_and_keeps_columns(tmp_path):
    from hwval_factory.core import batch
    path = tmp_path / "sys.csv"
    args = dict(first_serial="S1", product="P", hw_version="B", fw_version="1",
                batch="b", mfg_date="2026-01-01", first_mac="02:00:00:00:00:01")
    assert batch.append_rows(path, batch.sys_batch(count=2, first_id="U1", **args)) == 2
    with pytest.raises(CsvError, match="already in"):
        batch.append_rows(path, batch.sys_batch(count=2, first_id="U2", **args))
    batch.append_rows(path, batch.sys_batch(count=2, first_id="U3", **args))
    data = load(tmp_path, SYS, path.read_text())
    assert data.ids == ["U1", "U2", "U3", "U4"]
    with pytest.raises(CsvError):
        batch.sys_batch(count=1, first_id="U1", **{**args, "mfg_date": "nope"})
