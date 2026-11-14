// This port runs offline. Network management initializes locally and reports that no connection is
// available; the socket client lives in ov_socket.c, which serves it over Winsock.
#include "host.h"

static void ov_network_initialize(Cpu *c) { ret_x(c, 0); }
static void ov_network_available(Cpu *c) { ret_x(c, 0); }

HOST_OVERRIDE("_ZN2nn4nifm10InitializeEv", ov_network_initialize);
HOST_OVERRIDE("_ZN2nn4nifm18IsNetworkAvailableEv", ov_network_available);
HOST_OVERRIDE("nnsslInitialize", ov_network_initialize);
HOST_OVERRIDE("nnsslFinalize", ov_network_initialize);
HOST_OVERRIDE("_ZN2nn3ssl10InitializeEv", ov_network_initialize);
HOST_OVERRIDE("_ZN2nn3ssl8FinalizeEv", ov_network_initialize);
