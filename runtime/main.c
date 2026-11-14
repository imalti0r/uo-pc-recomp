// uo.exe entry: reserves the guest address space, parses the command line and runs one mode.
//
//   uo.exe [--exefs DIR] [--romfs DIR] [--profile DIR] <mode>
//     --selftest        layout, dispatch table and host-function registry; needs no game files
//     --probe-loader    load and relocate the modules, check them against the measured 1.2.1 values
//     --probe-kernel, --probe-ipc, --probe-services, --probe-fs    host probes
//     --probe-dpi       the process DPI awareness (per-monitor v2) and the primary monitor's size; no game files
//     --probe-gfx-abi   NVN object layouts against nvn.h's reserved sizes; needs no game files
//     --probe-nvn, --probe-nvn-tex, --probe-window, --probe-shader, --probe-vkmem, --probe-vkexec    graphics probes
//     --probe-astc      the compute ASTC decoder against astc_decode_ldr; needs no game files
//     --probe-movie     host VP9 movie::Decoder on one RomFS movie; skips without game data
//     --boot            run the game to the first NVN call
//     --seconds S       run the game (implies --boot's start-up) until the world clock (game time since the loaded
//                       save's first frame) passed S seconds, then exit 0 at that present
//     --shot PNG        with --seconds: save that frame; env UO_GPU=null|vulkan selects the back end
//     (no mode)         run the game
//
// Also here: the runtime services the generated code calls besides rt_dispatch and rt_svc (rt_brk, rt_udf,
// rt_ticks, the function profiler hook), logging, SHA-256, and the crash reporter (a vectored exception handler that
// names the lifted function, the guest registers and the guest frame-pointer chain).
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "dispatch.h"
#include "fs.h"
#include "mods.h"
#include "host.h"
#include "platform/platform.h"
#include "ui/menu.h"
#include "sha256.h"

#define MAX_MODULES 16

// ---- logging, tracing, hashing
unsigned rt_trace;

// Log file. A game run whose stderr is not a file already (started from Explorer, a shortcut or a console) also
// writes its log to logs\uo.log next to uo.exe, so a player has one to attach to a report; run_game.py and
// `2> file` keep their own file. Each start moves the earlier logs to uo.1.log .. uo.4.log. A file stops at 64 MiB
// (UO_TRACE can write that much). Lines from before log_open wait in a static buffer: mem_init runs first so that
// nothing else lands in the guest address space, and an earlier open would put the CRT's heap there.
#define LOG_KEEP 5
#define LOG_CAP (64ull << 20)
enum { LOG_EARLY, LOG_FILE, LOG_OFF };
static int g_log_state;
static PlatAppend *g_log_file;
static char g_log_path[PLAT_PATH_MAX * 3];
static char g_log_early[32768];
static size_t g_log_early_n;
static uint64_t g_log_bytes;

void rt_log_raw(const char *p, size_t n) {
    fwrite(p, 1, n, stderr);
    int state = __atomic_load_n(&g_log_state, __ATOMIC_ACQUIRE);
    if (state == LOG_EARLY) {
        size_t at = __atomic_fetch_add(&g_log_early_n, n, __ATOMIC_RELAXED);
        if (at + n <= sizeof g_log_early) memcpy(g_log_early + at, p, n);
    } else if (state == LOG_FILE) {
        uint64_t at = __atomic_fetch_add(&g_log_bytes, n, __ATOMIC_RELAXED);
        if (at + n <= LOG_CAP) {
            plat_append_write(g_log_file, p, n);
        } else if (at <= LOG_CAP) {   // the first write past the cap says so, once
            static const char cut[] = "[host] log: the file reached 64 MiB, the rest of this run goes to stderr only\n";
            plat_append_write(g_log_file, cut, sizeof cut - 1);
        }
    }
}

void rt_vlogf(const char *fmt, va_list ap) {
    char buf[2048];
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    if (n > 0) rt_log_raw(buf, n < (int)sizeof buf ? (size_t)n : sizeof buf - 1);
}

void rt_logf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    rt_vlogf(fmt, ap);
    va_end(ap);
}

void rt_log(const char *fmt, ...) {
    char buf[2048];
    int n = snprintf(buf, sizeof buf, "[host] ");
    va_list ap;
    va_start(ap, fmt);
    int k = vsnprintf(buf + n, sizeof buf - n - 1, fmt, ap);
    va_end(ap);
    n = k < 0 ? n : n + k < (int)sizeof buf - 1 ? n + k : (int)sizeof buf - 2;
    buf[n++] = '\n';
    rt_log_raw(buf, n);
}

const char *rt_log_path(void) {
    return __atomic_load_n(&g_log_state, __ATOMIC_ACQUIRE) == LOG_FILE ? g_log_path : NULL;
}

// Opens the log file for a game run (game = 1) and writes the buffered lines to it; anything else drops them. Runs on
// the main thread before the boot starts other threads.
static void log_open(int game) {
    char dir[PLAT_PATH_MAX * 3], from[PLAT_PATH_MAX * 3 + 16], to[PLAT_PATH_MAX * 3 + 16];
    char *cut = NULL;
    if (game && !plat_stderr_path(dir, sizeof dir) && plat_exe_path(dir, sizeof dir)) {
        cut = plat_last_sep(dir);
        char *f = strrchr(dir, '/');
        if (f > cut) cut = f;
    }
    if (cut && (size_t)(cut - dir) + sizeof "/logs" <= sizeof dir) {
        strcpy(cut, "/logs");
        plat_file_mkdir_p(dir);
        const char *stem = RT_LOG_STEM;
        for (int i = LOG_KEEP - 1; i > 0; i--) {   // totk.3.log -> totk.4.log, ..., uo.log -> totk.1.log
            if (i > 1) snprintf(from, sizeof from, "%s/%s.%d.log", dir, stem, i - 1);
            else snprintf(from, sizeof from, "%s/%s.log", dir, stem);
            snprintf(to, sizeof to, "%s/%s.%d.log", dir, stem, i);
            if (plat_file_exists(from)) plat_file_replace(from, to);
        }
        snprintf(g_log_path, sizeof g_log_path, "%s/%s.log", dir, stem);
        g_log_file = plat_append_open(g_log_path);
    }
    if (!g_log_file) {
        __atomic_store_n(&g_log_state, LOG_OFF, __ATOMIC_RELEASE);
        return;
    }
    size_t n = __atomic_load_n(&g_log_early_n, __ATOMIC_RELAXED);
    if (n > sizeof g_log_early) n = sizeof g_log_early;
    plat_append_write(g_log_file, g_log_early, n);
    g_log_bytes = n;
    __atomic_store_n(&g_log_state, LOG_FILE, __ATOMIC_RELEASE);
    rt_log("log: %s", g_log_path);
}

static void parse_trace(void) {
    const char *e = cfg_get("UO_TRACE");   // no default: nothing is traced when unset
    if (!e) return;
    static const struct { const char *name; unsigned bit; } names[] = {
        {"svc", RT_TRACE_SVC}, {"host", RT_TRACE_HOSTCALL}, {"ipc", RT_TRACE_IPC}, {"fs", RT_TRACE_FS},
        {"gfx", RT_TRACE_GFX}, {"perf", RT_TRACE_PERF}, {"sched", RT_TRACE_SCHED}, {"audio", RT_TRACE_AUDIO},
        {"present", RT_TRACE_PRESENT}, {"all", ~0u}};
    for (const char *p = e; *p;) {
        size_t n = strcspn(p, ",");
        int known = 0;
        for (unsigned i = 0; i < sizeof names / sizeof *names; i++)
            if (strlen(names[i].name) == n && !strncmp(p, names[i].name, n)) rt_trace |= names[i].bit, known = 1;
        if (!known && n) rt_log("UO_TRACE: unknown item '%.*s' (svc, host, ipc, fs, gfx, perf, sched, audio, present, all)", (int)n, p);
        p += n + (p[n] == ',');
    }
}

