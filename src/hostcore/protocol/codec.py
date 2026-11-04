"""FlatBuffers message builders and parsers for HWLINK (Appendix A.3).

All wire encoding is done by code generated from ``protocol/hwlink.fbs``
(GCON-004); this module only assembles generated builders and reads generated
tables.
"""

from __future__ import annotations

from typing import Any

import flatbuffers

# Table/enum modules are imported as modules (flatc puts the builders at
# module level); the enum and Body classes are imported explicitly because
# their constants live on the class.
from hostcore.protocol.generated.hwlink import (
    Abort,
    Ack,
    Activate,
    BootInfo,
    BootSlotInfo,
    ButtonEvent,
    ClearFault,
    ClearResults,
    DeviceInfo,
    EnterBootloader,
    EnterTextMode,
    FaultInfo,
    GetBootInfo,
    GetDeviceInfo,
    GetHarness,
    GetReport,
    GetResults,
    GetSetting,
    GetStatus,
    GroupInfo,
    GroupList,
    HarnessInfo,
    HarnessWire,
    Hello,
    HelloReply,
    ImageHeader,
    LedState,
    Limits,
    ListGroups,
    ListTests,
    Log,
    Measurement,
    Message,
    Nak,
    OperatorAnswer,
    OperatorPrompt,
    OperatorPromptDone,
    Ping,
    Pong,
    Progress,
    Report,
    Reset,
    ResultList,
    Run,
    RunFinished,
    RunStarted,
    SetBaud,
    SetLed,
    SetLedAll,
    SetSetting,
    Setting,
    Status,
    TaskStats,
    TestInfo,
    TestList,
    TestResult,
    TestStarted,
    UpdateAbort,
    UpdateBegin,
    UpdateData,
    UpdateEnd,
    UpdateResult,
)
from hostcore.protocol.generated.hwlink.Body import Body
from hostcore.protocol.generated.hwlink.BootOutcome import BootOutcome
from hostcore.protocol.generated.hwlink.ButtonPress import ButtonPress
from hostcore.protocol.generated.hwlink.FlashMode import FlashMode
from hostcore.protocol.generated.hwlink.HealthState import HealthState
from hostcore.protocol.generated.hwlink.LedId import LedId
from hostcore.protocol.generated.hwlink.LedPattern import LedPattern
from hostcore.protocol.generated.hwlink.LogLevel import LogLevel
from hostcore.protocol.generated.hwlink.PromptKind import PromptKind
from hostcore.protocol.generated.hwlink.Role import Role
from hostcore.protocol.generated.hwlink.Slot import Slot
from hostcore.protocol.generated.hwlink.TestLevel import TestLevel
from hostcore.protocol.generated.hwlink.TestVerdict import TestVerdict
from hostcore.protocol.generated.hwlink.TrialState import TrialState
from hostcore.protocol.link import ProtocolError, error_name

ROLE_NAMES = {Role.HWVAL: "HWVAL", Role.BOOTLOADER: "BOOTLOADER", Role.DEMO: "DEMO"}
VERDICT_NAMES = {
    TestVerdict.PASS: "PASS",
    TestVerdict.FAIL: "FAIL",
    TestVerdict.SKIP: "SKIP",
    TestVerdict.NA: "N/A",
}


def verdict_text(row: dict[str, Any]) -> str:
    """Return the verdict of a result row as text.

    Device rows carry the enum in ``result`` (an int, where PASS is 0 and
    therefore falsy) and the decoded name in ``result_name``; placeholders
    and history rows use strings such as ``NOT RUN``. Prefer the name so a
    PASS is not mistaken for "not run" and a FAIL is not shown as "1".
    """
    name = row.get("result_name")
    if not name:
        raw = row.get("result")
        if isinstance(raw, int) and not isinstance(raw, bool):
            name = VERDICT_NAMES.get(raw)
        elif raw is not None:
            name = str(raw)
    text = str(name or "NOT RUN").strip().upper()
    return "N/A" if text == "NA" else text
LEVEL_NAMES = {TestLevel.S: "S", TestLevel.L: "L", TestLevel.E: "E"}
PROMPT_KIND_NAMES = {
    PromptKind.YES_NO: "YES_NO",
    PromptKind.LINE: "LINE",
    PromptKind.BUTTON: "BUTTON",
    PromptKind.EQUIPMENT: "EQUIPMENT",
    PromptKind.DESTRUCTIVE: "DESTRUCTIVE",
}
SLOT_NAMES = {Slot.UNKNOWN: "—", Slot.A: "A", Slot.B: "B"}
OUTCOME_NAMES = {
    BootOutcome.UNKNOWN: "UNKNOWN",
    BootOutcome.BOOTED: "BOOTED",
    BootOutcome.ROLLED_BACK: "ROLLED_BACK",
    BootOutcome.UPDATE_MODE: "UPDATE_MODE",
}
FLASH_MODE_NAMES = {
    FlashMode.UNKNOWN: "UNKNOWN",
    FlashMode.SINGLE_BANK: "SINGLE_BANK",
    FlashMode.DUAL_BANK: "DUAL_BANK",
}
TRIAL_NAMES = {
    TrialState.UNKNOWN: "UNKNOWN",
    TrialState.CONFIRMED: "CONFIRMED",
    TrialState.PENDING: "PENDING",
}
HEALTH_NAMES = {
    HealthState.NOT_REQUIRED: "NOT_REQUIRED",
    HealthState.RUNNING: "RUNNING",
    HealthState.PASSED: "PASSED",
    HealthState.FAILED: "FAILED",
}
LED_PATTERN_NAMES = {
    LedPattern.OFF: "OFF",
    LedPattern.ON: "ON",
    LedPattern.BLINK: "BLINK",
    LedPattern.HEARTBEAT: "HEARTBEAT",
    LedPattern.FAST: "FAST",
    LedPattern.CHASE: "CHASE",
    LedPattern.MORSE_SOS: "MORSE_SOS",
}
LED_ID_NAMES = {LedId.LD1: "LD1", LedId.LD2: "LD2", LedId.LD3: "LD3"}
LOG_LEVEL_NAMES = {
    LogLevel.DEBUG: "DEBUG",
    LogLevel.INFO: "INFO",
    LogLevel.WARN: "WARN",
    LogLevel.ERROR: "ERROR",
}


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").rstrip("\x00")
    return str(value)


def _finish(builder: flatbuffers.Builder, body_type: int, body: Any) -> bytes:
    Message.MessageStart(builder)
    Message.MessageAddBodyType(builder, body_type)
    Message.MessageAddBody(builder, body)
    message = Message.MessageEnd(builder)
    builder.Finish(message, file_identifier=b"HWLK")
    return bytes(builder.Output())


# --------------------------------------------------------------------------
# Verification / generic access


def verify(payload: bytes) -> bool:
    """Minimal verifier for an inbound FlatBuffer (LNK-005)."""
    try:
        if len(payload) < 8:
            return False
        if not Message.Message.MessageBufferHasIdentifier(payload, 0):
            return False
        message = Message.Message.GetRootAs(payload, 0)
        body_type = message.BodyType()
        if body_type <= Body.NONE or body_type > Body.RunFinished:
            return False
        return message.Body() is not None
    except Exception:
        return False


def body_type(payload: bytes) -> int:
    if not verify(payload):
        raise ProtocolError("malformed FlatBuffer")
    return int(Message.Message.GetRootAs(payload, 0).BodyType())


