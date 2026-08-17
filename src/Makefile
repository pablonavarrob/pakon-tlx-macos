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

pkusb.dll: pkusb.c pkring.c pkring.h
	$(CC) -shared $(CFLAGS) -o $@ pkusb.c pkring.c $(LDFLAGS)

../tests/ringtest.exe: ../tests/ringtest.c pkring.c pkring.h
	$(CC) $(CFLAGS) -I. -o $@ ../tests/ringtest.c pkring.c

test: ../tests/ringtest.exe
	MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$(WINE)" ../tests/ringtest.exe

clean:
	rm -f pkusb.dll ../tests/ringtest.exe

.PHONY: all test clean