void rt_sha256(const void *p, size_t n, uint8_t out[32]) { sha256(p, n, out); }

// UO_ARENA_TRACE: report the game's bump-arena objects (main's 0x28f47e0 and 0x28f2de0 slots) whenever one of their
// fields changes, so a base that goes to zero can be attributed to the code that ran just before.
void rt_arena_trace(void) {
    static uint64_t last[2][6];
    static int n;
    // Read once: rt_ticks calls this on every guest clock read (tens of thousands of times a frame), and cfg_get
    // normalises the key and walks the whole settings table on each call.
    static int on = -1;
    if (on < 0) {
        const char *e = cfg_get("UO_ARENA_TRACE");
        on = e && *e;
    }
    if (!on || !rt_layout_count) return;
    uint64_t base = rt_layout[0].base;
    for (int k = 0; k < 2; k++) {
        uint64_t slot = base + (k ? 0x28f4000ull + 0x7e0 : 0x28f2000ull + 0xde0);
        if (!plat_mem_readable(slot, 8)) continue;
        uint64_t o = LD64(slot);
        if (!o || !plat_mem_readable(o, 0x40)) continue;
        uint64_t now[6] = {LD64(o), LD64(o + 8), LD64(o + 0x10), LD64(o + 0x20), LD64(o + 0x28), LD64(o + 0x30)};
        int changed = 0;
        for (int i = 0; i < 6; i++) changed |= now[i] != last[k][i];
        if (!changed) continue;
        for (int i = 0; i < 6; i++) last[k][i] = now[i];
        if (n++ > 400) return;
        rt_logf("arena[%d] %#llx obj %#llx vt %#llx base %#llx end %#llx cur %#llx lim %#llx cur2 %#llx", k,
                (unsigned long long)slot, (unsigned long long)o, (unsigned long long)now[0],
                (unsigned long long)now[1], (unsigned long long)now[2], (unsigned long long)now[3],
                (unsigned long long)now[4], (unsigned long long)now[5]);
    }
}

// ---- configuration
static RtConfig cfg;
const RtConfig *rt_config(void) { return &cfg; }

// ---- runtime services the generated code calls (cpu.h)
volatile int g_fnprof = 0;
// UO_BP=<addr>[,<addr>...]: guest function starts to report on entry (hex, with or without 0x). Every lifted
// function entry then tests this list, which is only worth it while tracing one path.
static uint64_t bp_addr[16];
static int bp_n;
static uint64_t bp_min, bp_max, bp_x0, bp_x1, bp_from;
static int bp_x0_set, bp_x1_set, bp_from_set, bp_max_set, bp_sp;
static int bp_all;
static uint64_t bp_mem[8];
static int bp_mem_n;
static uint64_t bp_memreg_off[4];
static int bp_memreg_reg[4], bp_memreg_n;
void fnprof_hit(uint64_t addr, uint64_t lr, uint64_t x0) {
    for (int i = 0; i < bp_n; i++)
        if (bp_addr[i] == addr) {
            static int hits;
            Cpu *c = kern_cpu();
            uint64_t x1 = c ? c->x[1] : 0, x8 = c ? c->x[8] : 0, x19 = c ? c->x[19] : 0;
            // UO_BP_FROM: only calls from this return address (a call site inside one lifted function).
            if (bp_from_set && lr != bp_from) return;
            // UO_BP_MIN: only report calls whose x0 (the size an allocator takes first) is at least this, to cut a
            // hot allocation path down to the interesting requests. UO_BP_X0: only calls whose x0 is exactly this.
            if (bp_x0_set && x0 != bp_x0) return;
            if (bp_x1_set && x1 != bp_x1) return;
            if (!bp_x0_set && !bp_x1_set && bp_min && x0 < bp_min) return;
            if (bp_max_set && x0 >= bp_max) return;
            if (hits++ > 4000) return;
            char at[128], from[128];
            rt_log("bp: %s x0 %#llx x1 %#llx x8 %#llx x19 %#llx from %s", ldr_describe(addr, at, sizeof at),
                   (unsigned long long)x0, (unsigned long long)x1, (unsigned long long)x8, (unsigned long long)x19,
                   ldr_describe(lr, from, sizeof from));
            // UO_BP_ALL: the whole register file, for a site whose arguments are not in the first four.
            if (bp_all && c) {
                for (int r = 0; r < 31; r++)
                    rt_logf("    x%-2d %016llx%s", r, (unsigned long long)c->x[r], r % 4 == 3 ? "\n" : "");
                rt_logf("    sp  %016llx\n", (unsigned long long)c->sp);
            }
            // The first eight words of the object the call names: for an allocator that is its block base, cursor
            // and limit.
            if (x0 && plat_mem_readable(x0, 64)) {
                rt_logf("    [%#llx]", (unsigned long long)x0);
                for (int k = 0; k < 8; k++)
                    rt_logf(" %016llx", (unsigned long long)*(const uint64_t *)(uintptr_t)(x0 + 8 * k));
                rt_log_raw("\n", 1);
            }
            // UO_BP_MEMREG=<reg>:<offset>[,...]: the 8 words at a register plus an offset, for a structure that
            // moves between runs (reg 19 and offset 3600 is x19+0x3600).
            for (int i = 0; bp_memreg_n && i < bp_memreg_n; i++) {
                uint64_t a = (c ? c->x[bp_memreg_reg[i]] : 0) + bp_memreg_off[i];
                if (!a || !plat_mem_readable(a, 64)) continue;
                rt_logf("    reg%d+%#llx [%#llx]", bp_memreg_reg[i], (unsigned long long)bp_memreg_off[i],
                        (unsigned long long)a);
                for (int k = 0; k < 8; k++)
                    rt_logf(" %016llx", (unsigned long long)*(const uint64_t *)(uintptr_t)(a + 8 * k));
                rt_log_raw("\n", 1);
            }
            if (bp_mem_n && c) {
                for (int i = 0; i < bp_mem_n; i++) {
                    uint64_t a = bp_mem[i];
                    if (!a || !plat_mem_readable(a, 64)) continue;
                    rt_logf("    mem[%#llx]", (unsigned long long)a);
                    for (int k = 0; k < 8; k++)
                        rt_logf(" %016llx", (unsigned long long)*(const uint64_t *)(uintptr_t)(a + 8 * k));
                    rt_log_raw("\n", 1);
                }
            }
            return;
        }
}
void rt_bp_init(void) {
    const char *e = cfg_get("UO_BP");
    if (e && *e)
        for (const char *p = e; *p && bp_n < 16;) {
            char *end = NULL;
            uint64_t a = strtoull(p, &end, 16);
            if (end == p) break;
            bp_addr[bp_n++] = a;
            p = *end == ',' ? end + 1 : end;
        }
    const char *m = cfg_get("UO_BP_MIN");
    if (m && *m) bp_min = strtoull(m, NULL, 0);
    const char *z = cfg_get("UO_BP_X0");
    if (z && *z) bp_x0 = strtoull(z, NULL, 0), bp_x0_set = 1;
    const char *o = cfg_get("UO_BP_X1");
    if (o && *o) bp_x1 = strtoull(o, NULL, 0), bp_x1_set = 1;
    const char *f = cfg_get("UO_BP_FROM");
    if (f && *f) bp_from = strtoull(f, NULL, 16), bp_from_set = 1;
    const char *mx = cfg_get("UO_BP_MAX");
    if (mx && *mx) bp_max = strtoull(mx, NULL, 0), bp_max_set = 1;
    const char *sp = cfg_get("UO_BP_SP");
    if (sp && *sp && *sp != '0') bp_sp = 1;
    const char *all = cfg_get("UO_BP_ALL");
    if (all && *all && *all != '0') bp_all = 1;
    const char *mm = cfg_get("UO_BP_MEM");
    if (mm && *mm)
        for (const char *q = mm; *q && bp_mem_n < 8;) {
            char *end = NULL;
            uint64_t a = strtoull(q, &end, 16);
            if (end == q) break;
            bp_mem[bp_mem_n++] = a;
            q = *end == ',' ? end + 1 : end;
        }
    const char *mr = cfg_get("UO_BP_MEMREG");
    if (mr && *mr)
        for (const char *q = mr; *q && bp_memreg_n < 4;) {
            char *end = NULL;
            int r = (int)strtol(q, &end, 10);
            if (end == q || *end != ':') break;
            q = end + 1;
            uint64_t off;
            if (*q == '-') off = (uint64_t)(-(int64_t)strtoull(q + 1, &end, 16));
            else off = strtoull(q, &end, 16);            bp_memreg_reg[bp_memreg_n] = r < 0 || r > 30 ? 0 : r;
            bp_memreg_off[bp_memreg_n] = off;
            bp_memreg_n++;
            q = *end == ',' ? end + 1 : end;
        }
    g_fnprof = bp_n > 0;
    if (bp_n) rt_log("bp: %d guest address(es) watched, x1 at least %#llx", bp_n, (unsigned long long)bp_min);
}

