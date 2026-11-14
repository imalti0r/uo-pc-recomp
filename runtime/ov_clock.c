// The guest clock.
//
// A game measures elapsed time as (GetSystemTick() - t0) / GetSystemTickFrequency(). The counter is CTM (CNTVCT), which
// the lifter turns into rt_ticks(), and rt_ticks() is scaled to 19.2 MHz, the console's CNTFRQ. So the frequency the SDK
// reports has to be that same 19.2 MHz, and the counter has to advance at that rate on the host too: either being wrong
// scales every delta the game computes, and it then runs its animation, its physics and its timeouts at that ratio.
#include <stdint.h>

#include "cfg.h"
#include "host.h"
#include "platform/platform.h"

#define CNTFRQ_HZ 19200000ull

// uint64_t nn::os::GetSystemTickFrequency(): 19.2 MHz, the rate rt_ticks() counts at.
static void ov_tick_frequency(Cpu *c) { ret_x(c, CNTFRQ_HZ); }

// UO_CLOCK_DIAG=1: the rate rt_ticks really advances at against the host's wall clock, once a second. A guest clock
// that runs fast is the first thing to rule out when a game's animation plays too quickly or its waits expire at once.
static void clock_diag(void) {
    static int on = -1;
    if (on < 0) { const char *e = cfg_value("UO_CLOCK_DIAG"); on = e && *e && strcmp(e, "0"); }
    if (!on) return;
    static uint64_t last_tick, last_ns;
    static int lines;
    uint64_t t = rt_ticks(), ns = kern_now_ns();
    if (!last_ns) { last_tick = t, last_ns = ns; return; }
    if (ns - last_ns < 1000000000ull || lines >= 8) return;
    lines++;
    double guest_hz = (double)(t - last_tick) * 1e9 / (double)(ns - last_ns);
    rt_log("clock: rt_ticks advances at %.3f MHz (nominal %.3f MHz, ratio %.4f)", guest_hz / 1e6,
           (double)CNTFRQ_HZ / 1e6, guest_hz / (double)CNTFRQ_HZ);
    last_tick = t, last_ns = ns;
}

// Every present is a convenient once-a-frame hook for the diagnostic.
void clock_diag_frame(void) { clock_diag(); }

HOST_OVERRIDE("_ZN2nn2os22GetSystemTickFrequencyEv", ov_tick_frequency);
HOST_OVERRIDE("nnosGetSystemTickFrequency", ov_tick_frequency);
