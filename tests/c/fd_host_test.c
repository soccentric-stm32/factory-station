/*
 * Host test of the generated factory data C API.
 *
 * Author: Sandesh Ghimire <sandesh@soccentric.com>
 *
 * usage: fd_host_test in.bin out.bin [chip-uid-hex]
 * in.bin is the three blocks (.sys, .cal, .diag), 4096 bytes each, as they
 * sit in flash from FD_SYS_ADDRESS. The program loads them through the
 * generated API, edits them in RAM, commits, and writes the result to
 * out.bin. It prints "key=value" lines the Python test checks.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "factory_data.h"

static uint8_t g_flash[3 * FD_BLOCK_SIZE];
static uint8_t g_chip[12];
static int g_have_chip;

static int flash_read(uint32_t addr, void *dst, size_t len)
{
    uint32_t off = addr - FD_SYS_ADDRESS;
    if (off + len > sizeof g_flash) {
        return -1;
    }
    memcpy(dst, g_flash + off, len);
    return 0;
}

static int flash_erase(uint32_t addr, size_t len)
{
    uint32_t off = addr - FD_SYS_ADDRESS;
    if (off + len > sizeof g_flash) {
        return -1;
    }
    memset(g_flash + off, 0xFF, len);
    return 0;
}

/* Like real flash: programming can only clear bits, so a missing erase shows. */
static int flash_write(uint32_t addr, const void *src, size_t len)
{
    uint32_t off = addr - FD_SYS_ADDRESS;
    const uint8_t *s = src;
    if (off + len > sizeof g_flash || (len & 3u) != 0) {
        return -1;
    }
    for (size_t i = 0; i < len; i++) {
        if (g_flash[off + i] != 0xFF) {
            return -1;
        }
        g_flash[off + i] = s[i];
    }
    return 0;
}

static bool chip_uid(uint8_t uid[12])
{
    memcpy(uid, g_chip, 12);
    return true;
}

static void hex(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        printf("%02X", p[i]);
    }
}

#define STEP(name, expr) printf("%s=%d\n", name, (int)(expr))