void rt_brk(Cpu *c, uint32_t imm, uint64_t pc) {
    char at[128], lr[128];
    rt_fatal("brk #%#x at %s (lr %s)", imm, ldr_describe(pc, at, sizeof at), ldr_describe(c->x[30], lr, sizeof lr));
}

void rt_udf(Cpu *c, uint64_t pc) {
    char at[128], lr[128];
    // UO_NOABORT: the SDK's noreturn abort stubs end in a udf, and the compiler also emits one right after
    // every call it knows never returns. Swallowing means resuming past the stub: walk the frame chain
    // (one level deeper on each consecutive trap, so a nested pair of stubs also gets out) and resume the
    // dispatcher at the first return address that is not itself a trap.
    const char *na = cfg_get("UO_NOABORT");
    static int nsw;
    if (na && *na && *na != '0' && nsw < 64) {
        uint64_t fp = c->x[29], ret = c->x[30];
        for (int i = 0; i <= nsw && fp && plat_mem_readable(fp, 16); i++) {
            uint64_t saved = *(const uint64_t *)(uintptr_t)fp, ra = *(const uint64_t *)(uintptr_t)(fp + 8);
            ret = ra, fp = saved;
        }
        nsw++;
        rt_log("diag: undefined instruction swallowed (UO_NOABORT #%d) at %s, resuming %s", nsw,
               ldr_describe(pc, at, sizeof at), ldr_describe(ret, lr, sizeof lr));
        rt_guest_resume(c, ret);
    }
    rt_fatal("undefined instruction at %s (lr %s)", ldr_describe(pc, at, sizeof at),
             ldr_describe(c->x[30], lr, sizeof lr));
}

// CNTPCT_EL0: the Switch counter runs at 19.2 MHz. The scale is set before main, so no thread reads it half written.
static PlatScale tick_scale;

__attribute__((constructor)) static void tick_scale_init(void) { tick_scale = plat_scale(19200000u, plat_counter_hz()); }

uint64_t rt_ticks(void) {
    rt_arena_trace();
    return plat_scaled(tick_scale, plat_counter());
}

// ---- crash reporter
typedef struct { uintptr_t host; uint64_t guest; } FnRev;
static FnRev *fn_rev;
static uint64_t fn_rev_n;

static int by_host(const void *a, const void *b) {
    uintptr_t x = ((const FnRev *)a)->host, y = ((const FnRev *)b)->host;
    return x < y ? -1 : x > y;
}

// The lifted function that contains a host code address, by the nearest function start below it; 0 outside the lifted
// code.
static uint64_t lifted_function(uintptr_t rip) {
    if (!fn_rev) {
        static RtModule mods[MAX_MODULES];
        int n = rt_linked_modules(mods, MAX_MODULES);
        uint64_t total = 0;
        for (int i = 0; i < n; i++) total += mods[i].count;
        fn_rev = malloc((total ? total : 1) * sizeof *fn_rev);
        if (!fn_rev) return 0;
        for (int i = 0; i < n; i++)
            for (uint64_t k = 0; k < mods[i].count; k++)
                fn_rev[fn_rev_n++] = (FnRev){(uintptr_t)mods[i].fns[k], mods[i].addrs[k]};
        qsort(fn_rev, fn_rev_n, sizeof *fn_rev, by_host);
    }
    // The lifted code is one range with the host code outside it (kernel.c ap_init checks that); an address above the
    // last function start is taken for host code, which costs only the last function's body.
    if (!fn_rev_n || rip > fn_rev[fn_rev_n - 1].host) return 0;
    uint64_t lo = 0, hi = fn_rev_n;
    while (lo < hi) {
        uint64_t mid = (lo + hi) / 2;
        if (fn_rev[mid].host <= rip) lo = mid + 1;
        else hi = mid;
    }
    return lo ? fn_rev[lo - 1].guest : 0;
}

static int readable(uint64_t a, uint64_t n) { return plat_mem_readable(a, n); }

