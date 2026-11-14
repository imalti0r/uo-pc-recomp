// The console random-number service (nn::csrng). Unicorn Overlord reaches it through the SDK's crypto layer the
// first time it needs a seed, and the SDK's client asks the service for bytes rather than generating them itself.
//
// One command carries an output buffer: fill it with bytes from the host's random source. Any other command is a
// no-argument initialise/finalise and answers 0, which is what the client expects of them.
#include <stdint.h>
#include <string.h>

#include "host.h"
#include "ipc.h"
#include "platform/platform.h"

#define CSRNG_MAX (1u << 16)

static IpcBuffer out_buffer(const IpcRequest *rq) {
    if (rq->nb && rq->b[0].size) return rq->b[0];
    if (rq->nc && rq->c[0].size) return rq->c[0];
    if (rq->nx && rq->x[0].size) return rq->x[0];
    if (rq->na && rq->a[0].size) return rq->a[0];
    return (IpcBuffer){0};
}

static void csrng_handler(IpcSession *s, const IpcRequest *rq, IpcResponse *rs) {
    (void)s;
    IpcBuffer b = out_buffer(rq);
    if (!b.size) { rs->result = 0; return; }   // Initialise / Finalise: nothing to answer
    if (b.size > CSRNG_MAX) { rs->result = 0x2fcd1u; return; }
    void *dst = ipc_guest(b.addr, b.size);
    if (!dst) { rs->result = 0x2fcd1u; return; }
    if (!plat_random(dst, b.size)) { rs->result = 0x2fcd1u; return; }
    rs->result = 0;
}

IpcHandler csrng_service_handler(void) { return csrng_handler; }
