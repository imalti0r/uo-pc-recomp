// nn::socket over Winsock.
//
// The engine calls the BSD client directly (EventFd, Socket, Poll, ...), not only through a session, so a client
// that reported success without a session branched to address 0 inside its own event object. This file therefore
// serves the client itself: descriptors live in a small table of real Winsock sockets and Win32 events, so the
// engine's networking works the way it does on a console with a network, and fails the way it does with none
// (DNS, connect) instead of stopping the process.
//
// Horizon's constants match the BSD ones the engine was compiled against, so the enum values pass through where
// Windows agrees and are translated where it does not (option levels, EWOULDBLOCK, non-blocking I/O).
#include "host.h"
#include "platform/platform.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#define SOCK_MAX 512

// Horizon errno values the engine checks. They are the BSD numbers, not Windows'.
enum { E_HZ_AGAIN = 11, E_HZ_BADF = 9, E_HZ_INVAL = 22, E_HZ_NOMEM = 12, E_HZ_NOSYS = 78, E_HZ_CONNRESET = 104,
       E_HZ_CONNREFUSED = 111, E_HZ_TIMEDOUT = 110, E_HZ_INPROGRESS = 115, E_HZ_NETUNREACH = 101, E_HZ_NODEV = 19 };

// Horizon socket levels and options that differ from Winsock's numbering.
#define HZ_SOL_SOCKET 0xFFFF
#define HZ_SO_REUSEADDR 0x0004
#define HZ_SO_LINGER 0x0080
#define HZ_SO_KEEPALIVE 0x0008
#define HZ_SO_BROADCAST 0x0020
#define HZ_SO_SNDBUF 0x1001
#define HZ_SO_RCVBUF 0x1002
#define HZ_SO_SNDTIMEO 0x1005
#define HZ_SO_RCVTIMEO 0x1006

typedef struct {
    SOCKET sock;
    HANDLE event;
    int used, nonblock;
} HzFd;

static HzFd g_fd[SOCK_MAX];
static PlatMutex g_lock = PLAT_MUTEX_INIT;
static volatile int32_t g_wsa;
static volatile int32_t g_errno;

static void set_errno(int v) { plat_xchg32(&g_errno, v); }

static int wsa_error(void) {
    switch (WSAGetLastError()) {
    case WSAEWOULDBLOCK: return E_HZ_AGAIN;
    case WSAEINPROGRESS: return E_HZ_INPROGRESS;
    case WSAECONNRESET: return E_HZ_CONNRESET;
    case WSAECONNREFUSED: return E_HZ_CONNREFUSED;
    case WSAETIMEDOUT: return E_HZ_TIMEDOUT;
    case WSAENETUNREACH: return E_HZ_NETUNREACH;
    case WSAEBADF: case WSAENOTSOCK: return E_HZ_BADF;
    case WSAEINVAL: return E_HZ_INVAL;
    default: return E_HZ_INVAL;
    }
}

static HzFd *fd_get(int fd) {
    if (fd < 0 || fd >= SOCK_MAX || !g_fd[fd].used) return NULL;
    return &g_fd[fd];
}

static int fd_new(void) {
    for (int i = 0; i < SOCK_MAX; i++)
        if (!g_fd[i].used) {
            g_fd[i] = (HzFd){0};
            g_fd[i].used = 1;
            return i;
        }
    return -1;
}

static void ensure_wsa(void) {
    if (plat_cas32(&g_wsa, 0, 1)) {
        WSADATA d;
        if (WSAStartup(MAKEWORD(2, 2), &d)) rt_log("socket: WSAStartup failed");
    }
}

// ---- lifecycle
static void ov_socket_initialize(Cpu *c) {
    ensure_wsa();
    static volatile int32_t logged;
    if (!plat_xchg32(&logged, 1)) rt_log("socket: the BSD client is served by Winsock");
    ret_x(c, 0);
}

static void ov_socket_finalize(Cpu *c) { ret_x(c, 0); }