static void crash_handler(const PlatCrash *x) {
    static volatile int32_t busy;
    if (plat_xchg32(&busy, 1)) plat_exit(71);
    char d[160];
    uintptr_t rip = x->rip;
    rt_logf("FATAL: %s (%#x) at host %p (", x->what, x->code, (void *)rip);
    if (x->module && !*x->module) rt_logf("outside every module)");
    else rt_logf("%s+%#llx)", x->module ? x->module : "uo", (unsigned long long)x->offset);
    if (x->access >= 0)
        rt_logf(", %s address %#llx", x->access == 1 ? "writing" : x->access == 8 ? "executing" : "reading",
                (unsigned long long)x->addr);
    rt_log_raw("\n", 1);
    uint64_t f = x->module ? 0 : lifted_function(rip);
    if (f) rt_logf("  in lifted function f_%llx = %s\n", (unsigned long long)f, ldr_describe(f, d, sizeof d));
    // abort and invalid parameter: where the host code called it; a fault in another module: the host calls that led
    // into it
    uintptr_t exe = plat_exe_base();
    for (int i = 0; i < x->nframes; i++) {
        uint64_t g = lifted_function(x->frames[i]);
        rt_logf("  host frame %2d: uo+%#llx", i, (unsigned long long)(x->frames[i] - exe));
        if (g) rt_logf(" in lifted function f_%llx", (unsigned long long)g);
        rt_log_raw("\n", 1);
    }
    Cpu *c = kern_cpu();
    if (c) {
        rt_logf("  tpidr %016llx tpidrro %016llx thread %p\n", (unsigned long long)c->tpidr,
                (unsigned long long)c->tpidrro, c->thread);
        if (c->tpidrro && readable(c->tpidrro + 0x1F8, 8))
            rt_logf("  TLR+0x1F8 (thread object) %016llx\n", (unsigned long long)LD64(c->tpidrro + 0x1F8));
        for (int i = 0; i < 31; i++)
            rt_logf("  x%-2d %016llx%s", i, (unsigned long long)c->x[i], i % 4 == 3 ? "\n" : "");
        rt_logf("  sp  %016llx\n  pc  %016llx  lr %s\n", (unsigned long long)c->sp, (unsigned long long)c->pc,
                ldr_describe(c->x[30], d, sizeof d));
        // UO_CRASH_DUMP: hex words around the addresses in the registers that name data (diagnosing a corrupt table).
        const char *dump = cfg_get("UO_CRASH_DUMP");
        if (dump && *dump && strcmp(dump, "0")) {
            for (int r = 0; r < 31; r++) {
                uint64_t a = c->x[r] & ~15ull;
                if (!a || !readable(a, 128)) continue;
                rt_logf("  dump x%d @ %#llx:", r, (unsigned long long)a);
                for (int k = 0; k < 8; k++) rt_logf(" %016llx", (unsigned long long)LD64(a + 8 * k));
                rt_log_raw("\n", 1);
            }
        }
        // UO_CRASH_DUMP=3: main's two-allocator routing globals (0x28f8000 + 0xa60/0xa68/0xc48/0xc50): the game
        // picks the allocator for a pointer by comparing it with the value at +0xc50, and a heap that sits on the
        // other side of that boundary sends a malloc block to the slab allocator.
        if (dump && atoi(dump) >= 3) {
            uint64_t g = rt_layout[0].base + 0x28f8000;
            rt_logf("  routing: a60 %#llx a68 %#llx c48 %#llx c50 %#llx\n", (unsigned long long)LD64(g + 0xa60),
                    (unsigned long long)LD64(g + 0xa68), (unsigned long long)LD64(g + 0xc48),
                    (unsigned long long)LD64(g + 0xc50));
            uint64_t p48 = LD64(g + 0xc48), p50 = LD64(g + 0xc50);
            if (p48 && readable(p48, 0x20))
                rt_logf("  [c48] %#llx: %016llx %016llx\n", (unsigned long long)p48, (unsigned long long)LD64(p48),
                        (unsigned long long)LD64(p48 + 8));
            if (p50 && readable(p50, 0x20))
                rt_logf("  [c50] %#llx: %016llx %016llx\n", (unsigned long long)p50, (unsigned long long)LD64(p50),
                        (unsigned long long)LD64(p50 + 8));
            rt_logf("  heap base %#llx size %#llx, alias %#llx\n", (unsigned long long)mem_layout()->heap.base,
                    (unsigned long long)mem_layout()->heap.size, (unsigned long long)mem_layout()->alias.base);
            uint64_t a60 = LD64(g + 0xa60), a68 = LD64(g + 0xa68);
            for (int k = 0; k < 2; k++) {
                uint64_t o = k ? a68 : a60;
                if (!o || !readable(o + 0x3810, 0x30)) continue;
                uint32_t rows = (uint32_t)LD32(o + 0x3818);
                uint64_t lo = 0, hi = 0;
                if (rows && readable(o + 0x30, 0x10)) {
                    lo = LD64(o + 0x30);
                    uint64_t last = o + 0x30 + 0x68ull * (rows - 1);
                    hi = readable(last, 0x10) ? LD64(last) + (uint32_t)LD32(last + 8) : 0;
                }
                rt_logf("  arena %s %#llx rows %u seq %u covers %#llx..%#llx\n", k ? "a68" : "a60",
                        (unsigned long long)o, rows, (unsigned)LD32(o + 0x381c), (unsigned long long)lo,
                        (unsigned long long)hi);
            }
            uint64_t hook = LD64(rt_layout[0].base + 0x3b00ca0), hookobj = rt_layout[0].base + 0x3b00ca8;
            rt_logf("  malloc hook %#llx object %#llx: %016llx %016llx\n", (unsigned long long)hook,
                    (unsigned long long)hookobj, (unsigned long long)LD64(hookobj),
                    (unsigned long long)LD64(hookobj + 8));
            uint64_t c50 = LD64(g + 0xc50);
            if (c50 && readable(c50, 0x10))
                rt_logf("  [c50] value %#llx, *value %#llx\n", (unsigned long long)c50,
                        (unsigned long long)LD64(c50));
            // Both allocators' block tables: which one owns the address being freed tells a wrong boundary from a
            // wrong allocation.
            for (int k = 0; k < 2; k++) {
                uint64_t o = k ? a68 : a60;
                if (!o || !readable(o + 0x3818, 4)) continue;
                uint32_t rows = (uint32_t)LD32(o + 0x3818);
                uint32_t cnt = (uint32_t)LD32(o + 0x381c);
                rt_logf("  %s %#llx rows %u seq %u\n", k ? "a68" : "a60", (unsigned long long)o, rows, cnt);
                for (uint32_t i = 0; i < rows && i < 80; i++) {
                    uint64_t row = o + 0x30 + 0x68ull * i;
                    if (!readable(row, 0x78)) break;
                    rt_logf("    [%3u] base %#llx size %#x f40 %#x f44 %u\n", i, (unsigned long long)LD64(row),
                            (unsigned)LD32(row + 8), (unsigned)LD32(row + 0x40), (unsigned)LD32(row + 0x44));
                }
            }
        }
        // UO_CRASH_DUMP=12: the heap head main+0x5e2714 takes its arena size from (root+0x48618, field +0xb0), dumped
        // whole so the size field can be compared with the sizes the game asks for.
        if (dump && atoi(dump) >= 12) {
            uint64_t root = LD64(rt_layout[0].base + 0x28f2000 + 0xde0);
            uint64_t t = root && readable(root + 0x48618, 8) ? LD64(root + 0x48618) : 0;
            rt_logf("  heap head %#llx\n", (unsigned long long)t);
            if (t && readable(t, 0x200))
                for (int k = 0; k < 0x200; k += 0x10)
                    rt_logf("    +%#04x: %016llx %016llx\n", k, (unsigned long long)LD64(t + k),
                            (unsigned long long)LD64(t + k + 8));
        }
        // UO_CRASH_DUMP=11: the size table main+0x5e2714 sizes its pools from.
        // UO_CRASH_DUMP=10: the two function pointers main+0x5e2714 calls to learn its arena sizes
        // (0x28f8000+0xef8 and the pool builder's own first field), named through the host-function registry.
        if (dump && atoi(dump) >= 10) {
            uint64_t g = rt_layout[0].base + 0x28f8000;
            uint64_t slot = LD64(g + 0xef8);
            uint64_t fn = slot ? LD64(slot) : 0;
            const char *nm = hostfn_name(fn);
            rt_logf("  arena size fn [%#llx] = %#llx (%s)", (unsigned long long)slot, (unsigned long long)fn,
                    nm ? nm : "guest");
            uint64_t root = LD64(rt_layout[0].base + 0x28f2000 + 0xde0);
            if (root && readable(root + 0x3620, 8)) {
                uint64_t b = LD64(root + 0x3620);
                rt_logf("  pool builder %#llx", (unsigned long long)b);
                if (b && readable(b, 8)) {
                    const char *b0 = hostfn_name(LD64(b));
                    rt_logf(" first %#llx (%s)", (unsigned long long)LD64(b), b0 ? b0 : "guest");
                }
            }
            rt_log_raw("\n", 1);
        }
        // UO_CRASH_DUMP=9: the objects main+0x5e2714 calls virtually (0x28f9000+0x1e0 .. +0x330) and their vtables,
        // whose entries name the getters that produce the arena sizes.
        if (dump && atoi(dump) >= 9) {
            uint64_t g = rt_layout[0].base + 0x28f9000;
            static const uint32_t offs[] = {0x1d8, 0x1e0, 0x1e8, 0x1f0, 0x218, 0x2e0, 0x2e8, 0x2f0,
                                            0x318, 0x328, 0x330};
            for (unsigned k = 0; k < sizeof offs / sizeof offs[0]; k++) {
                uint64_t slot = g + offs[k];
                uint64_t obj = LD64(slot);
                rt_logf("  slot +%#05x -> obj %#llx", offs[k], (unsigned long long)obj);
                if (obj && readable(obj, 8)) {
                    uint64_t vt = LD64(obj);
                    const char *nm = hostfn_name(vt);
                    rt_logf(" first %#llx (%s)", (unsigned long long)vt, nm ? nm : "guest code");
                    if (!nm && vt && readable(vt, 0x40)) {
                        rt_log_raw(":", 1);
                        for (int e = 0; e < 4; e++) {
                            uint64_t f = LD64(vt + 8 * e);
                            const char *fn = hostfn_name(f);
                            rt_logf(" [%d]%s", e, fn ? fn : "guest");
                        }
                    }
                }
                rt_log_raw("\n", 1);
            }
        }
        // UO_CRASH_DUMP=8: the buffer-size table main+0x5e2714 sizes its arenas from, found through the pointer the
        // first arena object holds at +0x00 (the object itself is at root+0x3600 and its sizes come from there), which
        // is where a size too large for its own arena comes from.
        if (dump && atoi(dump) >= 8) {
            uint64_t root = LD64(rt_layout[0].base + 0x28f2000 + 0xde0);
            uint64_t arena = root && readable(root + 0x3600, 8) ? LD64(root + 0x3600) : 0;
            rt_logf("  arena obj %#llx\n", (unsigned long long)arena);
            if (arena && readable(arena, 0x120))
                for (int k = 0; k < 0x120; k += 0x10)
                    rt_logf("    +%#04x: %016llx %016llx\n", k, (unsigned long long)LD64(arena + k),
                            (unsigned long long)LD64(arena + k + 8));
        }
        // UO_CRASH_DUMP=7: the memory the game reserved for itself and the arena fields built on it, which tell a
        // failed reservation from a later overwrite.
        if (dump && atoi(dump) >= 7) {
            uint64_t root = LD64(rt_layout[0].base + 0x28f2000 + 0xde0);
            if (root && readable(root, 0x48700)) {
                static const struct { uint32_t off; const char *what; } f[] = {
                    {0x3168, "block base"}, {0x3170, "block size"}, {0x3178, "block end"},
                    {0x3600, "arena obj"}, {0x3608, "arena base"}, {0x3610, "arena size"},
                    {0x3618, "arena end"}, {0x3620, "arena2"}, {0x3720, "w"}, {0x3724, "h"},
                    {0x48618, "pool"},
                };
                for (unsigned k = 0; k < sizeof f / sizeof f[0]; k++)
                    rt_logf("  root+%#06x %-10s %016llx\n", f[k].off, f[k].what,
                            (unsigned long long)LD64(root + f[k].off));
            }
        }
        // UO_CRASH_DUMP=6: the game's root object (0x28f2000+0xde0) arena fields at +0x3600, the block bases and
        // cursors of the two heaps the game builds on memory it reserved itself.
        if (dump && atoi(dump) >= 6) {
            uint64_t root = LD64(rt_layout[0].base + 0x28f2000 + 0xde0);
            rt_logf("  root %#llx\n", (unsigned long long)root);
            if (root && readable(root, 0x3860))
                for (int k = 0; k < 8; k++)
                    rt_logf("    +%#06x: %016llx %016llx %016llx %016llx\n", 0x3600 + k * 0x20,
                            (unsigned long long)LD64(root + 0x3600 + k * 0x20),
                            (unsigned long long)LD64(root + 0x3608 + k * 0x20),
                            (unsigned long long)LD64(root + 0x3610 + k * 0x20),
                            (unsigned long long)LD64(root + 0x3618 + k * 0x20));
        }
        // UO_CRASH_DUMP=5: main's bump-heap objects: the global at 0x28f2000+0xde0 that main+0x5a55c4 picks, the
        // one at 0x28f4000+0x7e0 its other branch uses, and main+0x5a0a00's thread index, whose record the game
        // reads from the array at 0x28f8000+0xc40.
        if (dump && atoi(dump) >= 5) {
            uint64_t base = rt_layout[0].base;
            for (int k = 0; k < 2; k++) {
                uint64_t slot = base + (k ? 0x28f4000ull + 0x7e0 : 0x28f2000ull + 0xde0);
                uint64_t o = LD64(slot);
                rt_logf("  bump[%d] slot %#llx object %#llx\n", k, (unsigned long long)slot, (unsigned long long)o);
                if (o && readable(o, 0x48))
                    for (int j = 0; j < 9; j++)
                        rt_logf("    +%#04x: %016llx\n", j * 8, (unsigned long long)LD64(o + 8 * j));
            }
            uint64_t map = base + 0x3a8c930;
            rt_logf("  thread map %#llx: %016llx %016llx\n", (unsigned long long)map, (unsigned long long)LD64(map),
                    (unsigned long long)LD64(map + 8));
            uint64_t arr = LD64(base + 0x28f8000ull + 0xc40);
            if (arr && readable(arr, 0x38 * 8))
                for (int k = 0; k < 8; k++)
                    rt_logf("    rec[%d] base %#llx cap %#x align %#x cur %#llx end %#llx\n", k,
                            (unsigned long long)LD64(arr + 0x38ull * k + 8), (unsigned)LD32(arr + 0x38ull * k + 0x10),
                            (unsigned)LD32(arr + 0x38ull * k + 0x1c), (unsigned long long)LD64(arr + 0x38ull * k + 0x20),
                            (unsigned long long)LD64(arr + 0x38ull * k + 0x30));
        }
        // UO_CRASH_DUMP=4: main's thread-allocator array (0x28f8000 + 0xc38 fallback, +0xc40 records of 0x38
        // bytes), which the game indexes by the current thread. A record with a zero base hands out an offset where
        // the caller expects an address.
        if (dump && atoi(dump) >= 4) {
            uint64_t g = rt_layout[0].base + 0x28f8000;
            uint64_t fallback = LD64(g + 0xc38), arr = LD64(g + 0xc40);
            rt_logf("  allocators: fallback %#llx array %#llx\n", (unsigned long long)fallback,
                    (unsigned long long)arr);
            for (int k = -1; k < 8; k++) {
                uint64_t a = k < 0 ? fallback : arr + 0x38ull * (unsigned)k;
                if (!a || !readable(a, 0x38)) continue;
                rt_logf("    [%2d] %#llx: %016llx %016llx %016llx %016llx | %016llx %016llx %016llx\n", k,
                        (unsigned long long)a, (unsigned long long)LD64(a), (unsigned long long)LD64(a + 8),
                        (unsigned long long)LD64(a + 0x10), (unsigned long long)LD64(a + 0x18),
                        (unsigned long long)LD64(a + 0x20), (unsigned long long)LD64(a + 0x28),
                        (unsigned long long)LD64(a + 0x30));
            }
        }
        // UO_CRASH_DUMP=2: the 0x68-byte block table x19 names (base u64 at +0x30, size u32 at +0x38, a u32 at
        // +0x70 and a count at +0x74 per row, the row count at +0x3818), to tell a corrupt table from a wrong index.
        if (dump && atoi(dump) >= 2) {
            for (int which = 0; which < 2; which++) {
                uint64_t base = which ? c->x[0] : c->x[19];
                if (!base || !readable(base + 0x3818, 4)) continue;
                uint32_t rows = (uint32_t)LD32(base + 0x3818);
                rt_logf("  table %s %#llx rows %u\n", which ? "x0" : "x19", (unsigned long long)base, rows);
                for (uint32_t i = 0; i < rows && i < 200; i++) {
                    uint64_t row = base + 0x30 + 0x68ull * i;
                    if (!readable(row, 0x78)) break;
                    rt_logf("    [%3u] base %#llx size %#x f40 %#x f44 %u\n", i,
                            (unsigned long long)LD64(row), (unsigned)LD32(row + 8), (unsigned)LD32(row + 0x40),
                            (unsigned)LD32(row + 0x44));
                }
            }
            uint64_t ptr = c->x[20];
            if (ptr && readable(ptr - 0x40, 0x80)) {
                rt_logf("  block %#llx around:\n", (unsigned long long)ptr);
                for (int k = -8; k < 8; k++)
                    rt_logf("    %#llx: %016llx\n", (unsigned long long)(ptr + 8 * k),
                            (unsigned long long)LD64(ptr + 8 * k));
            }
        }
        uint64_t fp = c->x[29];
        for (int n = 0; n < 32 && fp && !(fp & 7) && readable(fp, 16); n++) {
            uint64_t next = LD64(fp), lr = LD64(fp + 8);
            rt_logf("  guest frame %2d: %s\n", n, ldr_describe(lr, d, sizeof d));
            if (next <= fp || next - fp > (64u << 20)) break;
            fp = next;
        }
    } else {
        rt_logf("  not on a guest thread\n");
    }
    fflush(stderr);
    plat_exit(71);
}

