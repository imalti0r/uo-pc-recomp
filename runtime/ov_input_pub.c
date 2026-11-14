// The public nn::hid API for the calls the UE engine makes that the input layer did not cover: the sdk's own
// implementations of these go through the hid service proxy, and this port has no hid service.
//
// Note on delegation: calling the detail:: counterpart through ldr_symbol() would run the SDK's own definition, not
// the host override registered for the same name (the loader's override replaces the symbol in every relocation, not in
// the module's own code). So each function here answers from the emulated controller directly, and the detail:: ones in
// ov_input.c stay the only place that touches the host pad.
#include <stdint.h>
#include <string.h>

#include "host.h"
#include "ov.h"
#include "platform/platform.h"

// ---- six-axis sensors: at rest, which is what a controller lying on a desk reports. SixAxisSensorState is 0x50
// bytes: { uint64_t sampling_number; float dir_x, dir_y, dir_z, angular_rate_x, angular_rate_y, angular_rate_z;
// float delta_angle_x, delta_angle_y, delta_angle_z; ... }. A zeroed state with unit gravity is "still".
typedef struct {
    uint64_t sampling_number;
    float dir[3], rate[3], delta_angle[3];
    float pad;
    uint8_t rest[0x50 - 8 - 36 - 4];
} SixAxisState;
_Static_assert(sizeof(SixAxisState) == 0x50, "nn::hid::SixAxisSensorState");

static int six_fill(SixAxisState *out, int count) {
    static uint64_t calls;
    uint64_t n = __atomic_add_fetch(&calls, 1, __ATOMIC_RELAXED);
    for (int i = 0; i < count; i++) {
        memset(&out[i], 0, sizeof out[i]);
        out[i].sampling_number = n;
        out[i].dir[1] = 1.0f;   // gravity on +Y: the sensor is level
    }
    return count;
}

// Result GetSixAxisSensorStates(SixAxisSensorState *out, int count, const SixAxisSensorHandle &handle)
static void ov_pub_six_states(Cpu *c) {
    SixAxisState *out = arg_p(c, 0);
    int count = (int32_t)arg_x(c, 1);
    if (!out || count <= 0) { ret_x(c, 0); return; }
    ret_x(c, (uint64_t)six_fill(out, count));
}

// Result StartSixAxisSensor / StopSixAxisSensor(const SixAxisSensorHandle &): the emulated sensor always runs
static void ov_six_start(Cpu *c) { (void)c; ret_x(c, 0); }
static void ov_six_stop(Cpu *c) { (void)c; ret_x(c, 0); }
// Result SetGyroscopeZeroDriftMode(const SixAxisSensorHandle &, const GyroscopeZeroDriftMode &): a calibration hint
static void ov_gyro_drift(Cpu *c) { (void)c; ret_x(c, 0); }

// ---- vibration
typedef struct { uint32_t type, position; } VibrationDeviceInfo;
// Result InitializeVibrationDevice(const VibrationDeviceHandle &): the device exists; the handle already names it
static void ov_pub_vib_init(Cpu *c) { (void)c; ret_x(c, 0); }
// Result GetVibrationDeviceInfo(VibrationDeviceInfo *out, const VibrationDeviceHandle &): type 1 (linear resonant
// actuator), position 1 left / 2 right from the handle's device byte
static void ov_pub_vib_info(Cpu *c) {
    VibrationDeviceInfo *out = arg_p(c, 0);
    const uint32_t *h = arg_p(c, 1);
    if (!out) { ret_x(c, 0x7d402u); return; }
    uint32_t dev = h ? (*h >> 16) & 0xff : 0;
    *out = (VibrationDeviceInfo){1, dev == 1 ? 2u : 1u};
    ret_x(c, 0);
}
// Result SendVibrationValue(const VibrationDeviceHandle &, const VibrationValue &): the detail:: path in ov_input.c
// drives SDL rumble, and it is reached through the detail:: symbol the game itself calls.
static void ov_pub_vib_send(Cpu *c) { (void)c; ret_x(c, 0); }
static void ov_pub_vib_send_many(Cpu *c) { (void)c; ret_x(c, 0); }

