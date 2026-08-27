# 32-bit mingw cross build.  macOS:  brew install mingw-w64
# make defines CC=cc by default, so ?= would never take effect
ifeq ($(origin CC),default)
CC := i686-w64-mingw32-gcc
endif
CFLAGS  ?= -O2 -Wall
LDFLAGS ?= -lws2_32 -Wl,--kill-at
# override if wine is not on PATH
WINE    ?= wine

all: pkusb.dll

pkusb.dll: pkusb.c pkring.c pkring.h pkunlock.c pkunlock.h
	$(CC) -shared $(CFLAGS) -o $@ pkusb.c pkring.c pkunlock.c $(LDFLAGS)

../tests/ringtest.exe: ../tests/ringtest.c pkring.c pkring.h
	$(CC) $(CFLAGS) -I. -o $@ ../tests/ringtest.c pkring.c

../tests/unlocktest.exe: ../tests/unlocktest.c pkunlock.c pkunlock.h
	$(CC) $(CFLAGS) -I. -o $@ ../tests/unlocktest.c pkunlock.c

test: ../tests/ringtest.exe ../tests/unlocktest.exe
	MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$(WINE)" ../tests/ringtest.exe
	MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$(WINE)" ../tests/unlocktest.exe

clean:
	rm -f pkusb.dll ../tests/ringtest.exe ../tests/unlocktest.exe

.PHONY: all test clean