// ---- self-test: layout, dispatch table, host-function registry, without game files
static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("  FAIL: " __VA_ARGS__); putchar('\n'); } } while (0)

static double now_s(void) { return (double)plat_now_ns() / 1e9; }

static volatile int host_probe_hits;
static void host_probe(Cpu *c) { host_probe_hits++; c->x[0] = 42; }

static void check_cpu(void) {
    __builtin_cpu_init();
    int ok = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma") && __builtin_cpu_supports("bmi2");
    printf("cpu: x86-64-v3 (AVX2, FMA, BMI2) %s\n", ok ? "present" : "MISSING");
    CHECK(ok, "the lifted code is built for x86-64-v3");
}

// Layout: ascending, no overlap, below 2^39, and inside the window mem_init reserved.
static void check_layout(void) {
    const RtLayoutEntry *lay = rt_layout;
    int n = rt_layout_count;
    printf("layout: %d modules\n", n);
    for (int i = 0; i < n; i++) {
        printf("  %-8s base %#llx  size %#10llx  end %#llx\n", lay[i].name, (unsigned long long)lay[i].base,
               (unsigned long long)lay[i].size, (unsigned long long)(lay[i].base + lay[i].size));
        CHECK(lay[i].size > 0, "%s has size 0", lay[i].name);
        CHECK(lay[i].base % 0x10000 == 0, "%s base is not 64 KiB aligned", lay[i].name);
        CHECK(lay[i].base + lay[i].size <= RT_KERNEL_BASE, "%s reaches into the kernel region", lay[i].name);
        if (i) {
            CHECK(lay[i].base > lay[i - 1].base, "%s base is not above %s", lay[i].name, lay[i - 1].name);
            CHECK(lay[i - 1].base + lay[i - 1].size <= lay[i].base, "%s overlaps %s", lay[i - 1].name, lay[i].name);
        }
        uint64_t end;
        int ours = plat_mem_state(lay[i].base, lay[i].base + 1, &end) == PLAT_MEM_RESERVED;
        CHECK(ours, "%s is not inside the reserved module window", lay[i].name);
    }
    const RtGuestLayout *g = mem_layout();
    printf("guest memory: aslr %#llx+%#llx stack %#llx heap %#llx alias %#llx+%#llx kernel %#llx host functions %#llx\n",
           (unsigned long long)g->aslr.base, (unsigned long long)g->aslr.size, (unsigned long long)g->stack.base,
           (unsigned long long)g->heap.base, (unsigned long long)g->alias.base, (unsigned long long)g->alias.size,
           (unsigned long long)g->kernel.base, (unsigned long long)g->hostfn.base);
    CHECK(g->alias.base + g->alias.size <= lay[0].base && g->heap.base + g->heap.size <= g->alias.base,
          "guest regions overlap");
}

