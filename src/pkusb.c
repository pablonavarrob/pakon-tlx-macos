/* pkusb.dll -- gives the OEM's TLB.dll a \\.\Pakon135 device under Wine.
 *
 * TLB.dll reaches the scanner through exactly one handle and five calls.  This
 * DLL intercepts those five in TLB.dll's import table and forwards them to a
 * Python server (pakonusb.py) that owns libusb and enforces the device safety
 * rules.  Everything else -- threads, COM, the CRT, PakonImau, MFC, and the
 * window itself -- is real Wine, which is the whole point.
 *
 * It is loaded because TLB.dll's VERSION.dll import was renamed to pkusb.dll:
 * that import is only three version-info functions used for one log line, so
 * we re-export them as stubs and get DllMain called early for free.  No PE
 * surgery, no injection.
 *
 * Semantics were established by emulating TLB.dll first, and two of them are
 * counter-intuitive:
 *   - DeviceIoControl must complete SYNCHRONOUSLY (return TRUE with the data
 *     already in the output buffer).  Returning FALSE/ERROR_IO_PENDING, which
 *     is what FILE_FLAG_OVERLAPPED would normally imply, makes TLB.dll log
 *     EC_WIN_DeviceIoControl 997 and abort.
 *   - It must still signal OVERLAPPED.hEvent and leave the byte count in
 *     InternalHigh, because the caller then waits on that event and collects
 *     the count with GetOverlappedResult.
 *
 * i686-w64-mingw32-gcc -shared -O2 -o pkusb.dll pkusb.c -lws2_32 -Wl,--kill-at
 */
#include <winsock2.h>   /* before windows.h, or mingw warns and hides sockets */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define PORT 5140
#define PK_HANDLE ((HANDLE)0xD0D0D000)
#define READ_EP6  0xFFFFFFFFu        /* pseudo-code for a ReadFile on the device */
/* The image read buffer is NOT all pixels: its first page is a header.  The
 * request is exactly 4096 + 8 MiB, and TLB.dll indexes samples from
 * ringbase+0x1000 (seen live: ESI=0x04400000, data pointer 0x04401000).  Writing
 * image data from offset 0 overwrote that header every run -- the fields it
 * reads back then held raw samples (0x0298029b) and every ring counter was 0. */
#define RING_HDR  4096
/* Ring control block, laid out by TLB.dll's own allocator at 0x10028af0 and
 * read back by its consumer check at 0x1002f020.  Not guessed -- every offset
 * below appears in both.  TLB zeroes the block, sets the sizes and the data
 * pointer, and then expects the DRIVER to advance Writing as bytes land while
 * it advances Reading as it consumes them. */
#define HDR_MAGIC   0x00    /* = 0x38, the header size */
#define HDR_TOTAL   0x04
#define HDR_RINGSZ  0x0c
#define HDR_READING 0x14    /* consumer position, TLB owns it */
#define HDR_WRITING 0x1c    /* driver position, WE own it */
#define HDR_PKTSZ   0x24    /* element size, from allocator arg3 */
#define HDR_TRIGGER 0x28    /* threshold in PACKETS; the app publishes its demand */
#define HDR_EVENT   0x2c    /* HANDLE EventScanPacketReady -- WE signal it */
#define HDR_STOP    0x30    /* byte: app asks us to stop */
#define HDR_INPROG  0x31    /* byte: DRIVER-owned "transfer in progress" */
#define HDR_OVERFLW 0x32    /* byte: DRIVER-owned overflow flag */
#define HDR_DATAPTR 0x34    /* = base + 0x1000 */

static SOCKET g_sock = INVALID_SOCKET;          /* command channel */
static SOCKET g_imgsock = INVALID_SOCKET;       /* image stream, its own pipe */
static CRITICAL_SECTION g_lock, g_imglock;
static DWORD g_last_count = 0;
static LPOVERLAPPED g_pending = NULL;           /* the image read in flight */
static volatile LONG g_cancel = 0;              /* CancelIo stops the ring fill */
#define RING_CHUNK 262144                       /* bytes fetched per iteration */
#define RING_BURST 8                            /* packets fetched per iteration */