def body_table(message: Message.Message) -> Any:
    body = message.Body()
    if body is None:
        raise ProtocolError("message has no body")
    body_type = message.BodyType()
    classes = {
        Body.Hello: Hello.Hello,
        Body.Ping: Ping.Ping,
        Body.SetBaud: SetBaud.SetBaud,
        Body.Reset: Reset.Reset,
        Body.GetBootInfo: GetBootInfo.GetBootInfo,
        Body.UpdateBegin: UpdateBegin.UpdateBegin,
        Body.UpdateData: UpdateData.UpdateData,
        Body.UpdateEnd: UpdateEnd.UpdateEnd,
        Body.Activate: Activate.Activate,
        Body.UpdateAbort: UpdateAbort.UpdateAbort,
        Body.SetLed: SetLed.SetLed,
        Body.SetLedAll: SetLedAll.SetLedAll,
        Body.ClearFault: ClearFault.ClearFault,
        Body.EnterBootloader: EnterBootloader.EnterBootloader,
        Body.GetStatus: GetStatus.GetStatus,
        Body.GetDeviceInfo: GetDeviceInfo.GetDeviceInfo,
        Body.ListGroups: ListGroups.ListGroups,
        Body.ListTests: ListTests.ListTests,
        Body.GetHarness: GetHarness.GetHarness,
        Body.Run: Run.Run,
        Body.OperatorAnswer: OperatorAnswer.OperatorAnswer,
        Body.Abort: Abort.Abort,
        Body.GetResults: GetResults.GetResults,
        Body.ClearResults: ClearResults.ClearResults,
        Body.GetReport: GetReport.GetReport,
        Body.GetSetting: GetSetting.GetSetting,
        Body.SetSetting: SetSetting.SetSetting,
        Body.EnterTextMode: EnterTextMode.EnterTextMode,
        Body.HelloReply: HelloReply.HelloReply,
        Body.Pong: Pong.Pong,
        Body.Ack: Ack.Ack,
        Body.Nak: Nak.Nak,
        Body.Log: Log.Log,
        Body.Progress: Progress.Progress,
        Body.BootInfo: BootInfo.BootInfo,
        Body.UpdateResult: UpdateResult.UpdateResult,
        Body.Status: Status.Status,
        Body.ButtonEvent: ButtonEvent.ButtonEvent,
        Body.DeviceInfo: DeviceInfo.DeviceInfo,
        Body.GroupInfo: GroupInfo.GroupInfo,
        Body.GroupList: GroupList.GroupList,
        Body.TestInfo: TestInfo.TestInfo,
        Body.TestList: TestList.TestList,
        Body.HarnessWire: HarnessWire.HarnessWire,
        Body.HarnessInfo: HarnessInfo.HarnessInfo,
        Body.TestResult: TestResult.TestResult,
        Body.ResultList: ResultList.ResultList,
        Body.Report: Report.Report,
        Body.Setting: Setting.Setting,
        Body.RunStarted: RunStarted.RunStarted,
        Body.TestStarted: TestStarted.TestStarted,
        Body.OperatorPrompt: OperatorPrompt.OperatorPrompt,
        Body.OperatorPromptDone: OperatorPromptDone.OperatorPromptDone,
        Body.RunFinished: RunFinished.RunFinished,
    }
    cls = classes.get(body_type)
    if cls is None:
        raise ProtocolError(f"unsupported body type {body_type}")
    table = cls()
    table.Init(body.Bytes, body.Pos)
    return table


def body_table_from_payload(payload: bytes) -> Any:
    if not verify(payload):
        raise ProtocolError("malformed FlatBuffer")
    return body_table(Message.Message.GetRootAs(payload, 0))


# --------------------------------------------------------------------------
# Common builders


def build_hello(host_name: str, host_version: str, protocol_minor: int) -> bytes:
    b = flatbuffers.Builder(256)
    name = b.CreateString(host_name)
    version = b.CreateString(host_version)
    Hello.HelloStart(b)
    Hello.HelloAddHostName(b, name)
    Hello.HelloAddHostVersion(b, version)
    Hello.HelloAddProtocolMinor(b, protocol_minor)
    return _finish(b, Body.Hello, Hello.HelloEnd(b))


def build_ping(host_time_ms: int = 0) -> bytes:
    b = flatbuffers.Builder(64)
    Ping.PingStart(b)
    Ping.PingAddHostTimeMs(b, host_time_ms)
    return _finish(b, Body.Ping, Ping.PingEnd(b))


def build_set_baud(rate: int) -> bytes:
    b = flatbuffers.Builder(64)
    SetBaud.SetBaudStart(b)
    SetBaud.SetBaudAddRate(b, rate)
    return _finish(b, Body.SetBaud, SetBaud.SetBaudEnd(b))


def build_reset() -> bytes:
    b = flatbuffers.Builder(32)
    Reset.ResetStart(b)
    return _finish(b, Body.Reset, Reset.ResetEnd(b))


def build_enter_text_mode() -> bytes:
    b = flatbuffers.Builder(32)
    EnterTextMode.EnterTextModeStart(b)
    return _finish(b, Body.EnterTextMode, EnterTextMode.EnterTextModeEnd(b))


def build_ack(seq: int, next_offset: int = 0) -> bytes:
    b = flatbuffers.Builder(48)
    Ack.AckStart(b)
    Ack.AckAddSeq(b, seq)
    Ack.AckAddNextOffset(b, next_offset)
    return _finish(b, Body.Ack, Ack.AckEnd(b))


def build_nak(seq: int, error: int, detail: str, expected_offset: int = 0) -> bytes:
    b = flatbuffers.Builder(256)
    detail_s = b.CreateString(detail)
    Nak.NakStart(b)
    Nak.NakAddSeq(b, seq)
    Nak.NakAddError(b, error)
    Nak.NakAddDetail(b, detail_s)
    Nak.NakAddExpectedOffset(b, expected_offset)
    return _finish(b, Body.Nak, Nak.NakEnd(b))


def build_log(level: int, text_value: str) -> bytes:
    b = flatbuffers.Builder(256)
    string = b.CreateString(text_value)
    Log.LogStart(b)
    Log.LogAddLevel(b, level)
    Log.LogAddText(b, string)
    return _finish(b, Body.Log, Log.LogEnd(b))


def build_progress(operation: str, done: int, total: int) -> bytes:
    b = flatbuffers.Builder(256)
    op = b.CreateString(operation)
    Progress.ProgressStart(b)
    Progress.ProgressAddOperation(b, op)
    Progress.ProgressAddDone(b, done)
    Progress.ProgressAddTotal(b, total)
    return _finish(b, Body.Progress, Progress.ProgressEnd(b))


# --------------------------------------------------------------------------
# HWVAL builders


def build_get_device_info() -> bytes:
    b = flatbuffers.Builder(32)
    GetDeviceInfo.GetDeviceInfoStart(b)
    return _finish(b, Body.GetDeviceInfo, GetDeviceInfo.GetDeviceInfoEnd(b))


def build_list_groups(page: int = 0) -> bytes:
    b = flatbuffers.Builder(64)
    ListGroups.ListGroupsStart(b)
    ListGroups.ListGroupsAddPage(b, page)
    return _finish(b, Body.ListGroups, ListGroups.ListGroupsEnd(b))


def build_list_tests(group: str = "", page: int = 0) -> bytes:
    b = flatbuffers.Builder(128)
    group_s = b.CreateString(group)
    ListTests.ListTestsStart(b)
    ListTests.ListTestsAddGroup(b, group_s)
    ListTests.ListTestsAddPage(b, page)
    return _finish(b, Body.ListTests, ListTests.ListTestsEnd(b))


def build_get_harness(page: int = 0) -> bytes:
    b = flatbuffers.Builder(64)
    GetHarness.GetHarnessStart(b)
    GetHarness.GetHarnessAddPage(b, page)
    return _finish(b, Body.GetHarness, GetHarness.GetHarnessEnd(b))


def _string_vector(b: flatbuffers.Builder, values: list[str]) -> int:
    offsets = [b.CreateString(value) for value in values]
    Run.RunStartIdsVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    return b.EndVector()