// Every function start dispatches to its own function. The word after a start (unless that is a start too), a
// misaligned address and the word below the module dispatch to nothing.
static void check_modules(const RtModule *mods, int n) {
    uint64_t total = 0;
    for (int i = 0; i < n; i++) {
        const RtModule *m = &mods[i];
        const RtLayoutEntry *l = NULL;
        for (int k = 0; k < rt_layout_count; k++)
            if (!strcmp(rt_layout[k].name, m->name)) l = &rt_layout[k];
        CHECK(l && l->base == m->base && l->size == m->size, "%s: linked base/size differ from the layout", m->name);

        uint64_t bad = 0, bad_neg = 0;
        for (uint64_t k = 0; k < m->count; k++) {
            uint64_t a = m->addrs[k];
            if (rt_dispatch_lookup(a) != m->fns[k] && bad++ < 5)
                printf("  FAIL: %s_fn_addrs[%llu] %#llx does not dispatch to its function\n", m->name,
                       (unsigned long long)k, (unsigned long long)a);
            int next_is_start = k + 1 < m->count && m->addrs[k + 1] == a + 4;
            if ((!next_is_start && rt_dispatch_lookup(a + 4)) || rt_dispatch_lookup(a + 2)) bad_neg++;
        }
        if (rt_dispatch_lookup(m->base - 4)) bad_neg++;
        failures += bad > 0;
        CHECK(!bad_neg, "%s: %llu addresses that are not function starts dispatch to a function", m->name,
              (unsigned long long)bad_neg);
        uint64_t lo = m->count ? m->addrs[0] : 0, hi = m->count ? m->addrs[m->count - 1] : 0;
        printf("  %-8s %8llu functions, first %#llx, last %#llx: %s\n", m->name, (unsigned long long)m->count,
               (unsigned long long)lo, (unsigned long long)hi, bad || bad_neg ? "FAIL" : "ok");
        total += m->count;
    }
    printf("  total    %8llu functions\n", (unsigned long long)total);
}

static void bench(const RtModule *mods, int n) {
    uint64_t total = 0, sink = 0;
    double t0 = now_s();
    for (int i = 0; i < n; i++) {
        for (uint64_t k = 0; k < mods[i].count; k++) sink += (uintptr_t)rt_dispatch_lookup(mods[i].addrs[k]);
        total += mods[i].count;
    }
    double dt = now_s() - t0;
    __asm__ volatile("" ::"r"(sink));
    if (total) printf("lookup: %.1f ns per function start (address order)\n", dt * 1e9 / (double)total);
}

