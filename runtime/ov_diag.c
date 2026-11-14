// Diagnostics for the SDK's own fatal path.
//
// nn::diag::detail::Abort(const Result *) is what the SDK calls when a checked return value is a failure. The Break
// that follows only says "the guest aborted", so without this observer the failing call is invisible. This logs the
// Result code (module, description) and the caller, then lets the original abort run.
#include <stdint.h>

#include "host.h"
#include "cfg.h"
#include <windows.h>
#include <platform/platform.h>

// A Horizon Result: 0 means success; the module is in bits 9..17 and the description in bits 0..8.
static void obs_abort(Cpu *c) {
    const uint32_t *r = arg_p(c, 0);
    char at[128];
    uint32_t v = 0;
    if (r && plat_mem_readable((uint64_t)(uintptr_t)r, 4)) v = *r;
    rt_log("diag: Abort(%p) result %#x (module %u, description %u) from %s", (const void *)r, v, (v >> 9) & 0x1FF,
           v & 0x1FF, ldr_describe(c->x[30], at, sizeof at));
}

// VAbortImpl(...): argument layout unknown; dump the raw x0..x7 values and try the first three as strings.
static void obs_vabort(Cpu *c) {
    char at[128];
    char sbuf[3][140];
    for (int which = 0; which < 3; which++) {
        const char *s = arg_p(c, which);
        char *d = sbuf[which];
        snprintf(d, 30, "#%llx ", (unsigned long long)(uintptr_t)s);
        char *e = d + strlen(d);
        if (s && plat_mem_readable((uint64_t)(uintptr_t)s, 1)) {
            size_t k = 0;
            while (k < 99 && plat_mem_readable((uint64_t)(uintptr_t)s + k, 1) && s[k]) { e[k] = s[k]; k++; }
            e[k] = 0;
        } else {
            *e = 0;
        }
    }
    unsigned result = 0;
    if (c->x[4] && plat_mem_readable(c->x[4], 4)) memcpy(&result, (const void *)(uintptr_t)c->x[4], 4);
    rt_log("diag: VAbort x3=%llx x4=%llx *x4 result=%#x x7=%llx", (unsigned long long)c->x[3],
           (unsigned long long)c->x[4], result, (unsigned long long)c->x[7]);
    rt_log("diag: VAbort a0[%s] a1[%s] a2[%s] caller %s", sbuf[0], sbuf[1], sbuf[2],
           ldr_describe(c->x[30], at, sizeof at));
}

// UO_NOABORT=1: swallow the SDK's diag aborts instead of dying. The movie player aborts at its teardown
// when the present-paced frame release stalls; the guest continues fine otherwise. The override path only
// catches call-slot call sites, so also patch the two functions' first instruction to RET: the observer
// fires first, then the guest body (now a bare 'ret') returns to the caller instead of hitting its ud2.
static int no_abort = -1;

// The last host calls, for the abort log: which SDK call the guest made before it died.
#define RECENT 8
static struct { const char *name; uint64_t result; } recent[RECENT];
static volatile int recent_n;
void diag_note_hostcall(const char *name, uint64_t result) {
    (void)result;
    int i = plat_inc32(&recent_n) - 1;
    recent[i % RECENT].name = name;
}
static void dump_recent(void) {
    int n = recent_n < RECENT ? recent_n : RECENT;
    for (int k = 0; k < n; k++) {
        int i = (recent_n - 1 - k) % RECENT;
        if (recent[i].name) rt_log("diag: before the abort: %s -> %#llx", recent[i].name, (unsigned long long)recent[i].result);
    }
}
static void no_abort_check(void) {
    if (no_abort >= 0) return;
    const char *e = cfg_get("UO_NOABORT");
    no_abort = e && *e && *e != '0';
}
static void override_vabort(Cpu *c) {
    obs_vabort(c);
    dump_recent();
    no_abort_check();
}
HOST_OBSERVER("_ZN2nn4diag6detail5AbortEPKNS_6ResultE", override_vabort);
HOST_OBSERVER("_ZN2nn4diag6detail10VAbortImplEPKcS3_S3_iPKNS_6ResultEPKNS_2os17UserExceptionInfoES3_RSt9__va_list", override_vabort);
