// NVN procs the UE NVN RHI calls that the TotK-derived front end did not need. Everything here is a query (a value
// the game reads back from an object it filled in) or a hint the replay does not model, so each one is a few lines
// next to the slice that owns the object. Command-buffer procs are in nvn_api_cmd.c, where the record helpers live.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "gfx.h"
#include "host.h"
#include "nvn_api.h"
#include "nvn_obj.h"
#include "platform/platform.h"

// ---------- Device queries
// NVNboolean nvnDeviceGetDepthMode(const NVNdevice *d) / nvnDeviceGetWindowOriginMode
NVN_API(nvnDeviceGetDepthMode) { ret_x(c, (uint64_t)((const dev_t_ *)arg_p(c, 0))->depth_mode); }
NVN_API(nvnDeviceGetWindowOriginMode) { ret_x(c, (uint64_t)((const dev_t_ *)arg_p(c, 0))->origin); }
// void nvnDeviceGenerateDebugDomainId(const NVNdevice *d, const char *name, uint32_t *out): a stable id per name
NVN_API(nvnDeviceGenerateDebugDomainId) {
    const char *s = arg_p(c, 1);
    uint32_t h = 2166136261u;
    for (; s && *s; s++) h = (h ^ (uint8_t)*s) * 16777619u;
    uint32_t *out = arg_p(c, 2);
    if (out) *out = h;
}
// Debug and checkpoint knobs: no host counterpart, accepted and ignored.
NVN_API(nvnDeviceSetIntermediateShaderCache) { (void)c; }
NVN_API(nvnDeviceSetAutomaticCheckpointsEnable) { (void)c; }
NVN_API(nvnDeviceSetAutomaticCheckpointsByDebugGroup) { (void)c; }
NVN_API(nvnDeviceSetAutomaticCheckpointsSamplingInterval) { (void)c; }
NVN_API(nvnDeviceSetCheckpointCallStacksEnable) { (void)c; }
NVN_API(nvnDeviceSetCheckpointMaxCallStacksDepth) { (void)c; }
NVN_API(nvnDeviceWalkDebugDatabase) { (void)c; }

// ---------- Builder queries: the game reads back what it set
NVN_API(nvnDeviceBuilderGetFlags) { ret_x(c, ((const dev_builder_t *)arg_p(c, 0))->flags); }

NVN_API(nvnMemoryPoolBuilderGetDevice) { ret_x(c, (uintptr_t)((const pool_builder_t *)arg_p(c, 0))->dev); }
NVN_API(nvnMemoryPoolBuilderGetMemory) { ret_x(c, (uintptr_t)((const pool_builder_t *)arg_p(c, 0))->mem); }
NVN_API(nvnMemoryPoolBuilderGetSize) { ret_x(c, ((const pool_builder_t *)arg_p(c, 0))->size); }
NVN_API(nvnMemoryPoolBuilderGetFlags) { ret_x(c, ((const pool_builder_t *)arg_p(c, 0))->flags); }

NVN_API(nvnBufferBuilderGetDevice) { ret_x(c, (uintptr_t)((const buf_builder_t *)arg_p(c, 0))->dev); }
NVN_API(nvnBufferBuilderGetMemoryPool) { ret_x(c, (uintptr_t)((const buf_builder_t *)arg_p(c, 0))->pool); }
NVN_API(nvnBufferBuilderGetMemoryOffset) { ret_x(c, (uint64_t)((const buf_builder_t *)arg_p(c, 0))->off); }
NVN_API(nvnBufferBuilderGetSize) { ret_x(c, ((const buf_builder_t *)arg_p(c, 0))->size); }

NVN_API(nvnQueueBuilderGetDevice) { ret_x(c, (uintptr_t)((const queue_builder_t *)arg_p(c, 0))->dev); }
NVN_API(nvnQueueBuilderGetFlags) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->flags); }
NVN_API(nvnQueueBuilderGetMemory) { ret_x(c, (uintptr_t)((const queue_builder_t *)arg_p(c, 0))->mem); }
NVN_API(nvnQueueBuilderGetMemorySize) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->mem_sz); }
NVN_API(nvnQueueBuilderGetCommandMemorySize) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->cmd_sz); }
NVN_API(nvnQueueBuilderGetComputeMemorySize) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->comp_sz); }
NVN_API(nvnQueueBuilderGetControlMemorySize) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->ctl_sz); }
NVN_API(nvnQueueBuilderGetCommandFlushThreshold) { ret_x(c, ((const queue_builder_t *)arg_p(c, 0))->flush); }
// void nvnQueueBuilderSetQueuePriority(NVNqueueBuilder *b, NVNqueuePriority p): the host schedules the GPU
NVN_API(nvnQueueBuilderSetQueuePriority) { (void)c; }
NVN_API(nvnQueueBuilderGetQueuePriority) { ret_x(c, 0); }

// ---- Queue usage counters: the game sizes its next frame's command memory from them. The replay records commands in
// host memory and reports what the game added (cmd_t.hw_used) through GetCommandMemoryUsed; these totals only need to
// grow with the submits.
NVN_API(nvnQueueGetTotalCommandMemoryUsed) { ret_x(c, (uint64_t)((const queue_t *)arg_p(c, 0))->submits * 4096ull); }
NVN_API(nvnQueueGetTotalControlMemoryUsed) { ret_x(c, (uint64_t)((const queue_t *)arg_p(c, 0))->submits * 1024ull); }
NVN_API(nvnQueueGetTotalComputeMemoryUsed) { ret_x(c, 0); }
NVN_API(nvnQueueResetMemoryUsageCounts) { (void)c; }