// Memory SVCs without modules: heap, physical memory in the alias region, a stack move, QueryMemory, GetInfo.
static void check_memory(void) {
    const RtGuestLayout *g = mem_layout();
    RtMemoryInfo q;
    uint64_t heap = 0, v = 0;
    int before = failures;
    CHECK(mem_set_heap_size(0x400000, &heap) == 0 && heap == g->heap.base, "SetHeapSize 4 MiB");
    memset((void *)(uintptr_t)heap, 0xAB, 0x400000);
    mem_query(heap + 0x1000, &q);
    CHECK(q.base == heap && q.size == 0x400000 && q.state == 5 && q.perm == 3, "QueryMemory on the heap");
    CHECK(mem_set_heap_size(0x300000, &heap) != 0, "SetHeapSize accepts a size that is not a 2 MiB multiple");

    uint64_t a = g->alias.base + 0x100000;
    CHECK(mem_map_physical(a, 0x10000) == 0, "MapPhysicalMemory");
    ST64(a + 0x8000, 0x1234);
    CHECK(mem_map_physical(a - 0x4000, 0x20000) == 0 && LD64(a + 0x8000) == 0x1234,
          "MapPhysicalMemory over a mapped range lost its contents");
    mem_query(a, &q);
    CHECK(q.base == a - 0x4000 && q.size == 0x20000 && q.state == 5, "QueryMemory on mapped alias pages");
    mem_info(7, &v);
    CHECK(v == 0x400000 + 0x20000, "GetInfo 7 (used memory) %#llx", (unsigned long long)v);

    uint64_t stack = g->stack.base + 0x100000;
    ST64(a + 8, 0x99);
    CHECK(mem_map(stack, a, 0x4000) == 0 && LD64(stack + 8) == 0x99, "MapMemory alias -> stack did not copy");
    ST64(stack + 0x10, 0x5678);
    mem_query(stack, &q);
    CHECK(q.base == stack && q.size == 0x4000 && q.state == 0xB, "QueryMemory on a moved stack");
    mem_query(a, &q);
    CHECK(q.base == a && q.state == 5 && q.perm == 0, "QueryMemory on a moved stack's source");
    CHECK(mem_unmap_physical(a, 0x4000) != 0, "UnmapPhysicalMemory under a moved stack");
    CHECK(mem_unmap(stack, a, 0x4000) == 0 && LD64(a + 0x10) == 0x5678, "UnmapMemory did not copy the stack back");

    CHECK(mem_unmap_physical(a - 0x4000, 0x20000) == 0, "UnmapPhysicalMemory");
    mem_query(a, &q);
    CHECK(q.state == 0 && q.base == g->alias.base && q.size == g->alias.size, "QueryMemory on the free alias region");
    CHECK(mem_map_physical(a, 0x1000) == 0 && LD64(a + 0x10) == 0 && mem_unmap_physical(a, 0x1000) == 0,
          "remapped physical memory is not zeroed");
    // A mapping larger than the whole pool must be refused with LimitReached. The size is taken from the pool, not
    // written as 4 GiB: Unicorn Overlord asks for an 8 GiB pool, inside which a 4 GiB mapping would legitimately
    // succeed, and it must still stay inside the alias window so the refusal comes from the pool and not from the
    // address check.
    const RtGuestLayout *lay2 = mem_layout();
    if (lay2->pool + 0x200000 < lay2->alias.size) {
        uint64_t beyond = (lay2->pool + 0x200000 + 0xffff) & ~(uint64_t)0xffff;
        CHECK(mem_map_physical(a, beyond) == 0x10801, "MapPhysicalMemory beyond the pool is not LimitReached");
    }
    mem_query(RT_HOSTFN_BASE + 0x40, &q);
    CHECK(q.state == 0x10 && q.base == RT_HOSTFN_BASE, "QueryMemory on the host-function window");
    mem_query(g->alias.base + g->alias.size + 0x1000, &q);
    CHECK(q.state == (RT_ENGINE_GAME ? 0 : 3) && q.perm == (RT_ENGINE_GAME ? 0 : 5) &&
          q.base == g->alias.base + g->alias.size, "QueryMemory on unmapped host memory");
    CHECK(mem_set_heap_size(0, &heap) == 0, "SetHeapSize 0");
    printf("memory: heap, physical memory, stack move, QueryMemory, GetInfo: %s\n", failures > before ? "FAIL" : "ok");
}

static int selftest(void) {
    static RtModule mods[MAX_MODULES];
    int n = rt_linked_modules(mods, MAX_MODULES);

    check_cpu();
    check_layout();
    uint64_t probe = hostfn_register("<selftest probe>", host_probe);

    double t0 = now_s();
    hostfn_seal();
    double dt = now_s() - t0;
    RtDispatchStats s = rt_dispatch_stats();
    printf("dispatch: %d modules linked, table covers %#llx-%#llx: %llu slots, %.1f MiB reserved, built in %.0f ms\n",
           n, (unsigned long long)s.lo, (unsigned long long)s.hi, (unsigned long long)s.slots,
           (double)s.table_bytes / (1 << 20), dt * 1e3);
    check_modules(mods, n);
    CHECK(s.functions > 0, "no functions linked");
    Cpu *c = plat_aligned_alloc(sizeof(Cpu), 64);
    memset(c, 0, sizeof *c);
    uint64_t r = rt_call(c, probe, 1, (uint64_t)7);
    CHECK(rt_dispatch_lookup(probe) && r == 42 && host_probe_hits == 1 && c->x[30] == 0,
          "a registered host function does not dispatch through rt_call");
    CHECK(!rt_dispatch_lookup(probe + 16), "an unregistered host-function address dispatches");
    CHECK(rt_dispatch_lookup(RT_RET_HOST), "the rt_call return sentinel is not registered");
    plat_aligned_free(c);
    bench(mods, n);

    void *k1 = mem_kernel_alloc(0x200, 16), *k2 = mem_kernel_alloc(0x2000, 0x1000);
    CHECK((uintptr_t)k1 - RT_KERNEL_BASE < RT_KERNEL_SIZE && !((uintptr_t)k2 & 0xFFF) && k1 != k2,
          "kernel-region allocation");
    mem_kernel_free(k1, 0x200);
    CHECK(mem_kernel_alloc(0x200, 16) == k1, "a freed kernel-region block is not reused");
    check_memory();

    float used, total, proc;
    plat_mem_info(&used, &total, &proc);
    if (proc >= 0) printf("process: working set %.0f MiB\n", proc);
    printf("selftest: %s (%d failures)\n", failures ? "FAILED" : "passed", failures);
    return failures ? 1 : 0;
}

// --probe-crash-call and --probe-crash-null crash on purpose, to check the report of a fault outside this executable.
// call: strlen in the C runtime library (ucrtbase.dll, libc.so.6) reads address 8, the way a driver faults inside a
// call the host made; the report names the library and the host frames that called it. null: a call through a null
// function pointer, which leaves rip at 0, outside every module. Both end with exit code 71 and a FATAL line in the
// log.
static size_t (*volatile probe_strlen)(const char *) = strlen;
static void (*volatile probe_null)(void);

static int probe_crash(int null) {
    if (null) probe_null();
    else printf("%zu\n", probe_strlen((const char *)(uintptr_t)8));
    printf("probe-crash: still running, the crash did not happen\n");
    return 1;
}

static int usage(const char *argv0) {
    fprintf(stderr, "usage: %s [--exefs DIR] [--romfs DIR] [--profile DIR] [--selftest | --probe-loader | "
                    "--probe-engage-input | --probe-audout | --probe-menu-font | --probe-cfg | --probe-dpi | --probe-kernel | --probe-ipc | --probe-services | --probe-fs | --probe-engage-fs | --probe-mods | --probe-gfx-abi | "
                    "--probe-nvn | --probe-nvn-tex | --probe-window | --probe-shader | --probe-vkmem | "
                    "--probe-vkexec | --probe-astc | --probe-movie | --probe-crash-call | --probe-crash-null | "
                    "--prepare-shaders | --warm-shaders | --boot |--seconds S [--shot PNG]]\n", argv0);
    return 2;
}