/* real functions, captured before we overwrite the thunks */
static HANDLE (WINAPI *real_CreateFileW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
static BOOL (WINAPI *real_DeviceIoControl)(HANDLE, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
static BOOL (WINAPI *real_ReadFile)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
static BOOL (WINAPI *real_WriteFile)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
static BOOL (WINAPI *real_GetOverlappedResult)(HANDLE, LPOVERLAPPED, LPDWORD, BOOL);
static BOOL (WINAPI *real_CloseHandle)(HANDLE);
static BOOL (WINAPI *real_CancelIo)(HANDLE);
static BOOL (WINAPI *real_CancelIoEx)(HANDLE, LPOVERLAPPED);

static void logf_(const char *fmt, ...) {
    char buf[512];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    OutputDebugStringA(buf);
}

/* ---------------- transport ---------------- */
static int sock_connect_to(SOCKET *ps) {
    WSADATA w;
    struct sockaddr_in a;
    static int inited = 0;
    if (!inited) { WSAStartup(MAKEWORD(2, 2), &w); inited = 1; }
    *ps = socket(AF_INET, SOCK_STREAM, 0);
    if (*ps == INVALID_SOCKET) return 0;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(PORT);
    a.sin_addr.s_addr = htonl(0x7F000001);       /* 127.0.0.1 */
    if (connect(*ps, (struct sockaddr *)&a, sizeof a) != 0) {
        closesocket(*ps); *ps = INVALID_SOCKET; return 0;
    }
    return 1;
}

static int sock_connect(void) { return sock_connect_to(&g_sock); }

static int xfer_all_on(SOCKET s, char *p, int n, int recving) {
    int done = 0, r;
    while (done < n) {
        r = recving ? recv(s, p + done, n - done, 0)
                    : send(s, p + done, n - done, 0);
        if (r <= 0) return 0;
        done += r;
    }
    return 1;
}

/* One request/response on an explicit socket. */
static int pk_call_on(SOCKET *ps, CRITICAL_SECTION *lk, DWORD code,
                      const void *in, DWORD inlen, void *out, DWORD outsz,
                      const void *odata, DWORD odlen) {
    DWORD hdr[4], rep[2];
    int ok = -1;
    EnterCriticalSection(lk);
    if (*ps == INVALID_SOCKET && !sock_connect_to(ps)) { LeaveCriticalSection(lk); return -1; }
    hdr[0] = code; hdr[1] = outsz; hdr[2] = inlen; hdr[3] = odlen;
    if (!xfer_all_on(*ps, (char *)hdr, sizeof hdr, 0)) goto out;
    if (inlen && !xfer_all_on(*ps, (char *)in, inlen, 0)) goto out;
    if (odlen && !xfer_all_on(*ps, (char *)odata, odlen, 0)) goto out;
    if (!xfer_all_on(*ps, (char *)rep, sizeof rep, 1)) goto out;
    if (rep[0] == 0) { ok = -1; goto out; }
    if (rep[1] > outsz) { ok = -1; goto out; }
    if (rep[1] && !xfer_all_on(*ps, (char *)out, rep[1], 1)) goto out;
    ok = (int)rep[1];
out:
    if (ok < 0 && *ps != INVALID_SOCKET) { closesocket(*ps); *ps = INVALID_SOCKET; }
    LeaveCriticalSection(lk);
    return ok;
}

/* Commands go on the command socket. */
static int pk_call(DWORD code, const void *in, DWORD inlen,
                   void *out, DWORD outsz, const void *odata, DWORD odlen) {
    return pk_call_on(&g_sock, &g_lock, code, in, inlen, out, outsz, odata, odlen);
}

/* ---------------- the five interceptions ---------------- */
static HANDLE WINAPI my_CreateFileW(LPCWSTR name, DWORD acc, DWORD share,
                                    LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                    DWORD flags, HANDLE tmpl) {
    HANDLE h;
    if (name && wcsstr(name, L"Pakon135")) {
        if (g_sock == INVALID_SOCKET && !sock_connect()) {
            logf_("pkusb: no server on 127.0.0.1:%d\n", PORT);
            SetLastError(ERROR_FILE_NOT_FOUND);
            return INVALID_HANDLE_VALUE;
        }
        logf_("pkusb: opened Pakon device via socket\n");
        return PK_HANDLE;
    }
    h = real_CreateFileW(name, acc, share, sa, disp, flags, tmpl);
    /* TLB calls _wfopen for its logs and NEVER checks the result: a failed
     * open becomes fwprintf(NULL) -> _lock_file(NULL) -> read of 0x34 -> the
     * page fault we keep hitting at the end of every scan.  So if a log open
     * fails, put the file somewhere that works instead of letting TLB die. */
    if (h == INVALID_HANDLE_VALUE && name && wcsstr(name, L"Logs")) {
        WCHAR alt[MAX_PATH];
        const WCHAR *base = name, *p2;
        DWORD err = GetLastError();
        for (p2 = name; *p2; p2++) if (*p2 == L'\\' || *p2 == L'/') base = p2 + 1;
        if (GetModuleFileNameW(GetModuleHandleA("TLB.dll"), alt, MAX_PATH)) {
            WCHAR *cut = alt, *q;
            for (q = alt; *q; q++) if (*q == L'\\') cut = q;
            *cut = 0;
            if (lstrlenW(alt) + 7 + lstrlenW(base) < MAX_PATH) {
                lstrcatW(alt, L"\\Logs\\");
                lstrcatW(alt, base);
                h = real_CreateFileW(alt, acc, share, sa, disp, flags, tmpl);
                logf_("pkusb: log open failed (err %lu) -- redirected to install Logs, %s\n",
                      err, h == INVALID_HANDLE_VALUE ? "STILL FAILING" : "ok");
            }
        }
        if (h == INVALID_HANDLE_VALUE) SetLastError(err);
    }
    if (h == INVALID_HANDLE_VALUE && name && wcsstr(name, L"Logs"))
        logf_("pkusb: CreateFileW FAILED on a Logs path (err %lu)\n", GetLastError());
    return h;
}

/* Signal OVERLAPPED.hEvent and publish the count, the way the driver would. */
static void complete_ovl(LPOVERLAPPED o, DWORD n) {
    g_last_count = n;
    if (!o) return;
    o->Internal = 0;
    o->InternalHigh = n;
    if (g_pending == o) g_pending = NULL;
    if (o->hEvent) SetEvent(o->hEvent);
}

static BOOL WINAPI my_DeviceIoControl(HANDLE h, DWORD code, LPVOID in, DWORD inlen,
                                      LPVOID out, DWORD outsz, LPDWORD ret,
                                      LPOVERLAPPED ovl) {
    int n;
    if (h != PK_HANDLE)
        return real_DeviceIoControl(h, code, in, inlen, out, outsz, ret, ovl);
    n = pk_call(code, in, inlen, out, outsz, out, outsz);
    if (n < 0) { SetLastError(ERROR_INVALID_FUNCTION); return FALSE; }
    if (ret) *ret = (DWORD)n;
    complete_ovl(ovl, (DWORD)n);
    return TRUE;                    /* synchronous on purpose -- see header */
}

struct rdreq { LPVOID buf; DWORD n; LPOVERLAPPED ovl; };

static DWORD WINAPI read_worker(LPVOID p) {
    struct rdreq *r = (struct rdreq *)p;
    DWORD hdr[4], rep[2];
    BYTE *base = (BYTE *)r->buf;
    DWORD total = 0;
    DWORD ringsz, magic, npkt, pktsz;
    DWORD iter = 0, lastread = 0xFFFFFFFF;
    BYTE *data;
    volatile DWORD *reading, *writing;
    volatile BYTE *stopflag, *inprog, *overflow;
    volatile DWORD *thresh;
    HANDLE hpkt = NULL;

    magic  = *(DWORD *)(base + HDR_MAGIC);
    ringsz = *(DWORD *)(base + HDR_RINGSZ);
    data   = *(BYTE **)(base + HDR_DATAPTR);
    reading  = (volatile DWORD *)(base + HDR_READING);
    writing  = (volatile DWORD *)(base + HDR_WRITING);
    thresh   = (volatile DWORD *)(base + HDR_TRIGGER);
    hpkt     = *(HANDLE *)(base + HDR_EVENT);
    stopflag = (volatile BYTE *)(base + HDR_STOP);
    inprog   = (volatile BYTE *)(base + HDR_INPROG);
    overflow = (volatile BYTE *)(base + HDR_OVERFLW);
    npkt  = *(DWORD *)(base + HDR_RINGSZ);
    pktsz = *(DWORD *)(base + HDR_PKTSZ);
    if (magic != 0x38 || data != base + RING_HDR || !npkt || !pktsz
        || (size_t)npkt * pktsz > r->n - RING_HDR) {
        logf_("pkusb: ring header unexpected (magic %x ringsz %u data %p base %p)"
              " -- falling back to plain fill\n", magic, ringsz, data, base);
        data = base + RING_HDR;
        npkt = 1; pktsz = r->n - RING_HDR;
        reading = writing = NULL;
    }
    {   /* dump the whole control block once: the element size and threshold
           come from here, they are not assumed */
        DWORD *h = (DWORD *)base;
        logf_("pkusb: hdr %08x %08x %08x %08x %08x %08x %08x\n",
              h[0], h[1], h[2], h[3], h[4], h[5], h[6]);
        logf_("pkusb: hdr %08x %08x %08x %08x %08x %08x %08x\n",
              h[7], h[8], h[9], h[10], h[11], h[12], h[13]);
        logf_("pkusb: readfile n=%u dataarea=%u npkt(hdr0c)=%u\n",
              r->n, r->n - RING_HDR, ringsz);
    }

    EnterCriticalSection(&g_imglock);
    if (g_imgsock == INVALID_SOCKET && !sock_connect_to(&g_imgsock)) goto out;

    /* The driver owns three fields in this header, and TLB.dll blocks on all
     * three.  uiGetCorrections (0x1001cea0) opens with
     *     while (!ring->TransferInProgress(+0x31) && !(stop & 2)) Sleep(1);
     * so until +0x31 is set it spins forever with no error and no timeout --
     * that is the silent "Corrections" hang.  uiGetScanLines then waits on the
     * event handle stored at +0x2c (EventScanPacketReady), NOT on the
     * OVERLAPPED event, which means "scan finished".  And +0x1c (Writing) is
     * the producer head, in packets. */
    if (inprog) *inprog = 1;
    if (overflow) *overflow = 0;
    logf_("pkusb: ring npkt=%u pktsz=%u thresh=%u hEvent=%p -- TransferInProgress=1\n",
          npkt, pktsz, thresh ? *thresh : 0, hpkt);

    while (!g_cancel) {
        DWORD w = writing ? *writing : 0;
        DWORD rd = reading ? *reading : 0;
        DWORD used = (w >= rd) ? (w - rd) : (npkt - rd + w);
        DWORD freep = (npkt - 1) - used;            /* never fill the last slot */
        DWORD contig = npkt - w;
        DWORD n, want, got_total = 0;
        if (stopflag && *stopflag) {                /* the app asked us to stop */
            logf_("pkusb: StopTransfer set by app, ending ring fill\n");
            break;
        }
        if (!freep) {                               /* consumer has not caught up */
            if (overflow) *overflow = 1;
            Sleep(1);
            continue;
        }
        if (overflow) *overflow = 0;
        n = freep < contig ? freep : contig;
        if (n > RING_BURST) n = RING_BURST;
        want = n * pktsz;
        hdr[0] = READ_EP6; hdr[1] = want; hdr[2] = 0; hdr[3] = 0;
        if (!xfer_all_on(g_imgsock, (char *)hdr, sizeof hdr, 0)) goto out;
        if (!xfer_all_on(g_imgsock, (char *)rep, sizeof rep, 1)) goto out;
        if (rep[0] == 0 || rep[1] > want) goto out;
        while (got_total < rep[1]) {
            int got = recv(g_imgsock, (char *)data + (size_t)w * pktsz + got_total,
                           (int)(rep[1] - got_total), 0);
            if (got <= 0) goto out;
            got_total += (DWORD)got;
        }
        if (got_total < pktsz) { Sleep(1); continue; }
        n = got_total / pktsz;                      /* publish whole packets only */
        total += n * pktsz;
        w += n;
        if (w >= npkt) w -= npkt;
        if (writing) *writing = w;                  /* producer head, in packets */
        if (hpkt) SetEvent(hpkt);                   /* EventScanPacketReady */
        r->ovl->InternalHigh = total;
        if (++iter <= 5 || (iter % 64) == 0 || rd != lastread) {
            logf_("pkusb: ring it=%u W=%u R=%u used=%u free=%u +%upkt total=%u\n",
                  iter, w, rd, used, freep, n, total);
            lastread = rd;
        }
    }
out:
    if (g_imgsock != INVALID_SOCKET) { closesocket(g_imgsock); g_imgsock = INVALID_SOCKET; }
    if (inprog) *inprog = 0;       /* transfer really has ended now */
    LeaveCriticalSection(&g_imglock);
    complete_ovl(r->ovl, total);   /* only now: cancelled == scan over */
    free(r);
    return 0;
}

/* The image pipe is ASYNCHRONOUS in the real driver: it returns
 * ERROR_IO_PENDING and completes later.  Serving it synchronously blocks the
 * OEM's state machine for the seconds an 8 MB read takes, which is what
 * overflowed its ring (EC_DRV_RingTailOverflow 1002).  It also gets its own
 * socket, so a long image read cannot stall the command channel. */
static BOOL WINAPI my_ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ovl) {
    int r;
    if (h != PK_HANDLE) return real_ReadFile(h, buf, n, got, ovl);
    if (ovl) {
        struct rdreq *req = (struct rdreq *)malloc(sizeof *req);
        if (!req) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return FALSE; }
        req->buf = buf; req->n = n; req->ovl = ovl;
        ovl->Internal = 0x103;              /* STATUS_PENDING */
        ovl->InternalHigh = 0;
        g_pending = ovl;
        g_cancel = 0;
        if (ovl->hEvent) ResetEvent(ovl->hEvent);
        if (!CreateThread(NULL, 0, read_worker, req, 0, NULL)) {
            free(req); SetLastError(ERROR_INVALID_FUNCTION); return FALSE;
        }
        SetLastError(ERROR_IO_PENDING);
        return FALSE;
    }
    r = pk_call_on(&g_imgsock, &g_imglock, READ_EP6, NULL, 0, buf, n, NULL, 0);
    if (r < 0) { SetLastError(ERROR_INVALID_FUNCTION); return FALSE; }
    if (got) *got = (DWORD)r;
    return TRUE;
}