def build_run(
    *,
    ids: list[str] | None = None,
    groups: list[str] | None = None,
    all_tests: bool = False,
    levels: str = "S,L,E",
    allow_operator: bool = False,
    allow_destructive: bool = False,
    repeat: int = 1,
    until_fail: bool = False,
) -> bytes:
    b = flatbuffers.Builder(512)
    ids_offsets = [b.CreateString(value) for value in (ids or [])]
    groups_offsets = [b.CreateString(value) for value in (groups or [])]
    levels_s = b.CreateString(levels)
    Run.RunStartIdsVector(b, len(ids_offsets))
    for offset in reversed(ids_offsets):
        b.PrependUOffsetTRelative(offset)
    ids_vec = b.EndVector()
    Run.RunStartGroupsVector(b, len(groups_offsets))
    for offset in reversed(groups_offsets):
        b.PrependUOffsetTRelative(offset)
    groups_vec = b.EndVector()
    Run.RunStart(b)
    Run.RunAddIds(b, ids_vec)
    Run.RunAddGroups(b, groups_vec)
    Run.RunAddAll(b, all_tests)
    Run.RunAddLevels(b, levels_s)
    Run.RunAddAllowOperator(b, allow_operator)
    Run.RunAddAllowDestructive(b, allow_destructive)
    Run.RunAddRepeat(b, max(1, repeat))
    Run.RunAddUntilFail(b, until_fail)
    return _finish(b, Body.Run, Run.RunEnd(b))


def build_operator_answer(prompt_id: int, answer: str) -> bytes:
    b = flatbuffers.Builder(128)
    answer_s = b.CreateString(answer)
    OperatorAnswer.OperatorAnswerStart(b)
    OperatorAnswer.OperatorAnswerAddPromptId(b, prompt_id)
    OperatorAnswer.OperatorAnswerAddAnswer(b, answer_s)
    return _finish(b, Body.OperatorAnswer, OperatorAnswer.OperatorAnswerEnd(b))


def build_abort() -> bytes:
    b = flatbuffers.Builder(32)
    Abort.AbortStart(b)
    return _finish(b, Body.Abort, Abort.AbortEnd(b))


def build_get_results(page: int = 0) -> bytes:
    b = flatbuffers.Builder(64)
    GetResults.GetResultsStart(b)
    GetResults.GetResultsAddPage(b, page)
    return _finish(b, Body.GetResults, GetResults.GetResultsEnd(b))


def build_clear_results() -> bytes:
    b = flatbuffers.Builder(32)
    ClearResults.ClearResultsStart(b)
    return _finish(b, Body.ClearResults, ClearResults.ClearResultsEnd(b))


def build_get_report() -> bytes:
    b = flatbuffers.Builder(32)
    GetReport.GetReportStart(b)
    return _finish(b, Body.GetReport, GetReport.GetReportEnd(b))


def build_get_setting(key: str) -> bytes:
    b = flatbuffers.Builder(128)
    key_s = b.CreateString(key)
    GetSetting.GetSettingStart(b)
    GetSetting.GetSettingAddKey(b, key_s)
    return _finish(b, Body.GetSetting, GetSetting.GetSettingEnd(b))


def build_set_setting(key: str, value: str) -> bytes:
    b = flatbuffers.Builder(192)
    key_s = b.CreateString(key)
    value_s = b.CreateString(value)
    SetSetting.SetSettingStart(b)
    SetSetting.SetSettingAddKey(b, key_s)
    SetSetting.SetSettingAddValue(b, value_s)
    return _finish(b, Body.SetSetting, SetSetting.SetSettingEnd(b))


# --------------------------------------------------------------------------
# BOOT builders


def build_get_boot_info() -> bytes:
    b = flatbuffers.Builder(32)
    GetBootInfo.GetBootInfoStart(b)
    return _finish(b, Body.GetBootInfo, GetBootInfo.GetBootInfoEnd(b))


def _build_image_header(b: flatbuffers.Builder, header: dict[str, Any]) -> int:
    git = bytes(header.get("git_commit") or b"")
    git_vec = b.CreateByteVector(git)
    name = b.CreateString(str(header.get("name", "")))
    ImageHeader.ImageHeaderStart(b)
    ImageHeader.ImageHeaderAddMagic(b, int(header.get("magic", 0x48574249)))
    ImageHeader.ImageHeaderAddHeaderVersion(b, int(header.get("header_version", 1)))
    ImageHeader.ImageHeaderAddHeaderSize(b, int(header.get("header_size", 256)))
    ImageHeader.ImageHeaderAddImageType(b, int(header.get("image_type", 1)))
    ImageHeader.ImageHeaderAddVersion(b, int(header.get("version", 0)))
    ImageHeader.ImageHeaderAddImageSize(b, int(header.get("image_size", 0)))
    ImageHeader.ImageHeaderAddImageCrc32(b, int(header.get("image_crc32", 0)))
    ImageHeader.ImageHeaderAddLoadAddress(b, int(header.get("load_address", 0x0800C000)))
    ImageHeader.ImageHeaderAddEntryAddress(b, int(header.get("entry_address", 0)))
    ImageHeader.ImageHeaderAddBuildTimestamp(b, int(header.get("build_timestamp", 0)))
    ImageHeader.ImageHeaderAddGitCommit(b, git_vec)
    ImageHeader.ImageHeaderAddName(b, name)
    ImageHeader.ImageHeaderAddHeaderCrc32(b, int(header.get("header_crc32", 0)))
    return ImageHeader.ImageHeaderEnd(b)


def build_update_begin(header: dict[str, Any]) -> bytes:
    b = flatbuffers.Builder(1024)
    header_table = _build_image_header(b, header)
    UpdateBegin.UpdateBeginStart(b)
    UpdateBegin.UpdateBeginAddHeader(b, header_table)
    return _finish(b, Body.UpdateBegin, UpdateBegin.UpdateBeginEnd(b))


def build_update_data(offset: int, data: bytes) -> bytes:
    b = flatbuffers.Builder(len(data) + 256)
    vector = b.CreateByteVector(data)
    UpdateData.UpdateDataStart(b)
    UpdateData.UpdateDataAddOffset(b, offset)
    UpdateData.UpdateDataAddData(b, vector)
    return _finish(b, Body.UpdateData, UpdateData.UpdateDataEnd(b))


def build_update_end() -> bytes:
    b = flatbuffers.Builder(32)
    UpdateEnd.UpdateEndStart(b)
    return _finish(b, Body.UpdateEnd, UpdateEnd.UpdateEndEnd(b))


def build_activate() -> bytes:
    b = flatbuffers.Builder(32)
    Activate.ActivateStart(b)
    return _finish(b, Body.Activate, Activate.ActivateEnd(b))


def build_update_abort() -> bytes:
    b = flatbuffers.Builder(32)
    UpdateAbort.UpdateAbortStart(b)
    return _finish(b, Body.UpdateAbort, UpdateAbort.UpdateAbortEnd(b))


# --------------------------------------------------------------------------
# DEMO builders


def build_get_status() -> bytes:
    b = flatbuffers.Builder(32)
    GetStatus.GetStatusStart(b)
    return _finish(b, Body.GetStatus, GetStatus.GetStatusEnd(b))


def build_set_led(led: int, pattern: int, period_ms: int, force: bool = False) -> bytes:
    b = flatbuffers.Builder(64)
    SetLed.SetLedStart(b)
    SetLed.SetLedAddLed(b, led)
    SetLed.SetLedAddPattern(b, pattern)
    SetLed.SetLedAddPeriodMs(b, period_ms)
    SetLed.SetLedAddForce(b, force)
    return _finish(b, Body.SetLed, SetLed.SetLedEnd(b))


def build_set_led_all(pattern: int, period_ms: int) -> bytes:
    b = flatbuffers.Builder(64)
    SetLedAll.SetLedAllStart(b)
    SetLedAll.SetLedAllAddPattern(b, pattern)
    SetLedAll.SetLedAllAddPeriodMs(b, period_ms)
    return _finish(b, Body.SetLedAll, SetLedAll.SetLedAllEnd(b))


def build_clear_fault() -> bytes:
    b = flatbuffers.Builder(32)
    ClearFault.ClearFaultStart(b)
    return _finish(b, Body.ClearFault, ClearFault.ClearFaultEnd(b))