// ---- descriptors
// int Socket(int family, int type, int protocol)
static void ov_socket_socket(Cpu *c) {
    int family = (int32_t)arg_x(c, 0), type = (int32_t)arg_x(c, 1), proto = (int32_t)arg_x(c, 2);
    ensure_wsa();
    SOCKET s = socket(family, type, proto);
    if (s == INVALID_SOCKET) { set_errno(wsa_error()); ret_x(c, (uint64_t)-1); return; }
    plat_mutex_lock(&g_lock);
    int fd = fd_new();
    plat_mutex_unlock(&g_lock);
    if (fd < 0) { closesocket(s); set_errno(E_HZ_NOMEM); ret_x(c, (uint64_t)-1); return; }
    g_fd[fd].sock = s;
    ret_x(c, (uint64_t)fd);
}

// int Close(int fd)
static void ov_socket_close(Cpu *c) {
    int fd = (int32_t)arg_x(c, 0);
    plat_mutex_lock(&g_lock);
    HzFd *f = fd_get(fd);
    if (!f) { plat_mutex_unlock(&g_lock); set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    if (f->event) CloseHandle(f->event);
    if (f->sock) closesocket(f->sock);
    f->used = 0;
    plat_mutex_unlock(&g_lock);
    ret_x(c, 0);
}

// int EventFd(unsigned int init, EventFdFlags flags)
static void ov_socket_eventfd(Cpu *c) {
    plat_mutex_lock(&g_lock);
    int fd = fd_new();
    plat_mutex_unlock(&g_lock);
    if (fd < 0) { set_errno(E_HZ_NOMEM); ret_x(c, (uint64_t)-1); return; }
    g_fd[fd].event = CreateEventW(NULL, TRUE, arg_x(c, 0) ? TRUE : FALSE, NULL);
    if (!g_fd[fd].event) {
        plat_mutex_lock(&g_lock);
        g_fd[fd].used = 0;
        plat_mutex_unlock(&g_lock);
        set_errno(E_HZ_NOMEM);
        ret_x(c, (uint64_t)-1);
        return;
    }
    ret_x(c, (uint64_t)fd);
}

// int EventFdRead(int fd, uint64_t *out)
static void ov_socket_eventfd_read(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    uint64_t *out = arg_p(c, 1);
    if (!f || !f->event) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    if (out) *out = 1;
    ResetEvent(f->event);
    ret_x(c, 0);
}

// int EventFdWrite(int fd, uint64_t value)
static void ov_socket_eventfd_write(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f || !f->event) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    SetEvent(f->event);
    ret_x(c, 0);
}