// ---- Event / sync queries
NVN_API(nvnEventBuilderGetMemoryPool) { ret_x(c, (uintptr_t)((const ev_builder_t *)arg_p(c, 0))->pool); }
NVN_API(nvnEventBuilderGetMemoryOffset) { ret_x(c, (uint64_t)((const ev_builder_t *)arg_p(c, 0))->off); }
// void *nvnEventBuilderGetStorage(const NVNeventBuilder *b): the pool storage the value lives in
NVN_API(nvnEventBuilderGetStorage) {
    const ev_builder_t *b = arg_p(c, 0);
    ret_x(c, b->pool && b->pool->mem ? (uintptr_t)b->pool->mem + (uint64_t)b->off : 0);
}
// The event object itself does not keep its pool; the builder it came from does, and that is where the game asks.
NVN_API(nvnEventGetMemoryPool) { ret_x(c, 0); }
NVN_API(nvnEventGetMemoryOffset) { ret_x(c, 0); }

// ---------- State queries
NVN_API(nvnBlendStateGetAdvancedMode) { ret_x(c, 0); }
NVN_API(nvnBlendStateGetAdvancedNormalizedDst) { ret_x(c, 0); }
NVN_API(nvnBlendStateGetAdvancedOverlap) { ret_x(c, 0); }
NVN_API(nvnBlendStateGetAdvancedPremultipliedSrc) { ret_x(c, 0); }
NVN_API(nvnMultisampleStateGetAlphaToCoverageDither) { ret_x(c, ((const ms_st *)arg_p(c, 0))->a2c_dither); }
NVN_API(nvnMultisampleStateGetCoverageModulationMode) { ret_x(c, ((const ms_st *)arg_p(c, 0))->cov_mod); }
NVN_API(nvnMultisampleStateGetCoverageToColorEnable) { ret_x(c, ((const ms_st *)arg_p(c, 0))->cov2color_en); }
NVN_API(nvnMultisampleStateGetCoverageToColorOutput) { ret_x(c, ((const ms_st *)arg_p(c, 0))->cov2color_out); }
NVN_API(nvnMultisampleStateGetSampleLocationsEnable) { ret_x(c, ((const ms_st *)arg_p(c, 0))->sl_en); }
NVN_API(nvnMultisampleStateGetSampleLocationsGrid) { ret_x(c, 0); }
NVN_API(nvnMultisampleStateGetSampleLocationsGridEnable) { ret_x(c, ((const ms_st *)arg_p(c, 0))->sl_grid); }
// void nvnMultisampleStateSetCoverageModulationMode(NVNmultisampleState *s, NVNcoverageModulationMode mode)
NVN_API(nvnMultisampleStateSetCoverageModulationMode) { ((ms_st *)arg_p(c, 0))->cov_mod = (uint8_t)arg_x(c, 1); }
NVN_API(nvnMultisampleStateSetCoverageToColorEnable) { ((ms_st *)arg_p(c, 0))->cov2color_en = nvn_arg_bool(c, 1) != 0; }
NVN_API(nvnMultisampleStateSetCoverageToColorOutput) { ((ms_st *)arg_p(c, 0))->cov2color_out = (uint8_t)arg_x(c, 1); }
NVN_API(nvnMultisampleStateSetSampleLocationsGridEnable) { ((ms_st *)arg_p(c, 0))->sl_grid = nvn_arg_bool(c, 1) != 0; }
NVN_API(nvnProgramSetSampleShading) { (void)c; }
NVN_API(nvnProgramSetSubroutineLinkage) { (void)c; }

// ---------- Texture builder queries
NVN_API(nvnTextureBuilderGetPackagedTextureLayout) { (void)c; ret_x(c, 0); }
NVN_API(nvnTextureBuilderGetRawStorageClass) { (void)c; ret_x(c, 0); }
NVN_API(nvnTextureBuilderGetSparseTileLayout) { (void)c; }
NVN_API(nvnTextureGetSparseTileLayout) { (void)c; }
NVN_API(nvnSamplerPoolRegisterSamplerBuilder) {
    gfx_backend()->sampler_register((int)arg_x(c, 1), &((const samp_builder_t *)arg_p(c, 2))->p);
}

// ---------- GL / NVX interop: this port has no GL context and no NVX surface handles
NVN_API(nvnSyncCreateGLSync) { ret_x(c, 0); }
NVN_API(nvnSyncInitializeFromFencedGLSync) { ret_x(c, 0); }
NVN_API(nvnSyncGetNvRmSyncNVX) { ret_x(c, 0); }
NVN_API(nvnSyncInitializeFromNvRmSyncNVX) { ret_x(c, 0); }
NVN_API(nvnTextureGetNvRmSurfaceNVX) { ret_x(c, 0); }
NVN_API(nvnTextureBuilderSetGLTextureName) { (void)c; }
NVN_API(nvnTextureBuilderGetGLTextureName) { ret_x(c, 0); }