def build_enter_bootloader() -> bytes:
    b = flatbuffers.Builder(32)
    EnterBootloader.EnterBootloaderStart(b)
    return _finish(b, Body.EnterBootloader, EnterBootloader.EnterBootloaderEnd(b))


# --------------------------------------------------------------------------
# Event builders used by the simulator


def build_hello_reply(
    *,
    role: int,
    firmware_name: str,
    version: str,
    commit: str,
    build_time: str,
    uid: int,
    dev_id: int,
    rev_id: int,
    flash_mode: int,
    protocol_major: int = 1,
    protocol_minor: int = 1,
    max_frame: int = 2048,
    detail: str = "",
) -> bytes:
    b = flatbuffers.Builder(512)
    fw = b.CreateString(firmware_name)
    ver = b.CreateString(version)
    com = b.CreateString(commit)
    built = b.CreateString(build_time)
    det = b.CreateString(detail)
    HelloReply.HelloReplyStart(b)
    HelloReply.HelloReplyAddRole(b, role)
    HelloReply.HelloReplyAddFirmwareName(b, fw)
    HelloReply.HelloReplyAddVersion(b, ver)
    HelloReply.HelloReplyAddCommit(b, com)
    HelloReply.HelloReplyAddBuildTime(b, built)
    HelloReply.HelloReplyAddUid(b, uid)
    HelloReply.HelloReplyAddDevId(b, dev_id)
    HelloReply.HelloReplyAddRevId(b, rev_id)
    HelloReply.HelloReplyAddFlashMode(b, flash_mode)
    HelloReply.HelloReplyAddProtocolMajor(b, protocol_major)
    HelloReply.HelloReplyAddProtocolMinor(b, protocol_minor)
    HelloReply.HelloReplyAddMaxFrame(b, max_frame)
    HelloReply.HelloReplyAddDetail(b, det)
    return _finish(b, Body.HelloReply, HelloReply.HelloReplyEnd(b))


def build_pong(uptime_ms: int) -> bytes:
    b = flatbuffers.Builder(48)
    Pong.PongStart(b)
    Pong.PongAddUptimeMs(b, uptime_ms)
    return _finish(b, Body.Pong, Pong.PongEnd(b))


def build_device_info(fields: dict[str, Any]) -> bytes:
    b = flatbuffers.Builder(1024)
    strings = {
        key: b.CreateString(str(fields.get(key, "")))
        for key in ("firmware_name", "version", "commit", "build_time", "srs_id", "package", "uid", "reset_cause_text")
    }
    DeviceInfo.DeviceInfoStart(b)
    DeviceInfo.DeviceInfoAddFirmwareName(b, strings["firmware_name"])
    DeviceInfo.DeviceInfoAddVersion(b, strings["version"])
    DeviceInfo.DeviceInfoAddCommit(b, strings["commit"])
    DeviceInfo.DeviceInfoAddBuildTime(b, strings["build_time"])
    DeviceInfo.DeviceInfoAddSrsId(b, strings["srs_id"])
    DeviceInfo.DeviceInfoAddDevId(b, int(fields.get("dev_id", 0)))
    DeviceInfo.DeviceInfoAddRevId(b, int(fields.get("rev_id", 0)))
    DeviceInfo.DeviceInfoAddCpuid(b, int(fields.get("cpuid", 0)))
    DeviceInfo.DeviceInfoAddFlashSizeKb(b, int(fields.get("flash_size_kb", 0)))
    DeviceInfo.DeviceInfoAddPackage(b, strings["package"])
    DeviceInfo.DeviceInfoAddUid(b, strings["uid"])
    DeviceInfo.DeviceInfoAddOptcr(b, int(fields.get("optcr", 0)))
    DeviceInfo.DeviceInfoAddOptcr1(b, int(fields.get("optcr1", 0)))
    DeviceInfo.DeviceInfoAddSysclkHz(b, int(fields.get("sysclk_hz", 0)))
    DeviceInfo.DeviceInfoAddHclkHz(b, int(fields.get("hclk_hz", 0)))
    DeviceInfo.DeviceInfoAddPclk1Hz(b, int(fields.get("pclk1_hz", 0)))
    DeviceInfo.DeviceInfoAddPclk2Hz(b, int(fields.get("pclk2_hz", 0)))
    DeviceInfo.DeviceInfoAddApb1TimHz(b, int(fields.get("apb1_tim_hz", 0)))
    DeviceInfo.DeviceInfoAddApb2TimHz(b, int(fields.get("apb2_tim_hz", 0)))
    DeviceInfo.DeviceInfoAddPllqHz(b, int(fields.get("pllq_hz", 0)))
    DeviceInfo.DeviceInfoAddResetCause(b, int(fields.get("reset_cause", 0)))
    DeviceInfo.DeviceInfoAddResetCauseText(b, strings["reset_cause_text"])
    DeviceInfo.DeviceInfoAddUptimeMs(b, int(fields.get("uptime_ms", 0)))
    return _finish(b, Body.DeviceInfo, DeviceInfo.DeviceInfoEnd(b))


def build_group_list(page: int, page_count: int, total: int, groups: list[dict[str, Any]]) -> bytes:
    b = flatbuffers.Builder(1024)
    offsets = []
    for group in groups:
        code = b.CreateString(str(group.get("code", "")))
        name = b.CreateString(str(group.get("name", "")))
        GroupInfo.GroupInfoStart(b)
        GroupInfo.GroupInfoAddCode(b, code)
        GroupInfo.GroupInfoAddName(b, name)
        GroupInfo.GroupInfoAddTestCount(b, int(group.get("test_count", 0)))
        offsets.append(GroupInfo.GroupInfoEnd(b))
    GroupList.GroupListStartGroupsVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    vector = b.EndVector()
    GroupList.GroupListStart(b)
    GroupList.GroupListAddPage(b, page)
    GroupList.GroupListAddPageCount(b, page_count)
    GroupList.GroupListAddTotal(b, total)
    GroupList.GroupListAddGroups(b, vector)
    return _finish(b, Body.GroupList, GroupList.GroupListEnd(b))


def build_test_list(page: int, page_count: int, total: int, tests: list[dict[str, Any]]) -> bytes:
    b = flatbuffers.Builder(4096)
    offsets = []
    for test in tests:
        values = {
            "id": b.CreateString(str(test.get("id", ""))),
            "group": b.CreateString(str(test.get("group", ""))),
            "title": b.CreateString(str(test.get("title", ""))),
            "needs": b.CreateString(str(test.get("needs", ""))),
            "reqs": b.CreateString(str(test.get("reqs", ""))),
        }
        TestInfo.TestInfoStart(b)
        TestInfo.TestInfoAddId(b, values["id"])
        TestInfo.TestInfoAddGroup(b, values["group"])
        TestInfo.TestInfoAddTitle(b, values["title"])
        TestInfo.TestInfoAddLevel(b, int(test.get("level", 0)))
        TestInfo.TestInfoAddNeedsOperator(b, bool(test.get("operator", False)))
        TestInfo.TestInfoAddDestructive(b, bool(test.get("destructive", False)))
        TestInfo.TestInfoAddTimeoutMs(b, int(test.get("timeout_ms", 0)))
        TestInfo.TestInfoAddNeeds(b, values["needs"])
        TestInfo.TestInfoAddReqs(b, values["reqs"])
        offsets.append(TestInfo.TestInfoEnd(b))
    TestList.TestListStartTestsVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    vector = b.EndVector()
    TestList.TestListStart(b)
    TestList.TestListAddPage(b, page)
    TestList.TestListAddPageCount(b, page_count)
    TestList.TestListAddTotal(b, total)
    TestList.TestListAddTests(b, vector)
    return _finish(b, Body.TestList, TestList.TestListEnd(b))