// ---- Npad state
// NpadJoyHoldType GetNpadJoyHoldType(): horizontal (0) is what a detached pair reports
static void ov_npad_hold(Cpu *c) { ret_x(c, 0); }
// Result GetMouseState(MouseState *out): MouseState is 40 bytes { int64 sampling; int32 x, y, dx, dy, wx, wy;
// uint32 buttons, attributes }. No mouse is connected, so attributes has no CONNECTED bit and every axis is 0.
static void ov_mouse_state(Cpu *c) {
    void *out = arg_p(c, 0);
    if (out) memset(out, 0, 40);
    ret_x(c, 0);
}

// ---- ControllerSupportArg::SetDefault: a plain struct the SDK fills with defaults; the UI that would show it is not
// ported, and the game only builds the argument before calling ShowControllerSupport.
static void ov_controller_support_defaults(Cpu *c) {
    uint8_t *arg = arg_p(c, 0);
    if (!arg) return;
    memset(arg, 0, 0x100);
    arg[0x00] = 1;              // hdr: enabled
    arg[0x01] = 4;              // hdr: player count min
    arg[0x02] = 8;              // hdr: player count max
    arg[0x0C] = 1;              // single player mode
    arg[0x10] = 1;              // explain text enabled
}

// ---- VibrationConverter: the constructor stores the sample rate, ProcessChunk turns a PCM chunk into one value.
// ChunkDuration is a static data member (a float object in the sdk's data), not a function, so it needs no override.
typedef struct { int sample_rate, pad; } VibConv;
static void ov_vibconv_init(Cpu *c) {
    VibConv *o = arg_p(c, 0);
    if (o) { o->sample_rate = (int32_t)arg_x(c, 1); o->pad = 0; }
}
static void ov_vibconv_fini(Cpu *c) { (void)c; }
// int ProcessChunk(VibrationValue *out, const int16_t *samples, int count): the low band follows the sample envelope
static void ov_vibconv_chunk(Cpu *c) {
    float *out = arg_p(c, 0);
    const int16_t *samples = arg_p(c, 1);
    int count = (int32_t)arg_x(c, 2);
    if (!out) { ret_x(c, 0); return; }
    float peak = 0;
    for (int i = 0; samples && i < count; i++) {
        float v = samples[i] < 0 ? -(float)samples[i] / 32768.0f : (float)samples[i] / 32768.0f;
        if (v > peak) peak = v;
    }
    out[0] = peak > 1 ? 1 : peak;   // amplitude low
    out[1] = 160;                   // frequency low
    out[2] = 0;                     // amplitude high
    out[3] = 320;                   // frequency high
    ret_x(c, 1);
}

HOST_OVERRIDE("_ZN2nn3hid22GetSixAxisSensorStatesEPNS0_18SixAxisSensorStateEiRKNS0_19SixAxisSensorHandleE",
              ov_pub_six_states);
HOST_OVERRIDE("_ZN2nn3hid18StartSixAxisSensorERKNS0_19SixAxisSensorHandleE", ov_six_start);
HOST_OVERRIDE("_ZN2nn3hid17StopSixAxisSensorERKNS0_19SixAxisSensorHandleE", ov_six_stop);
HOST_OVERRIDE("_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE",
              ov_gyro_drift);
HOST_OVERRIDE("_ZN2nn3hid25InitializeVibrationDeviceERKNS0_21VibrationDeviceHandleE", ov_pub_vib_init);
HOST_OVERRIDE("_ZN2nn3hid22GetVibrationDeviceInfoEPNS0_19VibrationDeviceInfoERKNS0_21VibrationDeviceHandleE",
              ov_pub_vib_info);
HOST_OVERRIDE("_ZN2nn3hid18SendVibrationValueERKNS0_21VibrationDeviceHandleERKNS0_14VibrationValueE", ov_pub_vib_send);
HOST_OVERRIDE("_ZN2nn3hid18GetNpadJoyHoldTypeEv", ov_npad_hold);
HOST_OVERRIDE("_ZN2nn3hid13GetMouseStateEPNS0_10MouseStateE", ov_mouse_state);
HOST_OVERRIDE("_ZN2nn3hid20ControllerSupportArg10SetDefaultEv", ov_controller_support_defaults);
HOST_OVERRIDE("_ZN2nn3hid18VibrationConverterC1Ei", ov_vibconv_init);
HOST_OVERRIDE("_ZN2nn3hid18VibrationConverterD1Ev", ov_vibconv_fini);
HOST_OVERRIDE("_ZN2nn3hid18VibrationConverter12ProcessChunkEPNS0_14VibrationValueEPKsi", ov_vibconv_chunk);