// Paths for a copied folder (tools/package_build.py). Per path: UO_EXEFS, UO_ROMFS, UO_PROFILE_DIR, else the
// folder next to uo.exe (exefs with main.npdm, romfs with romfs.bin, any profile folder) if it is there, else the
// path baked in at build time. A relative value is relative to uo.exe's folder.
static char pp_exefs[PLAT_PATH_MAX * 2], pp_romfs[PLAT_PATH_MAX * 2], pp_profile[PLAT_PATH_MAX * 2];

static const char *portable_path(const char *dir, const char *var, const char *sub, const char *must, char *out, size_t cap,
                                 const char *fallback) {
    const char *e = cfg_get(var);
    if (e && *e) {
        int abs_path = e[0] == '\\' || e[0] == '/' || e[1] == ':';
        if (abs_path) snprintf(out, cap, "%s", e);
        else snprintf(out, cap, "%s/%s", dir, e);
        return out;
    }
    char probe[PLAT_PATH_MAX * 3];
    snprintf(out, cap, "%s/%s", dir, sub);
    if (!must) return plat_dir_exists(out) ? out : fallback;   // the folder itself is the sign
    snprintf(probe, sizeof probe, "%s/%s", out, must);
    return plat_file_exists(probe) ? out : fallback;
}

static void portable_paths(RtConfig *c) {
    char exe[PLAT_PATH_MAX * 2];
    int n = plat_exe_path(exe, sizeof exe);
    char *cut = n && n < sizeof exe ? plat_last_sep(exe) : NULL;
    if (!cut) return;
    *cut = 0;
    c->exefs = portable_path(exe, "UO_EXEFS", "exefs", "main.npdm", pp_exefs, sizeof pp_exefs, c->exefs);
    c->romfs = portable_path(exe, "UO_ROMFS", "romfs", "romfs.bin", pp_romfs, sizeof pp_romfs, c->romfs);
    c->profile = portable_path(exe, "UO_PROFILE_DIR", "profile", NULL, pp_profile, sizeof pp_profile, c->profile);
}

int main(int argc, char **argv) {
#ifdef RT_GAME_LIBRARY
    // Package checks can validate the public runtime's font before the player's DLL exists.
    if (argc == 2 && !strcmp(argv[1], "--probe-menu-font")) {
        int ok = menu_font_check();
        printf("menu-font: %s\n", ok ? "passed" : "FAILED");
        return ok ? 0 : 1;
    }
    rt_game_library();
#endif
    mem_init();   // first: claim the guest address space before anything else can land there; reads uo.ini
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    plat_crash_hook(crash_handler);
    parse_trace();
    rt_bp_init();
    prof_start();
    // Timed guest waits (kernel.c block_inner) sleep in whole milliseconds; at the default 15.6 ms tick a 1 ms wait
    // costs a full tick. UO_TIMER_1MS=0 keeps the default for comparison.
    if (cfg_bool("UO_TIMER_1MS")) plat_timer_1ms();
    cfg = (RtConfig){.exefs = rt_default_exefs, .romfs = rt_default_romfs, .profile = rt_default_profile,
                     .gpu = cfg_get("UO_GPU")};
    portable_paths(&cfg);   // command-line paths below still win
    if (cfg.gpu && strcmp(cfg.gpu, "null") && strcmp(cfg.gpu, "vulkan")) {
        rt_logf("UO_GPU must be null or vulkan, not '%s'\n", cfg.gpu);
        return 2;
    }
    const char *mode = NULL;
    for (int i = 1; i < argc; i++) {
        const char **path = !strcmp(argv[i], "--exefs") ? &cfg.exefs : !strcmp(argv[i], "--romfs") ? &cfg.romfs
                          : !strcmp(argv[i], "--profile") ? &cfg.profile : NULL;
        if (path) {
            if (++i == argc) return usage(argv[0]);
            *path = argv[i];
        } else if (!strcmp(argv[i], "--seconds")) {
            char *end;
            if (++i == argc) return usage(argv[0]);
            double s = strtod(argv[i], &end);
            if (*end || !(s > 0) || s > 1e6) return usage(argv[0]);
            cfg.seconds = s;
        } else if (!strcmp(argv[i], "--shot")) {
            if (++i == argc) return usage(argv[0]);
            cfg.shot = argv[i];
        } else if (!mode && !strncmp(argv[i], "--", 2)) {
            mode = argv[i];
        } else {
            return usage(argv[0]);
        }
    }
    if (cfg.shot && !cfg.seconds) return usage(argv[0]);
    if (cfg.seconds && mode) return usage(argv[0]);
    log_open(!mode);
    if (!mode) {
        cfg.boot = cfg.seconds > 0;   // --seconds starts the game like --boot, with its checkpoints
        rt_boot();
        return 0;
    }
    if (!strcmp(mode, "--selftest")) return selftest();
    if (!strcmp(mode, "--probe-menu-font")) {
        int ok = menu_font_check();
        printf("menu-font: %s\n", ok ? "passed" : "FAILED");
        return ok ? 0 : 1;
    }
    if (!strcmp(mode, "--probe-loader")) return probe_loader();
    if (!strcmp(mode, "--probe-crash-call")) return probe_crash(0);
    if (!strcmp(mode, "--probe-crash-null")) return probe_crash(1);
    if (!strcmp(mode, "--probe-cfg")) return probe_cfg();
#ifdef _WIN32
    if (!strcmp(mode, "--probe-dpi")) return probe_dpi();   // the Win32 DPI awareness of uo.manifest
#endif
    if (!strcmp(mode, "--probe-kernel")) return probe_kernel();
    if (!strcmp(mode, "--probe-ipc")) return probe_ipc();
    if (!strcmp(mode, "--probe-audout")) return probe_audout();
    if (!strcmp(mode, "--probe-engage-input")) return probe_engage_input();
    if (!strcmp(mode, "--probe-services")) return probe_services();
    if (!strcmp(mode, "--probe-fs")) return probe_fs();
    if (!strcmp(mode, "--probe-engage-fs")) return probe_engage_fs();
    if (!strcmp(mode, "--probe-mods")) return probe_mods();
    if (!strcmp(mode, "--probe-gfx-abi")) return probe_gfx_abi();
    if (!strcmp(mode, "--probe-nvn")) return probe_nvn();
    if (!strcmp(mode, "--probe-nvn-tex")) return probe_nvn_tex();
    if (!strcmp(mode, "--probe-window")) return probe_window();
    if (!strcmp(mode, "--probe-shader")) return probe_shader();
    if (!strcmp(mode, "--probe-vkmem")) return probe_vkmem();
    if (!strcmp(mode, "--probe-vkexec")) return probe_vkexec();
    if (!strcmp(mode, "--probe-astc")) return probe_astc();
    if (!strcmp(mode, "--probe-vkdevice")) return probe_vkdevice();
    if (!strcmp(mode, "--probe-movie")) return probe_movie();
    if (!strcmp(mode, "--prepare-shaders")) return vk_pipeline_prepare_shaders();
    if (!strcmp(mode, "--warm-shaders")) return vk_pipeline_warm_shaders();
    if (!strcmp(mode, "--boot")) { cfg.boot = 1; rt_boot(); return 0; }
    return usage(argv[0]);
}