static BOOL WINAPI my_WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD put, LPOVERLAPPED ovl) {
    if (h != PK_HANDLE) return real_WriteFile(h, buf, n, put, ovl);
    /* The real driver never assigns a write pipe: WriteFile on this device
       always fails.  Reproduce that rather than inventing behaviour. */
    SetLastError(ERROR_INVALID_FUNCTION);
    return FALSE;
}

/* While the read is still running this MUST report failure with
 * ERROR_IO_INCOMPLETE.  Returning TRUE with the partial count instead tells the
 * caller the transfer finished having delivered almost nothing, so it derives
 * zero complete lines -- which is precisely EC_DRV_RingTailOverflow (1002), and
 * leaves the ring counters at 0 for the code that later faults on them. */
static BOOL WINAPI my_GetOverlappedResult(HANDLE h, LPOVERLAPPED ovl, LPDWORD n, BOOL wait) {
    if (h != PK_HANDLE) return real_GetOverlappedResult(h, ovl, n, wait);
    if (!ovl) { if (n) *n = g_last_count; return TRUE; }
    if (ovl->Internal == 0x103) {                    /* STATUS_PENDING */
        if (!wait) {
            if (n) *n = (DWORD)ovl->InternalHigh;
            SetLastError(ERROR_IO_INCOMPLETE);
            return FALSE;
        }
        if (ovl->hEvent) WaitForSingleObject(ovl->hEvent, 30000);
    }
    if (n) *n = (DWORD)ovl->InternalHigh;
    return TRUE;
}