// ---- connection setup
static void ov_socket_bind(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = bind(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2));
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_connect(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = connect(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2));
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_listen(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = listen(f->sock, (int)(int32_t)arg_x(c, 1));
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_accept(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int addrlen = (int32_t)arg_x(c, 2);
    SOCKET s = accept(f->sock, arg_p(c, 1), &addrlen);
    if (s == INVALID_SOCKET) { set_errno(wsa_error()); ret_x(c, (uint64_t)-1); return; }
    plat_mutex_lock(&g_lock);
    int fd = fd_new();
    plat_mutex_unlock(&g_lock);
    if (fd < 0) { closesocket(s); set_errno(E_HZ_NOMEM); ret_x(c, (uint64_t)-1); return; }
    g_fd[fd].sock = s;
    ret_x(c, (uint64_t)fd);
}

static void ov_socket_shutdown(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = shutdown(f->sock, (int)(int32_t)arg_x(c, 1));
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

// ---- data
static void ov_socket_send(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = send(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2), (int)(int32_t)arg_x(c, 3));
    if (r < 0) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_recv(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = recv(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2), (int)(int32_t)arg_x(c, 3));
    if (r < 0) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_sendto(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int r = sendto(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2), (int)(int32_t)arg_x(c, 3), arg_p(c, 4),
                   (int)(int32_t)arg_x(c, 5));
    if (r < 0) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_recvfrom(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int addrlen = (int32_t)arg_x(c, 5);
    int r = recvfrom(f->sock, arg_p(c, 1), (int)(int32_t)arg_x(c, 2), (int)(int32_t)arg_x(c, 3), arg_p(c, 4), &addrlen);
    if (r < 0) set_errno(wsa_error());
    int *outlen = arg_p(c, 5);
    if (outlen) *outlen = addrlen;
    ret_x(c, (uint64_t)(int64_t)r);
}

// ---- options
static int map_level(int level) { return level == HZ_SOL_SOCKET ? SOL_SOCKET : level; }

static int map_option(int level, int opt) {
    if (level != HZ_SOL_SOCKET) return opt;
    switch (opt) {
    case HZ_SO_REUSEADDR: return SO_REUSEADDR;
    case HZ_SO_LINGER: return SO_LINGER;
    case HZ_SO_KEEPALIVE: return SO_KEEPALIVE;
    case HZ_SO_BROADCAST: return SO_BROADCAST;
    case HZ_SO_SNDBUF: return SO_SNDBUF;
    case HZ_SO_RCVBUF: return SO_RCVBUF;
    case HZ_SO_SNDTIMEO: return SO_SNDTIMEO;
    case HZ_SO_RCVTIMEO: return SO_RCVTIMEO;
    default: return opt;
    }
}

static void ov_socket_setsockopt(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int level = (int32_t)arg_x(c, 1), opt = (int32_t)arg_x(c, 2);
    int r = setsockopt(f->sock, map_level(level), map_option(level, opt), arg_p(c, 3), (int)(int32_t)arg_x(c, 4));
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_getsockopt(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int level = (int32_t)arg_x(c, 1), opt = (int32_t)arg_x(c, 2);
    int *lenp = arg_p(c, 4);
    int len = lenp ? *lenp : 0;
    int r = getsockopt(f->sock, map_level(level), map_option(level, opt), arg_p(c, 3), &len);
    if (r) set_errno(wsa_error());
    int *outlen = arg_p(c, 4);
    if (outlen) *outlen = len;
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_getpeername(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int len = (int32_t)arg_x(c, 2);
    int r = getpeername(f->sock, arg_p(c, 1), &len);
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

static void ov_socket_getsockname(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int len = (int32_t)arg_x(c, 2);
    int r = getsockname(f->sock, arg_p(c, 1), &len);
    if (r) set_errno(wsa_error());
    ret_x(c, (uint64_t)(int64_t)r);
}

// int Fcntl(int fd, int cmd, ...): Horizon's F_SETFL with O_NONBLOCK (4)
static void ov_socket_fcntl(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    int cmd = (int32_t)arg_x(c, 1);
    if (cmd == 4 && f->sock) {   // F_SETFL
        u_long on = ((int32_t)arg_x(c, 2) & 4) ? 1 : 0;
        ioctlsocket(f->sock, FIONBIO, &on);
        f->nonblock = (int)on;
        ret_x(c, 0);
        return;
    }
    ret_x(c, 0);
}

static void ov_socket_ioctl(Cpu *c) {
    HzFd *f = fd_get((int32_t)arg_x(c, 0));
    if (!f) { set_errno(E_HZ_BADF); ret_x(c, (uint64_t)-1); return; }
    u_long n = 0;
    if (ioctlsocket(f->sock, FIONREAD, &n)) { set_errno(wsa_error()); ret_x(c, (uint64_t)-1); return; }
    int *outlen = arg_p(c, 2);
    if (outlen) *outlen = (int)n;
    ret_x(c, 0);
}

// ---- readiness
// int Poll(PollFd *fds, size_t nfds, int timeout): PollFd { int fd; short events; short revents; } = 8 bytes
static void ov_socket_poll(Cpu *c) {
    uint8_t *fds = arg_p(c, 0);
    size_t n = (size_t)arg_x(c, 1);
    int timeout = (int32_t)arg_x(c, 2);
    if (!fds || n > 256) { set_errno(E_HZ_INVAL); ret_x(c, (uint64_t)-1); return; }
    WSAPOLLFD w[256];
    int map[256];
    unsigned used = 0;
    for (size_t i = 0; i < n; i++) {
        int fd = *(int *)(fds + i * 8);
        short ev = *(short *)(fds + i * 8 + 4);
        *(short *)(fds + i * 8 + 6) = 0;
        HzFd *f = fd_get(fd);
        if (!f) continue;
        if (f->event) {   // an event object: poll is a wait
            DWORD r = WaitForSingleObject(f->event, timeout < 0 ? INFINITE : (DWORD)timeout);
            if (r == WAIT_OBJECT_0) {
                *(short *)(fds + i * 8 + 6) = 1;
                ret_x(c, 1);
                return;
            }
            continue;
        }
        w[used].fd = f->sock;
        w[used].events = (short)(ev & 0xFF);
        w[used].revents = 0;
        map[used] = (int)i;
        used++;
    }
    if (!used) { ret_x(c, 0); return; }
    int r = WSAPoll(w, used, timeout);
    if (r < 0) { set_errno(wsa_error()); ret_x(c, (uint64_t)-1); return; }
    for (unsigned i = 0; i < used; i++) *(short *)(fds + map[i] * 8 + 6) = w[i].revents;
    ret_x(c, (uint64_t)r);
}

// int Select(int nfds, fd_set *read, fd_set *write, fd_set *except, timeval *timeout)
static void ov_socket_select(Cpu *c) {
    fd_set *rd = arg_p(c, 1), *wr = arg_p(c, 2), *ex = arg_p(c, 3);
    struct timeval *tv = arg_p(c, 4);
    fd_set rr, ww, ee;
    FD_ZERO(&rr);
    FD_ZERO(&ww);
    FD_ZERO(&ee);
    int maxfd = -1;
    for (int set = 0; set < 3; set++) {
        fd_set *src = set == 0 ? rd : set == 1 ? wr : ex;
        if (!src) continue;
        for (u_int i = 0; i < src->fd_count; i++) {
            HzFd *f = fd_get((int)src->fd_array[i]);
            if (!f) continue;
            if (set == 0) FD_SET(f->sock, &rr);
            else if (set == 1) FD_SET(f->sock, &ww);
            else FD_SET(f->sock, &ee);
            if ((int)f->sock > maxfd) maxfd = (int)f->sock;
        }
    }
    struct timeval t = tv ? *tv : (struct timeval){0, 0};
    int r = select(maxfd + 1, rd ? &rr : NULL, wr ? &ww : NULL, ex ? &ee : NULL, tv ? &t : NULL);
    if (r < 0) { set_errno(wsa_error()); ret_x(c, (uint64_t)-1); return; }
    if (rd) *rd = rr;
    if (wr) *wr = ww;
    if (ex) *ex = ee;
    ret_x(c, (uint64_t)r);
}

// ---- errors and byte order
static void ov_socket_last_error(Cpu *c) { ret_x(c, (uint64_t)(uint32_t)plat_cas32(&g_errno, 0, 0)); }
static void ov_socket_htonl(Cpu *c) { ret_x(c, (uint64_t)htonl((u_long)arg_x(c, 0))); }
static void ov_socket_htons(Cpu *c) { ret_x(c, (uint64_t)htons((u_short)arg_x(c, 0))); }
static void ov_socket_ntohl(Cpu *c) { ret_x(c, (uint64_t)ntohl((u_long)arg_x(c, 0))); }
static void ov_socket_ntohs(Cpu *c) { ret_x(c, (uint64_t)ntohs((u_short)arg_x(c, 0))); }

// int InetPton(int family, const char *src, void *dst)
static void ov_socket_inet_pton(Cpu *c) {
    const char *src = arg_p(c, 1);
    ret_x(c, (uint64_t)(int64_t)(src ? InetPtonA((int)(int32_t)arg_x(c, 0), src, arg_p(c, 2)) : -1));
}

// ---- name resolution: no DNS on this port, so a lookup fails the way an offline console's does
static void ov_socket_getaddrinfo(Cpu *c) {
    static volatile int32_t logged;
    if (!plat_xchg32(&logged, 1)) rt_log("socket: name resolution is not available on this port");
    set_errno(E_HZ_NODEV);
    ret_x(c, (uint64_t)(int64_t)E_HZ_NODEV);
}

static void ov_socket_freeaddrinfo(Cpu *c) { (void)c; }

static void ov_socket_getnameinfo(Cpu *c) {
    ret_x(c, (uint64_t)(int64_t)E_HZ_NODEV);
}

// ---- misc
static void ov_socket_is_initialized(Cpu *c) { ret_x(c, 1); }
static void ov_socket_ok0(Cpu *c) { ret_x(c, 0); }
static void ov_socket_ok1(Cpu *c) { ret_x(c, 1); }
static void ov_socket_set_error(Cpu *c) { set_errno((int)(int32_t)arg_x(c, 0)); ret_x(c, 0); }

static void *socket_alloc(uint64_t n) {
    void *p = calloc(1, (size_t)(n ? n : 1));
    return p;
}
static void ov_socket_alloc(Cpu *c) { ret_x(c, (uint64_t)(uintptr_t)socket_alloc(arg_x(c, 0))); }
static void ov_socket_calloc(Cpu *c) { ret_x(c, (uint64_t)(uintptr_t)socket_alloc(arg_x(c, 0) * arg_x(c, 1))); }
static void ov_socket_free(Cpu *c) { free((void *)(uintptr_t)arg_x(c, 0)); }
static void ov_socket_strdup(Cpu *c) {
    const char *s = arg_p(c, 0);
    size_t n = s ? strlen(s) + 1 : 1;
    char *p = socket_alloc(n);
    if (p && s) memcpy(p, s, n);
    ret_x(c, (uint64_t)(uintptr_t)p);
}

// EventFd without a flags argument: a manually reset event object, the shape both layers expect
static void ov_socket_eventfd_plain(Cpu *c) {
    plat_mutex_lock(&g_lock);
    int fd = fd_new();
    plat_mutex_unlock(&g_lock);
    if (fd < 0) { set_errno(E_HZ_NOMEM); ret_x(c, (uint64_t)-1); return; }
    g_fd[fd].event = CreateEventW(NULL, TRUE, FALSE, NULL);
    ret_x(c, (uint64_t)fd);
}

static void ov_socket_eventfd_trigger(Cpu *c) { ov_socket_eventfd_plain(c); }

// The public nn::socket wrappers in the sdk are thin: each converts its Horizon-shaped arguments and calls the
// detail:: function, which is the IPC client. Overriding only the public names therefore leaves the detail layer
// talking to a session that does not exist (that is what aborted inside detail::Poll). The detail layer is the one
// served here; the public wrappers then work through it, and the public names are overridden as well for the calls
// the engine makes to them directly.
HOST_OVERRIDE("_ZN2nn6socket6detail10InitializeEPvmmi", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail10InitializeERKNS0_6ConfigE", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail10InitializeEi", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail10InitializeEPiii", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail8FinalizeEv", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail8FinalizeEi", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6detail13IsInitializedEv", ov_socket_is_initialized);
HOST_OVERRIDE("_ZN2nn6socket6detail6SocketEiii", ov_socket_socket);
HOST_OVERRIDE("_ZN2nn6socket6detail6SocketENS0_6FamilyENS0_4TypeENS0_8ProtocolE", ov_socket_socket);
HOST_OVERRIDE("_ZN2nn6socket6detail5CloseEi", ov_socket_close);
HOST_OVERRIDE("_ZN2nn6socket6detail7EventFdEmi", ov_socket_eventfd);
HOST_OVERRIDE("_ZN2nn6socket6detail11NoSfEventFdEv", ov_socket_eventfd_plain);
HOST_OVERRIDE("_ZN2nn6socket6detail18NoSfEventFdTriggerEi", ov_socket_eventfd_trigger);
HOST_OVERRIDE("_ZN2nn6socket18NoSfEventFdTriggerEi", ov_socket_eventfd_trigger);
HOST_OVERRIDE("_ZN2nn6socket11NoSfEventFdEv", ov_socket_eventfd_plain);
HOST_OVERRIDE("_ZN2nn6socket6detail4BindEiPK8sockaddrj", ov_socket_bind);
HOST_OVERRIDE("_ZN2nn6socket6detail4BindEiPKNS0_8SockAddrEj", ov_socket_bind);
HOST_OVERRIDE("_ZN2nn6socket6detail7ConnectEiPK8sockaddrj", ov_socket_connect);
HOST_OVERRIDE("_ZN2nn6socket6detail7ConnectEiPKNS0_8SockAddrEj", ov_socket_connect);
HOST_OVERRIDE("_ZN2nn6socket6detail6ListenEii", ov_socket_listen);
HOST_OVERRIDE("_ZN2nn6socket6detail6AcceptEiP8sockaddrPj", ov_socket_accept);
HOST_OVERRIDE("_ZN2nn6socket6detail6AcceptEiPNS0_8SockAddrEPj", ov_socket_accept);
HOST_OVERRIDE("_ZN2nn6socket6detail8ShutdownEii", ov_socket_shutdown);
HOST_OVERRIDE("_ZN2nn6socket6detail8ShutdownEiNS0_14ShutdownMethodE", ov_socket_shutdown);
HOST_OVERRIDE("_ZN2nn6socket6detail4SendEiPKvmi", ov_socket_send);
HOST_OVERRIDE("_ZN2nn6socket6detail4SendEiPKvmNS0_7MsgFlagE", ov_socket_send);
HOST_OVERRIDE("_ZN2nn6socket6detail5WriteEiPKvm", ov_socket_send);
HOST_OVERRIDE("_ZN2nn6socket6detail4RecvEiPvmi", ov_socket_recv);
HOST_OVERRIDE("_ZN2nn6socket6detail4RecvEiPvmNS0_7MsgFlagE", ov_socket_recv);
HOST_OVERRIDE("_ZN2nn6socket6detail4ReadEiPvm", ov_socket_recv);
HOST_OVERRIDE("_ZN2nn6socket6detail6SendToEiPKvmiPK8sockaddrj", ov_socket_sendto);
HOST_OVERRIDE("_ZN2nn6socket6detail8RecvFromEiPvmiP8sockaddrPj", ov_socket_recvfrom);
HOST_OVERRIDE("_ZN2nn6socket6detail10SetSockOptEiiiPKvj", ov_socket_setsockopt);
HOST_OVERRIDE("_ZN2nn6socket6detail10SetSockOptEiNS0_5LevelENS0_6OptionEPKvj", ov_socket_setsockopt);
HOST_OVERRIDE("_ZN2nn6socket6detail10GetSockOptEiiiPvPj", ov_socket_getsockopt);
HOST_OVERRIDE("_ZN2nn6socket6detail10GetSockOptEiNS0_5LevelENS0_6OptionEPvPj", ov_socket_getsockopt);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetPeerNameEiP8sockaddrPj", ov_socket_getpeername);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetPeerNameEiPNS0_8SockAddrEPj", ov_socket_getpeername);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetSockNameEiP8sockaddrPj", ov_socket_getsockname);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetSockNameEiPNS0_8SockAddrEPj", ov_socket_getsockname);
HOST_OVERRIDE("_ZN2nn6socket6detail5FcntlEiiz", ov_socket_fcntl);
HOST_OVERRIDE("_ZN2nn6socket6detail5FcntlEiNS0_12FcntlCommandEz", ov_socket_fcntl);
HOST_OVERRIDE("_ZN2nn6socket6detail5IoctlEijPvm", ov_socket_ioctl);
HOST_OVERRIDE("_ZN2nn6socket6detail4PollEP6pollfdmi", ov_socket_poll);
HOST_OVERRIDE("_ZN2nn6socket6detail4PollEPNS0_6PollFdEmi", ov_socket_poll);
HOST_OVERRIDE("_ZN2nn6socket6detail6SelectEiP6fd_setS3_S3_P7timeval", ov_socket_select);
HOST_OVERRIDE("_ZN2nn6socket6detail6SelectEiPNS0_5FdSetES3_S3_PNS0_7TimeValE", ov_socket_select);
HOST_OVERRIDE("_ZN2nn6socket6detail12GetLastErrorEv", ov_socket_last_error);
HOST_OVERRIDE("_ZN2nn6socket6detail12GetLastErrnoEv", ov_socket_last_error);
HOST_OVERRIDE("_ZN2nn6socket6detail12SetLastErrorENS0_5ErrnoE", ov_socket_set_error);
HOST_OVERRIDE("_ZN2nn6socket6detail12SetLastErrnoEi", ov_socket_set_error);
HOST_OVERRIDE("_ZN2nn6socket6detail9InetHtonlEj", ov_socket_htonl);
HOST_OVERRIDE("_ZN2nn6socket6detail9InetHtonsEt", ov_socket_htons);
HOST_OVERRIDE("_ZN2nn6socket6detail9InetNtohlEj", ov_socket_ntohl);
HOST_OVERRIDE("_ZN2nn6socket6detail9InetNtohsEt", ov_socket_ntohs);
HOST_OVERRIDE("_ZN2nn6socket6detail8InetPtonEiPKcPv", ov_socket_inet_pton);
HOST_OVERRIDE("_ZN2nn6socket6detail8InetPtonENS0_6FamilyEPKcPv", ov_socket_inet_pton);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetAddrInfoEPKcS3_PK8addrinfoPPS4_PKNS0_14ResolverOptionEm", ov_socket_getaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetAddrInfoEPKcS3_PKNS0_8AddrInfoEPPS4_PKNS0_14ResolverOptionEm", ov_socket_getaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail12FreeAddrInfoEP8addrinfo", ov_socket_freeaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail12FreeAddrInfoEPNS0_8AddrInfoE", ov_socket_freeaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetNameInfoEPK8sockaddrjPcjS5_jiPKNS0_14ResolverOptionEm", ov_socket_getnameinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail11GetNameInfoEPKNS0_8SockAddrEjPcmS5_mNS0_12NameInfoFlagEPKNS0_14ResolverOptionEm",
              ov_socket_getnameinfo);
HOST_OVERRIDE("_ZN2nn6socket6detail5AllocEm", ov_socket_alloc);
HOST_OVERRIDE("_ZN2nn6socket6detail6CallocEmm", ov_socket_calloc);
HOST_OVERRIDE("_ZN2nn6socket6detail4FreeEPv", ov_socket_free);
HOST_OVERRIDE("_ZN2nn6socket6detail6StrdupEPKc", ov_socket_strdup);
HOST_OVERRIDE("_ZN2nn6socket6detail21InitializeHandleTable27AcquireHandleForConcurrencyEi", ov_socket_ok0);
HOST_OVERRIDE("_ZN2nn6socket6detail21InitializeHandleTable30GetConcurrencyAndReleaseHandleEi", ov_socket_ok0);
HOST_OVERRIDE("_ZN2nn6socket6detail21SetClientSessionCountEi", ov_socket_ok0);
HOST_OVERRIDE("_ZN2nn6socket6detail20AllocatorIsAvailableEi", ov_socket_ok1);

HOST_OVERRIDE("_ZN2nn6socket10InitializeEPvmmi", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket10InitializeERKNS0_6ConfigE", ov_socket_initialize);
HOST_OVERRIDE("_ZN2nn6socket6SocketEiii", ov_socket_socket);
HOST_OVERRIDE("_ZN2nn6socket6SocketENS0_6FamilyENS0_4TypeENS0_8ProtocolE", ov_socket_socket);
HOST_OVERRIDE("_ZN2nn6socket5CloseEi", ov_socket_close);
HOST_OVERRIDE("_ZN2nn6socket7EventFdEmNS0_12EventFdFlagsE", ov_socket_eventfd);
HOST_OVERRIDE("_ZN2nn6socket11EventFdReadEiPm", ov_socket_eventfd_read);
HOST_OVERRIDE("_ZN2nn6socket12EventFdWriteEim", ov_socket_eventfd_write);
HOST_OVERRIDE("_ZN2nn6socket4BindEiPK8sockaddrj", ov_socket_bind);
HOST_OVERRIDE("_ZN2nn6socket7ConnectEiPK8sockaddrj", ov_socket_connect);
HOST_OVERRIDE("_ZN2nn6socket7ConnectEiPKNS0_8SockAddrEj", ov_socket_connect);
HOST_OVERRIDE("_ZN2nn6socket6ListenEii", ov_socket_listen);
HOST_OVERRIDE("_ZN2nn6socket6AcceptEiP8sockaddrPj", ov_socket_accept);
HOST_OVERRIDE("_ZN2nn6socket8ShutdownEii", ov_socket_shutdown);
HOST_OVERRIDE("_ZN2nn6socket4SendEiPKvmi", ov_socket_send);
HOST_OVERRIDE("_ZN2nn6socket4SendEiPKvmNS0_7MsgFlagE", ov_socket_send);
HOST_OVERRIDE("_ZN2nn6socket4RecvEiPvmi", ov_socket_recv);
HOST_OVERRIDE("_ZN2nn6socket4RecvEiPvmNS0_7MsgFlagE", ov_socket_recv);
HOST_OVERRIDE("_ZN2nn6socket6SendToEiPKvmiPK8sockaddrj", ov_socket_sendto);
HOST_OVERRIDE("_ZN2nn6socket8RecvFromEiPvmiP8sockaddrPj", ov_socket_recvfrom);
HOST_OVERRIDE("_ZN2nn6socket10SetSockOptEiiiPKvj", ov_socket_setsockopt);
HOST_OVERRIDE("_ZN2nn6socket10SetSockOptEiNS0_5LevelENS0_6OptionEPKvj", ov_socket_setsockopt);
HOST_OVERRIDE("_ZN2nn6socket10GetSockOptEiiiPvPj", ov_socket_getsockopt);
HOST_OVERRIDE("_ZN2nn6socket10GetSockOptEiNS0_5LevelENS0_6OptionEPvPj", ov_socket_getsockopt);
HOST_OVERRIDE("_ZN2nn6socket11GetPeerNameEiP8sockaddrPj", ov_socket_getpeername);
HOST_OVERRIDE("_ZN2nn6socket11GetSockNameEiP8sockaddrPj", ov_socket_getsockname);
HOST_OVERRIDE("_ZN2nn6socket5FcntlEiiz", ov_socket_fcntl);
HOST_OVERRIDE("_ZN2nn6socket5FcntlEiNS0_12FcntlCommandEz", ov_socket_fcntl);
HOST_OVERRIDE("_ZN2nn6socket5IoctlEijPvm", ov_socket_ioctl);
HOST_OVERRIDE("_ZN2nn6socket4PollEPNS0_6PollFdEmi", ov_socket_poll);
HOST_OVERRIDE("_ZN2nn6socket6SelectEiP6fd_setS2_S2_P7timeval", ov_socket_select);
HOST_OVERRIDE("_ZN2nn6socket12GetLastErrorEv", ov_socket_last_error);
HOST_OVERRIDE("_ZN2nn6socket9InetHtonlEj", ov_socket_htonl);
HOST_OVERRIDE("_ZN2nn6socket9InetHtonsEt", ov_socket_htons);
HOST_OVERRIDE("_ZN2nn6socket9InetNtohlEj", ov_socket_ntohl);
HOST_OVERRIDE("_ZN2nn6socket9InetNtohsEt", ov_socket_ntohs);
HOST_OVERRIDE("_ZN2nn6socket8InetPtonEiPKcPv", ov_socket_inet_pton);
HOST_OVERRIDE("_ZN2nn6socket11GetAddrInfoEPKcS2_PK8addrinfoPPS3_", ov_socket_getaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket11GetAddrInfoEPKcS2_PKNS0_8AddrInfoEPPS3_", ov_socket_getaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket12FreeAddrInfoEP8addrinfo", ov_socket_freeaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket12FreeAddrInfoEPNS0_8AddrInfoE", ov_socket_freeaddrinfo);
HOST_OVERRIDE("_ZN2nn6socket11GetNameInfoEPK8sockaddrjPcjS4_ji", ov_socket_getnameinfo);