def build_harness_info(
    fitted_mask: int, count: int, wires: list[dict[str, Any]], page: int = 0, page_count: int = 1
) -> bytes:
    b = flatbuffers.Builder(1024)
    offsets = []
    for wire in wires:
        values = {
            "name": b.CreateString(str(wire.get("name", ""))),
            "pin_a": b.CreateString(str(wire.get("pin_a", ""))),
            "pin_b": b.CreateString(str(wire.get("pin_b", ""))),
            "used_by": b.CreateString(str(wire.get("used_by", ""))),
        }
        HarnessWire.HarnessWireStart(b)
        HarnessWire.HarnessWireAddName(b, values["name"])
        HarnessWire.HarnessWireAddPinA(b, values["pin_a"])
        HarnessWire.HarnessWireAddPinB(b, values["pin_b"])
        HarnessWire.HarnessWireAddUsedBy(b, values["used_by"])
        HarnessWire.HarnessWireAddFitted(b, bool(wire.get("fitted", False)))
        HarnessWire.HarnessWireAddVerified(b, bool(wire.get("verified", False)))
        offsets.append(HarnessWire.HarnessWireEnd(b))
    HarnessInfo.HarnessInfoStartWiresVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    vector = b.EndVector()
    HarnessInfo.HarnessInfoStart(b)
    HarnessInfo.HarnessInfoAddFittedMask(b, fitted_mask)
    HarnessInfo.HarnessInfoAddCount(b, count)
    HarnessInfo.HarnessInfoAddWires(b, vector)
    HarnessInfo.HarnessInfoAddPage(b, page)
    HarnessInfo.HarnessInfoAddPageCount(b, page_count)
    return _finish(b, Body.HarnessInfo, HarnessInfo.HarnessInfoEnd(b))


def build_test_result(result: dict[str, Any]) -> bytes:
    b = flatbuffers.Builder(1024)
    id_s = b.CreateString(str(result.get("id", "")))
    group_s = b.CreateString(str(result.get("group", "")))
    reason_s = b.CreateString(str(result.get("reason", "")))
    reqs_s = b.CreateString(str(result.get("reqs", "")))
    measured = result.get("measured") or {}
    limits = result.get("limits") or {}
    if measured:
        unit_s = b.CreateString(str(measured.get("unit", "")))
        text_s = b.CreateString(str(measured.get("text", "")))
        Measurement.MeasurementStart(b)
        Measurement.MeasurementAddValue(b, float(measured.get("value", 0.0)))
        Measurement.MeasurementAddUnit(b, unit_s)
        Measurement.MeasurementAddText(b, text_s)
        Measurement.MeasurementAddValid(b, bool(measured.get("valid", True)))
        measured_table = Measurement.MeasurementEnd(b)
    else:
        measured_table = None
    if limits:
        unit_s = b.CreateString(str(limits.get("unit", "")))
        text_s = b.CreateString(str(limits.get("text", "")))
        Limits.LimitsStart(b)
        Limits.LimitsAddMin(b, float(limits.get("min", 0.0)))
        Limits.LimitsAddMax(b, float(limits.get("max", 0.0)))
        Limits.LimitsAddUnit(b, unit_s)
        Limits.LimitsAddText(b, text_s)
        Limits.LimitsAddValid(b, bool(limits.get("valid", True)))
        limits_table = Limits.LimitsEnd(b)
    else:
        limits_table = None
    TestResult.TestResultStart(b)
    TestResult.TestResultAddId(b, id_s)
    TestResult.TestResultAddGroup(b, group_s)
    TestResult.TestResultAddResult(b, int(result.get("result", TestVerdict.PASS)))
    TestResult.TestResultAddDurationMs(b, int(result.get("duration_ms", 0)))
    TestResult.TestResultAddReason(b, reason_s)
    if measured_table is not None:
        TestResult.TestResultAddMeasured(b, measured_table)
    if limits_table is not None:
        TestResult.TestResultAddLimits(b, limits_table)
    TestResult.TestResultAddReqs(b, reqs_s)
    return _finish(b, Body.TestResult, TestResult.TestResultEnd(b))


def build_result_list(page: int, page_count: int, total: int, results: list[dict[str, Any]]) -> bytes:
    b = flatbuffers.Builder(8192)
    offsets = [_append_test_result(b, result) for result in results]
    ResultList.ResultListStartResultsVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    vector = b.EndVector()
    ResultList.ResultListStart(b)
    ResultList.ResultListAddPage(b, page)
    ResultList.ResultListAddPageCount(b, page_count)
    ResultList.ResultListAddTotal(b, total)
    ResultList.ResultListAddResults(b, vector)
    return _finish(b, Body.ResultList, ResultList.ResultListEnd(b))


def _append_test_result(b: flatbuffers.Builder, result: dict[str, Any]) -> int:
    id_s = b.CreateString(str(result.get("id", "")))
    group_s = b.CreateString(str(result.get("group", "")))
    reason_s = b.CreateString(str(result.get("reason", "")))
    reqs_s = b.CreateString(str(result.get("reqs", "")))
    measured = result.get("measured") or {}
    limits = result.get("limits") or {}
    measured_table = None
    limits_table = None
    if measured:
        unit_s = b.CreateString(str(measured.get("unit", "")))
        text_s = b.CreateString(str(measured.get("text", "")))
        Measurement.MeasurementStart(b)
        Measurement.MeasurementAddValue(b, float(measured.get("value", 0.0)))
        Measurement.MeasurementAddUnit(b, unit_s)
        Measurement.MeasurementAddText(b, text_s)
        Measurement.MeasurementAddValid(b, bool(measured.get("valid", True)))
        measured_table = Measurement.MeasurementEnd(b)
    if limits:
        unit_s = b.CreateString(str(limits.get("unit", "")))
        text_s = b.CreateString(str(limits.get("text", "")))
        Limits.LimitsStart(b)
        Limits.LimitsAddMin(b, float(limits.get("min", 0.0)))
        Limits.LimitsAddMax(b, float(limits.get("max", 0.0)))
        Limits.LimitsAddUnit(b, unit_s)
        Limits.LimitsAddText(b, text_s)
        Limits.LimitsAddValid(b, bool(limits.get("valid", True)))
        limits_table = Limits.LimitsEnd(b)
    TestResult.TestResultStart(b)
    TestResult.TestResultAddId(b, id_s)
    TestResult.TestResultAddGroup(b, group_s)
    TestResult.TestResultAddResult(b, int(result.get("result", TestVerdict.PASS)))
    TestResult.TestResultAddDurationMs(b, int(result.get("duration_ms", 0)))
    TestResult.TestResultAddReason(b, reason_s)
    if measured_table is not None:
        TestResult.TestResultAddMeasured(b, measured_table)
    if limits_table is not None:
        TestResult.TestResultAddLimits(b, limits_table)
    TestResult.TestResultAddReqs(b, reqs_s)
    return TestResult.TestResultEnd(b)


def build_run_started(total: int, resumed: bool = False) -> bytes:
    b = flatbuffers.Builder(64)
    RunStarted.RunStartedStart(b)
    RunStarted.RunStartedAddTotal(b, total)
    RunStarted.RunStartedAddResumed(b, resumed)
    return _finish(b, Body.RunStarted, RunStarted.RunStartedEnd(b))


def build_test_started(test_id: str, index: int, total: int) -> bytes:
    b = flatbuffers.Builder(128)
    id_s = b.CreateString(test_id)
    TestStarted.TestStartedStart(b)
    TestStarted.TestStartedAddId(b, id_s)
    TestStarted.TestStartedAddIndex(b, index)
    TestStarted.TestStartedAddTotal(b, total)
    return _finish(b, Body.TestStarted, TestStarted.TestStartedEnd(b))


def build_operator_prompt(
    prompt_id: int, test_id: str, kind: int, text_value: str, answers: str, timeout_s: int
) -> bytes:
    b = flatbuffers.Builder(512)
    test_s = b.CreateString(test_id)
    text_s = b.CreateString(text_value)
    answers_s = b.CreateString(answers)
    OperatorPrompt.OperatorPromptStart(b)
    OperatorPrompt.OperatorPromptAddPromptId(b, prompt_id)
    OperatorPrompt.OperatorPromptAddTestId(b, test_s)
    OperatorPrompt.OperatorPromptAddKind(b, kind)
    OperatorPrompt.OperatorPromptAddText(b, text_s)
    OperatorPrompt.OperatorPromptAddAnswers(b, answers_s)
    OperatorPrompt.OperatorPromptAddTimeoutS(b, timeout_s)
    return _finish(b, Body.OperatorPrompt, OperatorPrompt.OperatorPromptEnd(b))