/* TLB.dll calls CancelIo on the device handle to tear down a pending image
 * read.  Unpatched it reached the real kernel32 with our synthetic handle and
 * returned "Invalid handle", after which the OEM waited for a cancellation
 * that never came and reported EC_TimeOut. */
static BOOL WINAPI my_CancelIo(HANDLE h) {
    if (h == PK_HANDLE) {
        /* Retire the pending read for real.  Now that GetOverlappedResult
           blocks while STATUS_PENDING is set, leaving it set here would hang
           the caller instead of tearing the transfer down. */
        g_cancel = 1;                 /* stops the ring-fill loop */
        return TRUE;
    }
    return real_CancelIo ? real_CancelIo(h) : TRUE;
}

static BOOL WINAPI my_CancelIoEx(HANDLE h, LPOVERLAPPED o) {
    if (h == PK_HANDLE) { g_cancel = 1; return TRUE; }
    return real_CancelIoEx ? real_CancelIoEx(h, o) : TRUE;
}

static BOOL WINAPI my_CloseHandle(HANDLE h) {
    if (h == PK_HANDLE) return TRUE;
    return real_CloseHandle(h);
}


/* ---------------- TLB.dll internal error tracing ----------------
 * TLB reports EVERY internal error through one thiscall at RVA 0x1acd0:
 *     void __thiscall Report(void *this, int classId, int fnId, int errCode,
 *                            unsigned extra, const wchar_t *extraStr, int noAccum)
 * 834 call sites.  Most never reach a dialog -- bGetErrors only hands the
 * client the accumulated text at the end -- so hooking this is the only way to
 * watch the failure as it happens.  We log the numeric triple and let the
 * Python side put names to it (pknames.py), which keeps the tables editable.
 *
 * Prologue is `push -1; push 0x10059d10` = 7 bytes of position-independent
 * code, so it relocates into a trampoline safely.
 */
