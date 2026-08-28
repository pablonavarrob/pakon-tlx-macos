# Positive and B&W scanning

The client's *Scan Settings* dialog offers four film modes. Three of them are
greyed out:

| control | id | state as shipped |
|---|---|---|
| Negative | 0x3f6 | available |
| **Positive** | 0x3f7 | greyed |
| **B&W** | 0x3f8 | greyed |
| **B&W C41** | 0x3f9 | greyed |

This project makes them selectable. It happens automatically, with nothing to
run.

## How the controls are disabled

None of them carries `WS_DISABLED` in the dialog resource. They are created
enabled, then disabled at runtime when a capability bit is clear. The same
pattern repeats once per capability:

```
push  $0x800000        ; the capability bit
mov   %edi,%ecx
call  0x404690         ; (caps & bit) == bit ?
test  %eax,%eax
jne   skip             ; capable, so leave the controls alone
push  %eax             ; eax is 0 here, i.e. FALSE
push  $0x3f7           ; "Positive"
call  GetDlgItem
call  EnableWindow     ; ...(FALSE)
skip:
```

`0x404690` is six instructions testing that bit against a DWORD held in the
dialog object:

```
mov 0x4(%esp),%eax ; mov 0x28(%ecx),%ecx ; and %eax,%ecx
cmp %ecx,%eax ; sete %dl ; mov %edx,%eax ; ret $0x4
```

Nothing reads a licence file or the registry, and nothing asks the scanner.
Positive and B&W share one bit (`0x800000`), so a single change makes both
selectable. B&W C41 has its own (`0x400000`).

## What is changed

Two bytes, in memory, at load: each `jne` becomes an unconditional `jmp`, so the
disabling block is skipped. It skips both `push`es along with the two calls, so
the stack stays balanced, and the controls keep the enabled state they were
created with. The binary on disk is not modified.

A build this does not recognise is left alone. Each site is matched on its whole
16-byte instruction pattern, capability bit included, before anything is
written: a bare offset applied to a different OEM build would put a byte in the
middle of unrelated code and fail much later as something inexplicable. If the
pattern is absent the log says so and the controls stay greyed.

```
pkusb: film unlock: Positive and B&W enabled (RVA 0x3ef6, jne -> jmp)
pkusb: film unlock: B&W C41 -- the expected code is not at RVA 0x3ed2 in this
                    build; left alone
```

The implementation is `src/pkunlock.c`, its reasoning is in `src/pkunlock.h`,
and `tests/unlocktest.c` checks that exactly one byte changes and that every
near miss writes nothing. `make -C src test` runs it, with no hardware needed.

To switch it off: `PAKON_NO_FILM_UNLOCK=1 ./run.sh`.

## What is not yet known

The modes are selectable and they scan, confirmed on an F-135. What is untraced
is where the capability word comes from: the client only ever reads it, never
writes it, so whether the bit was meant to describe the scanner, the software
tier or something else is unknown. Nothing about the scanning depends on that
answer, but it would explain why the bit is clear on a unit that scans these
modes perfectly well.

The same change works on Windows, where people enable these controls with
AutoIt.
