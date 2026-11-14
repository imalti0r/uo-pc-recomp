// nn::os thread-stack aliasing.
//
// SDK 20.x creates guest threads through ThreadManagerImplByHorizon, which maps each new stack a second time at a
// random address (StackGuardManager::MapAtRandomAddress) and gives the thread that alias as its SP, so a stack overflow
// lands on a guard page instead of another thread's data. That path needs the SDK's own address-space allocator, which
// reserves and maps regions through SVCs this host does not model for it, so it reported failure and the SDK aborted in
// diag (ThreadManager::CreateThread branches to diag::AbortImpl when MapAliasStack returns false).
//
// A host process already gets its stacks from the OS with guard pages of its own, and a guest thread's memory is the
// memory the game allocated. So the alias is the mapping itself: the thread runs on the stack the game made, which is
// what every other part of this runtime already assumes (kern_svc CreateThread takes the game's stack top directly).
#include <stdint.h>

#include "host.h"
#include "platform/platform.h"

// bool MapAliasStack(void **out, const void *src, size_t size) const: true = mapped. A const member function, so x0 is
// the object and the three arguments follow.
static void ov_map_alias_stack(Cpu *c) {
    void **out = arg_p(c, 1);
    const void *src = arg_p(c, 2);
    uint64_t size = arg_x(c, 3);
    static volatile int32_t logged;
    if (!plat_xchg32(&logged, 1))
        rt_log("os: MapAliasStack(%p, %#llx) from %s", src, (unsigned long long)size,
               ldr_describe(c->x[30], (char[96]){0}, 96));
    if (!out || !src || !size) { ret_x(c, 0); return; }
    *out = (void *)src;
    ret_x(c, 1);
}

// bool UnmapAliasStack(const void *alias, const void *src, size_t size) const: nothing was mapped twice.
static void ov_unmap_alias_stack(Cpu *c) { (void)c; ret_x(c, 1); }

// Observation only: log what the SDK's thread layer is given and what it answered, so the thread bring-up can be
// matched to the kernel. The observer runs the original itself (rt_original).
static void obs_create_thread(Cpu *c) {
    uint64_t thread = arg_x(c, 0), stack = arg_x(c, 3);
    int64_t prio = (int32_t)arg_x(c, 5);
    rt_original(c);
    rt_log("os: CreateThread(thread %#llx, entry %s, stack %#llx, prio %lld) -> result %#x handle %#llx", 
           (unsigned long long)thread, ldr_describe(arg_x(c, 1), (char[96]){0}, 96), (unsigned long long)stack,
           (long long)prio, (unsigned)c->x[0], (unsigned long long)c->x[1]);
}
// StartThread: the SDK's own entry, which reads the handle CreateThread stored in the thread object.
static void obs_start_thread(Cpu *c) {
    uint64_t thread = arg_x(c, 0);
    rt_original(c);
    rt_log("os: StartThread(thread %#llx) -> result %#x", (unsigned long long)thread, (unsigned)c->x[0]);
}
HOST_OBSERVER("_ZN2nn2os11StartThreadEPNS0_10ThreadTypeE", obs_start_thread);
HOST_OBSERVER("_ZN2nn2os12CreateThreadEPNS0_10ThreadTypeEPFvPvES3_S3_mi", obs_create_thread);
HOST_OVERRIDE("_ZNK2nn2os6detail26ThreadManagerImplByHorizon13MapAliasStackEPPvPKvm", ov_map_alias_stack);
HOST_OVERRIDE("_ZNK2nn2os6detail26ThreadManagerImplByHorizon15UnmapAliasStackEPKvS4_m", ov_unmap_alias_stack);