void *g_tramp;                           /* original 7 bytes + jmp back
                                            (non-static: the asm stub needs the symbol) */
static int   g_errhook_on;

void pk_on_error(void *thisp, DWORD *args)
{
    (void)thisp;
    /* args[0]=classId args[1]=fnId args[2]=errCode args[3]=extra */
    logf_("TLBERR cls=%d fn=%d ec=%d extra=%u\n",
          (int)args[0], (int)args[1], (int)args[2], args[3]);
}

extern void pk_err_stub(void);
__asm__(
".text\n"
".globl _pk_err_stub\n"
"_pk_err_stub:\n"
"    pushal\n"
"    pushfl\n"
"    leal 40(%esp), %eax\n"      /* &args[0] */
"    pushl %eax\n"
"    pushl %ecx\n"               /* this */
"    call _pk_on_error\n"
"    addl $8, %esp\n"
"    popfl\n"
"    popal\n"
"    jmp *_g_tramp\n"
);

/* Find TLB's error reporter without knowing its address.
 *
 * It is called from ~834 sites -- far more than anything else -- and begins with
 * the MSVC SEH prologue `push -1; push <scopetable>`.  So: count the targets of
 * every direct `call rel32` in .text, and take the most-called function whose
 * first bytes are 6A FF 68.  That is build-independent; the previous hardcoded
 * RVA only matched one particular TLB.dll.
 *
 * PAKON_ERRHOOK_RVA=<hex> overrides the search if you already know the offset.
 */