def build_run_finished(
    total: int, pass_: int, fail: int, skip: int, na: int, aborted: bool = False, resumed: bool = False
) -> bytes:
    b = flatbuffers.Builder(96)
    RunFinished.RunFinishedStart(b)
    RunFinished.RunFinishedAddTotal(b, total)
    RunFinished.RunFinishedAddPass(b, pass_)
    RunFinished.RunFinishedAddFail(b, fail)
    RunFinished.RunFinishedAddSkip(b, skip)
    RunFinished.RunFinishedAddNa(b, na)
    RunFinished.RunFinishedAddAborted(b, aborted)
    RunFinished.RunFinishedAddResumed(b, resumed)
    return _finish(b, Body.RunFinished, RunFinished.RunFinishedEnd(b))


def build_report(
    text_value: str, pass_: int, fail: int, skip: int, na: int, total: int, overall_pass: bool
) -> bytes:
    b = flatbuffers.Builder(1024)
    text_s = b.CreateString(text_value)
    Report.ReportStart(b)
    Report.ReportAddText(b, text_s)
    Report.ReportAddPass(b, pass_)
    Report.ReportAddFail(b, fail)
    Report.ReportAddSkip(b, skip)
    Report.ReportAddNa(b, na)
    Report.ReportAddTotal(b, total)
    Report.ReportAddOverallPass(b, overall_pass)
    return _finish(b, Body.Report, Report.ReportEnd(b))


def build_setting(key: str, value: str, known: bool = True) -> bytes:
    b = flatbuffers.Builder(256)
    key_s = b.CreateString(key)
    value_s = b.CreateString(value)
    Setting.SettingStart(b)
    Setting.SettingAddKey(b, key_s)
    Setting.SettingAddValue(b, value_s)
    Setting.SettingAddKnown(b, known)
    return _finish(b, Body.Setting, Setting.SettingEnd(b))


def build_button_event(press: int, pattern: int, period_ms: int) -> bytes:
    b = flatbuffers.Builder(64)
    ButtonEvent.ButtonEventStart(b)
    ButtonEvent.ButtonEventAddPress(b, press)
    ButtonEvent.ButtonEventAddPattern(b, pattern)
    ButtonEvent.ButtonEventAddPeriodMs(b, period_ms)
    return _finish(b, Body.ButtonEvent, ButtonEvent.ButtonEventEnd(b))


# --------------------------------------------------------------------------
# BOOT/DEMO event builders used by the simulator


def build_boot_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return slot


def build_boot_info(info: dict[str, Any]) -> bytes:
    b = flatbuffers.Builder(1024)
    slots = info.get("slots") or []
    offsets = []
    for slot in slots:
        git_s = b.CreateString(str(slot.get("git_commit", "")))
        name_s = b.CreateString(str(slot.get("name", "")))
        detail_s = b.CreateString(str(slot.get("detail", "")))
        BootSlotInfo.BootSlotInfoStart(b)
        BootSlotInfo.BootSlotInfoAddSlot(b, int(slot.get("slot", 0)))
        BootSlotInfo.BootSlotInfoAddPresent(b, bool(slot.get("present", False)))
        BootSlotInfo.BootSlotInfoAddValid(b, bool(slot.get("valid", False)))
        BootSlotInfo.BootSlotInfoAddPending(b, bool(slot.get("pending", False)))
        BootSlotInfo.BootSlotInfoAddConfirmed(b, bool(slot.get("confirmed", False)))
        BootSlotInfo.BootSlotInfoAddRejected(b, bool(slot.get("rejected", False)))
        BootSlotInfo.BootSlotInfoAddSequence(b, int(slot.get("sequence", 0)))
        BootSlotInfo.BootSlotInfoAddTrialCount(b, int(slot.get("trial_count", 0)))
        BootSlotInfo.BootSlotInfoAddVersion(b, int(slot.get("version", 0)))
        BootSlotInfo.BootSlotInfoAddBuildTimestamp(b, int(slot.get("build_timestamp", 0)))
        BootSlotInfo.BootSlotInfoAddGitCommit(b, git_s)
        BootSlotInfo.BootSlotInfoAddName(b, name_s)
        BootSlotInfo.BootSlotInfoAddImageSize(b, int(slot.get("image_size", 0)))
        BootSlotInfo.BootSlotInfoAddDetail(b, detail_s)
        offsets.append(BootSlotInfo.BootSlotInfoEnd(b))
    BootInfo.BootInfoStartSlotsVector(b, len(offsets))
    for offset in reversed(offsets):
        b.PrependUOffsetTRelative(offset)
    vector = b.EndVector()
    BootInfo.BootInfoStart(b)
    BootInfo.BootInfoAddActiveSlot(b, int(info.get("active_slot", 0)))
    BootInfo.BootInfoAddLastOutcome(b, int(info.get("last_outcome", 0)))
    BootInfo.BootInfoAddRejectedSlot(b, int(info.get("rejected_slot", 0)))
    BootInfo.BootInfoAddFlashMode(b, int(info.get("flash_mode", 0)))
    BootInfo.BootInfoAddSlots(b, vector)
    return _finish(b, Body.BootInfo, BootInfo.BootInfoEnd(b))


def build_update_result(success: bool, image_crc32: int, error: int = 0, detail: str = "") -> bytes:
    b = flatbuffers.Builder(256)
    detail_s = b.CreateString(detail)
    UpdateResult.UpdateResultStart(b)
    UpdateResult.UpdateResultAddSuccess(b, success)
    UpdateResult.UpdateResultAddImageCrc32(b, image_crc32)
    UpdateResult.UpdateResultAddError(b, error)
    UpdateResult.UpdateResultAddDetail(b, detail_s)
    return _finish(b, Body.UpdateResult, UpdateResult.UpdateResultEnd(b))