int main(int argc, char **argv)
{
    if (argc < 3) {
        return 2;
    }
    FILE *in = fopen(argv[1], "rb");
    if (!in || fread(g_flash, 1, sizeof g_flash, in) != sizeof g_flash) {
        return 2;
    }
    fclose(in);

    static fd_port_t port = {flash_read, flash_erase, flash_write, NULL};
    if (argc > 3) {
        for (int i = 0; i < 12; i++) {
            unsigned v;
            sscanf(argv[3] + 2 * i, "%2x", &v);
            g_chip[i] = (uint8_t)v;
        }
        port.chip_uid = chip_uid;
        g_have_chip = 1;
    }
    fd_init(&port);

    STEP("load_sys", fd_load(FD_SYS));
    STEP("load_cal", fd_load(FD_CAL));
    STEP("load_diag", fd_load(FD_DIAG));
    if (!fd_is_loaded(FD_SYS) || !fd_is_loaded(FD_CAL) || !fd_is_loaded(FD_DIAG)) {
        return 0;   /* the Python side checks the statuses */
    }

    char text[64];
    uint8_t mac[6];
    float gain, offset, temp;
    uint32_t initial;
    uint16_t depth, flags, code;
    uint8_t severity, fflags;

    /* ---- read what the Python generator wrote ---- */
    STEP("get_serial", fd_sys_get_serial(text, sizeof text));
    printf("serial=%s\n", text);
    STEP("get_product", fd_sys_get_product(text, sizeof text));
    printf("product=%s\n", text);
    STEP("get_mac", fd_sys_get_mac(mac));
    printf("mac=");
    hex(mac, 6);
    printf("\n");
    STEP("notes_absent", fd_sys_get_notes(text, sizeof text) == FD_ERR_NOT_FOUND);
    STEP("serial_small_buffer", fd_sys_get_serial(text, 3) == FD_ERR_RANGE);
    STEP("get_channel2", fd_cal_get_channel(2, &gain, &offset));
    printf("channel2=%.4f,%.4f\n", gain, offset);
    printf("channels=%zu\n", fd_cal_channel_count());
    STEP("get_temp_ref", fd_cal_get_temp_ref(&temp));
    printf("temp_ref=%.2f\n", temp);
    STEP("get_cal_operator", fd_cal_get_operator(text, sizeof text));
    printf("operator=%s\n", text);
    STEP("get_fault", fd_diag_get_fault(0x100, &severity, &fflags, text, sizeof text));
    printf("fault=%u,%u,%s\n", severity, fflags, text);
    STEP("get_counter1", fd_diag_get_counter(1, &initial));
    printf("counter1=%u\n", (unsigned)initial);
    STEP("get_log", fd_diag_get_log(&depth, &flags));
    printf("log=%u,%u\n", depth, flags);

    /* ---- bad use is refused ---- */
    STEP("empty_serial_refused", fd_sys_set_serial("") == FD_ERR_ARG);
    STEP("missing_channel", fd_cal_get_channel(77, &gain, &offset) == FD_ERR_NOT_FOUND);
    STEP("missing_remove", fd_cal_remove_channel(77) == FD_ERR_NOT_FOUND);

    /* ---- discard throws RAM edits away ---- */
    STEP("set_tmp", fd_sys_set_serial("TEMPORARY"));
    STEP("modified_after_set", fd_is_modified(FD_SYS));
    STEP("discard", fd_discard(FD_SYS));
    STEP("modified_after_discard", fd_is_modified(FD_SYS));
    fd_sys_get_serial(text, sizeof text);
    printf("serial_after_discard=%s\n", text);

    /* ---- edit in RAM ---- */
    STEP("set_serial", fd_sys_set_serial("SC9999"));
    STEP("set_notes", fd_sys_set_notes("edited on target"));
    STEP("set_mac", fd_sys_set_mac((const uint8_t[6]){0x02, 0x11, 0x22, 0x33, 0x44, 0x55}));
    STEP("set_rev", fd_set_revision(FD_SYS, 7));
    STEP("cal_set2", fd_cal_set_channel(2, 1.25f, 0.75f));
    STEP("cal_set3", fd_cal_set_channel(3, 2.0f, -2.0f));
    STEP("cal_remove1", fd_cal_remove_channel(1));
    STEP("cal_date", fd_cal_set_date("2026-11-12"));
    STEP("cal_remove_operator", fd_cal_remove_operator());
    STEP("diag_fault", fd_diag_set_fault(0x200, 3, 0, "New fault"));
    STEP("diag_fault_replace", fd_diag_set_fault(0x100, 1, 1, "Over temperature"));
    STEP("diag_counter1", fd_diag_set_counter(1, 77));
    STEP("diag_counter2", fd_diag_set_counter(2, 5));
    STEP("diag_log", fd_diag_set_log(128, 1));
    STEP("modified_before_commit", fd_is_modified(FD_CAL));

    /* the first fault's text length changed, so the second moved: read both */
    STEP("faults", fd_diag_fault_count());
    STEP("fault_at1", fd_diag_get_fault_at(1, &code, &severity, &fflags, text, sizeof text));
    printf("fault_at1=%u,%s\n", code, text);

    if (g_have_chip) {
        STEP("bind", fd_bind_to_chip(FD_SYS));
    }

    /* ---- commit all three ---- */
    STEP("commit_sys", fd_commit(FD_SYS));
    STEP("commit_cal", fd_commit(FD_CAL));
    STEP("commit_diag", fd_commit(FD_DIAG));
    STEP("modified_after_commit", fd_is_modified(FD_SYS));

    /* a fresh load of what was written must give the same answers */
    STEP("reload_sys", fd_load(FD_SYS));
    fd_sys_get_serial(text, sizeof text);
    printf("serial_reloaded=%s\n", text);

    FILE *out = fopen(argv[2], "wb");
    if (!out || fwrite(g_flash, 1, sizeof g_flash, out) != sizeof g_flash) {
        return 2;
    }
    fclose(out);
    return 0;
}