static BYTE *find_reporter(HMODULE tlb)
{
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)tlb;
    IMAGE_NT_HEADERS *nt;
    IMAGE_SECTION_HEADER *sec;
    BYTE *base = (BYTE *)tlb, *text = NULL;
    DWORD i, textsz = 0, n;
    /* bounded tally: (target, count) pairs, keeping the busiest */
    struct { DWORD off; DWORD n; } top[64];
    DWORD ntop = 0, bestn = 0; DWORD bestoff = 0;
    char envbuf[16];

    if (GetEnvironmentVariableA("PAKON_ERRHOOK_RVA", envbuf, sizeof envbuf)) {
        DWORD rva = (DWORD)strtoul(envbuf, NULL, 16);
        if (rva) { logf_("pkusb: errhook RVA from environment: 0x%lx\n", rva);
                   return base + rva; }
    }
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    sec = IMAGE_FIRST_SECTION(nt);
    for (i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        if (!memcmp(sec->Name, ".text", 5)) {
            text = base + sec->VirtualAddress;
            textsz = sec->Misc.VirtualSize;
            break;
        }
    }
    if (!text || textsz < 0x1000) return NULL;

    for (i = 0; i + 5 <= textsz; i++) {
        BYTE *p = text + i;
        LONG rel;
        BYTE *tgt;
        if (*p != 0xE8) continue;
        rel = *(LONG *)(p + 1);
        tgt = p + 5 + rel;
        if (tgt < text || tgt >= text + textsz) continue;
        if (tgt[0] != 0x6A || tgt[1] != 0xFF || tgt[2] != 0x68) continue;  /* SEH prologue */
        {
            DWORD off = (DWORD)(tgt - base), k, found = 0;
            for (k = 0; k < ntop; k++)
                if (top[k].off == off) { top[k].n++; found = 1;
                                         if (top[k].n > bestn) { bestn = top[k].n; bestoff = off; }
                                         break; }
            if (!found && ntop < 64) { top[ntop].off = off; top[ntop].n = 1; ntop++; }
        }
    }
    if (bestn < 50) {          /* the reporter has hundreds of callers */
        logf_("pkusb: errhook: no clear reporter found (best %lu calls) -- not hooking\n",
              bestn);
        return NULL;
    }
    n = bestn;
    logf_("pkusb: errhook: reporter at RVA 0x%lx (%lu call sites)\n", bestoff, n);
    return base + bestoff;
}