def build_status(status: dict[str, Any]) -> bytes:
    b = flatbuffers.Builder(4096)
    strings = {
        key: b.CreateString(str(status.get(key, "")))
        for key in ("firmware_name", "version", "commit", "build_time", "reset_cause_text")
    }
    leds = status.get("leds") or []
    led_offsets = []
    for led in leds:
        LedState.LedStateStart(b)
        LedState.LedStateAddLed(b, int(led.get("led", 1)))
        LedState.LedStateAddPattern(b, int(led.get("pattern", 0)))
        LedState.LedStateAddPeriodMs(b, int(led.get("period_ms", 0)))
        led_offsets.append(LedState.LedStateEnd(b))
    Status.StatusStartLedsVector(b, len(led_offsets))
    for offset in reversed(led_offsets):
        b.PrependUOffsetTRelative(offset)
    leds_vec = b.EndVector()
    tasks = status.get("tasks") or []
    task_offsets = []
    for task in tasks:
        name_s = b.CreateString(str(task.get("name", "")))
        TaskStats.TaskStatsStart(b)
        TaskStats.TaskStatsAddName(b, name_s)
        TaskStats.TaskStatsAddPriority(b, int(task.get("priority", 0)))
        TaskStats.TaskStatsAddState(b, int(task.get("state", 0)))
        TaskStats.TaskStatsAddStackHighWater(b, int(task.get("stack_high_water", 0)))
        task_offsets.append(TaskStats.TaskStatsEnd(b))
    Status.StatusStartTasksVector(b, len(task_offsets))
    for offset in reversed(task_offsets):
        b.PrependUOffsetTRelative(offset)
    tasks_vec = b.EndVector()
    fault = status.get("fault") or {}
    if fault:
        fault_strings = {
            key: b.CreateString(str(fault.get(key, ""))) for key in ("reason", "task", "file")
        }
        FaultInfo.FaultInfoStart(b)
        FaultInfo.FaultInfoAddPresent(b, bool(fault.get("present", False)))
        FaultInfo.FaultInfoAddReason(b, fault_strings["reason"])
        FaultInfo.FaultInfoAddCfsr(b, int(fault.get("cfsr", 0)))
        FaultInfo.FaultInfoAddHfsr(b, int(fault.get("hfsr", 0)))
        FaultInfo.FaultInfoAddMmfar(b, int(fault.get("mmfar", 0)))
        FaultInfo.FaultInfoAddBfar(b, int(fault.get("bfar", 0)))
        FaultInfo.FaultInfoAddPc(b, int(fault.get("pc", 0)))
        FaultInfo.FaultInfoAddLr(b, int(fault.get("lr", 0)))
        FaultInfo.FaultInfoAddTask(b, fault_strings["task"])
        FaultInfo.FaultInfoAddFile(b, fault_strings["file"])
        FaultInfo.FaultInfoAddLine(b, int(fault.get("line", 0)))
        fault_table = FaultInfo.FaultInfoEnd(b)
    else:
        fault_table = None
    Status.StatusStart(b)
    Status.StatusAddFirmwareName(b, strings["firmware_name"])
    Status.StatusAddVersion(b, strings["version"])
    Status.StatusAddCommit(b, strings["commit"])
    Status.StatusAddBuildTime(b, strings["build_time"])
    Status.StatusAddUid(b, int(status.get("uid", 0)))
    Status.StatusAddUptimeMs(b, int(status.get("uptime_ms", 0)))
    Status.StatusAddSlot(b, int(status.get("slot", 0)))
    Status.StatusAddTrial(b, int(status.get("trial", 0)))
    Status.StatusAddTrialCount(b, int(status.get("trial_count", 0)))
    Status.StatusAddHealth(b, int(status.get("health", 0)))
    Status.StatusAddResetCause(b, int(status.get("reset_cause", 0)))
    Status.StatusAddResetCauseText(b, strings["reset_cause_text"])
    if fault_table is not None:
        Status.StatusAddFault(b, fault_table)
    Status.StatusAddLeds(b, leds_vec)
    Status.StatusAddTasks(b, tasks_vec)
    Status.StatusAddHeapFree(b, int(status.get("heap_free", 0)))
    Status.StatusAddHeapMinFree(b, int(status.get("heap_min_free", 0)))
    Status.StatusAddProtocolMajor(b, int(status.get("protocol_major", 1)))
    Status.StatusAddProtocolMinor(b, int(status.get("protocol_minor", 1)))
    return _finish(b, Body.Status, Status.StatusEnd(b))


# --------------------------------------------------------------------------
# Parsers


def parse_hello_reply(t: Any) -> dict[str, Any]:
    return {
        "role": int(t.Role()),
        "role_name": ROLE_NAMES.get(int(t.Role()), f"ROLE_{int(t.Role())}"),
        "firmware_name": text(t.FirmwareName()),
        "version": text(t.Version()),
        "commit": text(t.Commit()),
        "build_time": text(t.BuildTime()),
        "uid": int(t.Uid()),
        # HelloReply carries only 64 of the 96 UID bits; do not pad it to
        # 24 digits as if it were the full UID.  HWVAL gets the full value
        # from DeviceInfo.uid (see StudioController._on_connected).
        "uid_hex": f"{int(t.Uid()):016X}",
        "dev_id": int(t.DevId()),
        "rev_id": int(t.RevId()),
        "flash_mode": int(t.FlashMode()),
        "flash_mode_name": FLASH_MODE_NAMES.get(int(t.FlashMode()), "UNKNOWN"),
        "protocol_major": int(t.ProtocolMajor()),
        "protocol_minor": int(t.ProtocolMinor()),
        "max_frame": int(t.MaxFrame()),
        "detail": text(t.Detail()),
    }


def parse_pong(t: Any) -> int:
    return int(t.UptimeMs())


def parse_ack(t: Any) -> dict[str, Any]:
    return {"seq": int(t.Seq()), "next_offset": int(t.NextOffset())}


def parse_nak(t: Any) -> dict[str, Any]:
    return {
        "seq": int(t.Seq()),
        "error": int(t.Error()),
        "error_name": error_name(int(t.Error())),
        "detail": text(t.Detail()),
        "expected_offset": int(t.ExpectedOffset()),
    }


def parse_log(t: Any) -> dict[str, Any]:
    return {"level": int(t.Level()), "level_name": LOG_LEVEL_NAMES.get(int(t.Level()), "INFO"), "text": text(t.Text())}


def log_level(t: Any) -> str:
    return LOG_LEVEL_NAMES.get(int(t.Level()), "INFO")


def parse_progress(t: Any) -> dict[str, Any]:
    return {"operation": text(t.Operation()), "done": int(t.Done()), "total": int(t.Total())}


def parse_device_info(t: Any) -> dict[str, Any]:
    return {
        "firmware_name": text(t.FirmwareName()),
        "version": text(t.Version()),
        "commit": text(t.Commit()),
        "build_time": text(t.BuildTime()),
        "srs_id": text(t.SrsId()),
        "dev_id": int(t.DevId()),
        "dev_id_hex": f"{int(t.DevId()):03X}",
        "rev_id": int(t.RevId()),
        "rev_id_hex": f"{int(t.RevId()):04X}",
        "cpuid": int(t.Cpuid()),
        "flash_size_kb": int(t.FlashSizeKb()),
        "package": text(t.Package()),
        "uid": text(t.Uid()),
        "optcr": int(t.Optcr()),
        "optcr1": int(t.Optcr1()),
        "sysclk_hz": int(t.SysclkHz()),
        "hclk_hz": int(t.HclkHz()),
        "pclk1_hz": int(t.Pclk1Hz()),
        "pclk2_hz": int(t.Pclk2Hz()),
        "apb1_tim_hz": int(t.Apb1TimHz()),
        "apb2_tim_hz": int(t.Apb2TimHz()),
        "pllq_hz": int(t.PllqHz()),
        "reset_cause": int(t.ResetCause()),
        "reset_cause_text": text(t.ResetCauseText()),
        "uptime_ms": int(t.UptimeMs()),
    }


def parse_group_list(t: Any) -> dict[str, Any]:
    groups = []
    for index in range(int(t.GroupsLength())):
        group = t.Groups(index)
        groups.append(
            {"code": text(group.Code()), "name": text(group.Name()), "test_count": int(group.TestCount())}
        )
    return {"page": int(t.Page()), "page_count": int(t.PageCount()), "total": int(t.Total()), "groups": groups}


def parse_test_list(t: Any) -> dict[str, Any]:
    tests = []
    for index in range(int(t.TestsLength())):
        test = t.Tests(index)
        tests.append(
            {
                "id": text(test.Id()),
                "group": text(test.Group()),
                "title": text(test.Title()),
                "level": int(test.Level()),
                "level_name": LEVEL_NAMES.get(int(test.Level()), "S"),
                "operator": bool(test.NeedsOperator()),
                "destructive": bool(test.Destructive()),
                "timeout_ms": int(test.TimeoutMs()),
                "needs": text(test.Needs()),
                "reqs": text(test.Reqs()),
            }
        )
    return {"page": int(t.Page()), "page_count": int(t.PageCount()), "total": int(t.Total()), "tests": tests}


def parse_harness(t: Any) -> dict[str, Any]:
    wires = []
    for index in range(int(t.WiresLength())):
        wire = t.Wires(index)
        wires.append(
            {
                "name": text(wire.Name()),
                "pin_a": text(wire.PinA()),
                "pin_b": text(wire.PinB()),
                "used_by": text(wire.UsedBy()),
                "fitted": bool(wire.Fitted()),
                "verified": bool(wire.Verified()),
            }
        )
    return {
        "fitted_mask": int(t.FittedMask()),
        "count": int(t.Count()),
        "wires": wires,
        "page": int(t.Page()),
        "page_count": int(t.PageCount()),
    }