static void install_errhook(HMODULE tlb)
{
    BYTE *fn, *tr;
    DWORD old;
    if (g_errhook_on || !tlb) return;
    /* Opt-in only.  This patches OEM code in memory. */
    {
        char v[8] = {0};
        if (!GetEnvironmentVariableA("PAKON_ERRHOOK", v, sizeof v) || v[0] != '1') {
            logf_("pkusb: errhook disabled (set PAKON_ERRHOOK=1 to trace TLB errors)\n");
            g_errhook_on = 1;      /* don't re-check every watcher tick */
            return;
        }
    }
    fn = find_reporter(tlb);
    if (!fn) { g_errhook_on = 1; return; }
    if (fn[0] != 0x6a || fn[1] != 0xff || fn[2] != 0x68) {
        logf_("pkusb: errhook: prologue mismatch (%02x %02x %02x), not hooking\n",
              fn[0], fn[1], fn[2]);
        g_errhook_on = 1;
        return;
    }
    tr = (BYTE *)VirtualAlloc(NULL, 64, MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    if (!tr) return;
    memcpy(tr, fn, 7);                       /* the relocated prologue */
    tr[7] = 0xE9;                            /* jmp back to fn+7 */
    *(LONG *)(tr + 8) = (LONG)((fn + 7) - (tr + 12));
    g_tramp = tr;
    if (!VirtualProtect(fn, 7, PAGE_EXECUTE_READWRITE, &old)) return;
    fn[0] = 0xE9;
    *(LONG *)(fn + 1) = (LONG)((BYTE *)pk_err_stub - (fn + 5));
    fn[5] = 0x90; fn[6] = 0x90;
    VirtualProtect(fn, 7, old, &old);
    g_errhook_on = 1;
    logf_("pkusb: errhook installed at %p (trampoline %p)\n", fn, tr);
}

/* ---------------- IAT patching ---------------- */
static int patch_one(HMODULE mod, const char *want, void *repl, void **saved) {
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)mod;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((char *)mod + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR *imp;
    if (!rva) return 0;
    for (imp = (IMAGE_IMPORT_DESCRIPTOR *)((char *)mod + rva); imp->Name; imp++) {
        IMAGE_THUNK_DATA *orig = (IMAGE_THUNK_DATA *)((char *)mod + imp->OriginalFirstThunk);
        IMAGE_THUNK_DATA *iat  = (IMAGE_THUNK_DATA *)((char *)mod + imp->FirstThunk);
        if (!imp->OriginalFirstThunk) orig = iat;
        for (; orig->u1.AddressOfData; orig++, iat++) {
            IMAGE_IMPORT_BY_NAME *n;
            DWORD old;
            if (orig->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            n = (IMAGE_IMPORT_BY_NAME *)((char *)mod + orig->u1.AddressOfData);
            if (lstrcmpA((char *)n->Name, want)) continue;
            if (saved) *saved = (void *)iat->u1.Function;
            if (VirtualProtect(&iat->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) {
                iat->u1.Function = (ULONGLONG)(ULONG_PTR)repl;
                VirtualProtect(&iat->u1.Function, sizeof(void *), old, &old);
                return 1;
            }
            return 0;
        }
    }
    return 0;
}

/* tlx.dll opens \\.\Pakon135 itself as a presence check before it ever loads
   TLB.dll through COM, so both need patching -- and TLB.dll appears LATER than
   our DllMain.  install() is therefore idempotent and gets called from DllMain,
   from the re-exported stubs, and from a short-lived watcher thread. */
static const char *TARGETS[] = { "tlx.dll", "TLB.dll", "TLA.dll", "TLC.dll" };
static HMODULE g_done[8];

static int already(HMODULE m) {
    unsigned i;
    for (i = 0; i < sizeof g_done / sizeof g_done[0]; i++) {
        if (g_done[i] == m) return 1;
        if (!g_done[i]) { g_done[i] = m; return 0; }
    }
    return 1;
}

static void install(void) {
    unsigned i;
    for (i = 0; i < sizeof TARGETS / sizeof TARGETS[0]; i++) {
        HMODULE m = GetModuleHandleA(TARGETS[i]);
        int n = 0;
        if (!m || already(m)) continue;
        n += patch_one(m, "CreateFileW", my_CreateFileW, (void **)&real_CreateFileW);
        n += patch_one(m, "CreateFileA", my_CreateFileW, NULL);   /* rare, same test */
        n += patch_one(m, "DeviceIoControl", my_DeviceIoControl, (void **)&real_DeviceIoControl);
        n += patch_one(m, "ReadFile", my_ReadFile, (void **)&real_ReadFile);
        n += patch_one(m, "WriteFile", my_WriteFile, (void **)&real_WriteFile);
        n += patch_one(m, "GetOverlappedResult", my_GetOverlappedResult, (void **)&real_GetOverlappedResult);
        n += patch_one(m, "CloseHandle", my_CloseHandle, (void **)&real_CloseHandle);
        n += patch_one(m, "CancelIo", my_CancelIo, (void **)&real_CancelIo);
        n += patch_one(m, "CancelIoEx", my_CancelIoEx, (void **)&real_CancelIoEx);
        logf_("pkusb: patched %d device imports in %s\n", n, TARGETS[i]);
        if (!lstrcmpiA(TARGETS[i], "TLB.dll")) install_errhook(m);
    }
}

static DWORD WINAPI watcher(LPVOID p) {
    int i;
    (void)p;
    for (i = 0; i < 600; i++) {           /* ~60 s, then stop looking */
        install();
        Sleep(100);
    }
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        InitializeCriticalSection(&g_lock);
        InitializeCriticalSection(&g_imglock);
        install();
        CreateThread(NULL, 0, watcher, NULL, 0, NULL);
    }
    return TRUE;
}

/* Re-exported so the renamed VERSION.dll imports still resolve.  Only used for
   a version string in the OEM's log, so stubs are fine -- and calling install()
   here catches a module that loaded after our DllMain. */
__declspec(dllexport) DWORD WINAPI GetFileVersionInfoSizeW(LPCWSTR f, LPDWORD h) {
    (void)f; install(); if (h) *h = 0; return 0;
}
__declspec(dllexport) BOOL WINAPI GetFileVersionInfoW(LPCWSTR f, DWORD h, DWORD len, LPVOID d) {
    (void)f; (void)h; (void)len; (void)d; install(); return FALSE;
}
__declspec(dllexport) BOOL WINAPI VerQueryValueW(LPCVOID b, LPCWSTR s, LPVOID *p, PUINT l) {
    (void)b; (void)s; install(); if (p) *p = NULL; if (l) *l = 0; return FALSE;
}