def parse_measurement(t: Any) -> dict[str, Any]:
    if t is None:
        return {}
    return {
        "value": float(t.Value()),
        "unit": text(t.Unit()),
        "text": text(t.Text()),
        "valid": bool(t.Valid()),
    }


def parse_limits(t: Any) -> dict[str, Any]:
    if t is None:
        return {}
    return {
        "min": float(t.Min()),
        "max": float(t.Max()),
        "unit": text(t.Unit()),
        "text": text(t.Text()),
        "valid": bool(t.Valid()),
    }


def parse_test_result(t: Any) -> dict[str, Any]:
    return {
        "id": text(t.Id()),
        "group": text(t.Group()),
        "result": int(t.Result()),
        "result_name": VERDICT_NAMES.get(int(t.Result()), "N/A"),
        "duration_ms": int(t.DurationMs()),
        "reason": text(t.Reason()),
        "measured": parse_measurement(t.Measured()),
        "limits": parse_limits(t.Limits()),
        "reqs": text(t.Reqs()),
    }


def parse_result_list(t: Any) -> dict[str, Any]:
    results = []
    for index in range(int(t.ResultsLength())):
        results.append(parse_test_result(t.Results(index)))
    return {"page": int(t.Page()), "page_count": int(t.PageCount()), "total": int(t.Total()), "results": results}


def parse_report(t: Any) -> dict[str, Any]:
    return {
        "text": text(t.Text()),
        "pass": int(t.Pass()),
        "fail": int(t.Fail()),
        "skip": int(t.Skip()),
        "na": int(t.Na()),
        "total": int(t.Total()),
        "overall_pass": bool(t.OverallPass()),
    }


def parse_setting(t: Any) -> dict[str, Any]:
    return {"key": text(t.Key()), "value": text(t.Value()), "known": bool(t.Known())}


def parse_run_started(t: Any) -> dict[str, Any]:
    return {"total": int(t.Total()), "resumed": bool(t.Resumed())}


def parse_test_started(t: Any) -> dict[str, Any]:
    return {"id": text(t.Id()), "index": int(t.Index()), "total": int(t.Total())}


def parse_operator_prompt_done(t: Any) -> dict[str, Any]:
    return {
        "prompt_id": int(t.PromptId()),
        "answer": text(t.Answer()),
    }


def parse_operator_prompt(t: Any) -> dict[str, Any]:
    return {
        "prompt_id": int(t.PromptId()),
        "test_id": text(t.TestId()),
        "kind": int(t.Kind()),
        "kind_name": PROMPT_KIND_NAMES.get(int(t.Kind()), "YES_NO"),
        "text": text(t.Text()),
        "answers": text(t.Answers()),
        "timeout_s": int(t.TimeoutS()),
    }


def parse_run_finished(t: Any) -> dict[str, Any]:
    return {
        "total": int(t.Total()),
        "pass": int(t.Pass()),
        "fail": int(t.Fail()),
        "skip": int(t.Skip()),
        "na": int(t.Na()),
        "aborted": bool(t.Aborted()),
        "resumed": bool(t.Resumed()),
    }


def parse_boot_info(t: Any) -> dict[str, Any]:
    slots = []
    for index in range(int(t.SlotsLength())):
        slot = t.Slots(index)
        slots.append(
            {
                "slot": int(slot.Slot()),
                "slot_name": SLOT_NAMES.get(int(slot.Slot()), "—"),
                "present": bool(slot.Present()),
                "valid": bool(slot.Valid()),
                "pending": bool(slot.Pending()),
                "confirmed": bool(slot.Confirmed()),
                "rejected": bool(slot.Rejected()),
                "sequence": int(slot.Sequence()),
                "trial_count": int(slot.TrialCount()),
                "version": int(slot.Version()),
                "version_text": f"{(int(slot.Version()) >> 24) & 0xFF}.{(int(slot.Version()) >> 16) & 0xFF}.{int(slot.Version()) & 0xFF}",
                "build_timestamp": int(slot.BuildTimestamp()),
                "git_commit": text(slot.GitCommit()),
                "name": text(slot.Name()),
                "image_size": int(slot.ImageSize()),
                "detail": text(slot.Detail()),
            }
        )
    return {
        "active_slot": int(t.ActiveSlot()),
        "active_slot_name": SLOT_NAMES.get(int(t.ActiveSlot()), "—"),
        "last_outcome": int(t.LastOutcome()),
        "last_outcome_name": OUTCOME_NAMES.get(int(t.LastOutcome()), "UNKNOWN"),
        "rejected_slot": int(t.RejectedSlot()),
        "rejected_slot_name": SLOT_NAMES.get(int(t.RejectedSlot()), "—"),
        "flash_mode": int(t.FlashMode()),
        "flash_mode_name": FLASH_MODE_NAMES.get(int(t.FlashMode()), "UNKNOWN"),
        "slots": slots,
    }


def parse_update_result(t: Any) -> dict[str, Any]:
    return {
        "success": bool(t.Success()),
        "image_crc32": int(t.ImageCrc32()),
        "error": int(t.Error()),
        "error_name": error_name(int(t.Error())),
        "detail": text(t.Detail()),
    }


def parse_fault(t: Any) -> dict[str, Any]:
    if t is None:
        return {}
    return {
        "present": bool(t.Present()),
        "reason": text(t.Reason()),
        "cfsr": int(t.Cfsr()),
        "hfsr": int(t.Hfsr()),
        "mmfar": int(t.Mmfar()),
        "bfar": int(t.Bfar()),
        "pc": int(t.Pc()),
        "lr": int(t.Lr()),
        "task": text(t.Task()),
        "file": text(t.File()),
        "line": int(t.Line()),
    }


def parse_status(t: Any) -> dict[str, Any]:
    leds = []
    for index in range(int(t.LedsLength())):
        led = t.Leds(index)
        leds.append(
            {
                "led": int(led.Led()),
                "led_name": LED_ID_NAMES.get(int(led.Led()), "?"),
                "pattern": int(led.Pattern()),
                "pattern_name": LED_PATTERN_NAMES.get(int(led.Pattern()), "OFF"),
                "period_ms": int(led.PeriodMs()),
            }
        )
    tasks = []
    for index in range(int(t.TasksLength())):
        task = t.Tasks(index)
        tasks.append(
            {
                "name": text(task.Name()),
                "priority": int(task.Priority()),
                "state": int(task.State()),
                "stack_high_water": int(task.StackHighWater()),
            }
        )
    return {
        "firmware_name": text(t.FirmwareName()),
        "version": text(t.Version()),
        "commit": text(t.Commit()),
        "build_time": text(t.BuildTime()),
        "uid": int(t.Uid()),
        "uptime_ms": int(t.UptimeMs()),
        "slot": int(t.Slot()),
        "slot_name": SLOT_NAMES.get(int(t.Slot()), "—"),
        "trial": int(t.Trial()),
        "trial_name": TRIAL_NAMES.get(int(t.Trial()), "UNKNOWN"),
        "trial_count": int(t.TrialCount()),
        "health": int(t.Health()),
        "health_name": HEALTH_NAMES.get(int(t.Health()), "UNKNOWN"),
        "reset_cause": int(t.ResetCause()),
        "reset_cause_text": text(t.ResetCauseText()),
        "fault": parse_fault(t.Fault()),
        "leds": leds,
        "tasks": tasks,
        "heap_free": int(t.HeapFree()),
        "heap_min_free": int(t.HeapMinFree()),
        "protocol_major": int(t.ProtocolMajor()),
        "protocol_minor": int(t.ProtocolMinor()),
    }


def parse_button_event(t: Any) -> dict[str, Any]:
    return {
        "press": int(t.Press()),
        "press_name": "LONG" if int(t.Press()) == ButtonPress.LONG else "SHORT",
        "pattern": int(t.Pattern()),
        "pattern_name": LED_PATTERN_NAMES.get(int(t.Pattern()), "OFF"),
        "period_ms": int(t.PeriodMs()),
    }
