""" Example frontend for PPK2 profiler """

import os
from selectors import DefaultSelector, EVENT_READ
from sys import argv, stdin
from termios import tcsetattr, TCSAFLUSH
from tty import setcbreak
from typing import ContextManager, Literal

from . import PPK2CTX, PPK2Cmd, PPK2Data

class rawstdin(ContextManager[None]):
    """ Run nexted code with stdin in raw mode if it is a tty """
    # This really ought to be in Python's standard library...
    def __init__(self) -> None:
        self.old_tcattr = None

    def __enter__(self) -> None:
        if stdin.isatty():
            self.old_tcattr = setcbreak(stdin)

    def __exit__(self, *_ex) -> Literal[False]:
        if self.old_tcattr is not None:
            tcsetattr(stdin, TCSAFLUSH, self.old_tcattr)
        return False

def show(cmd: PPK2Cmd, data: PPK2Data) -> None:
    print("callback", cmd, "got", data)

if __name__ == "__main__":
    if len(argv) == 2:
        devpath = argv[1]
    else:
        found = 0
        devname = None
        for e in os.listdir("/dev/serial/by-id"):
            if e.startswith("usb-Nordic_Semiconductor_PPK2"):
                found += 1
                devname = e
        if found != 1:
            print("zero or more than one profiler devices")
            exit(1)
        devpath = os.path.join("/dev/serial/by-id", devname)
    print("Using PPK on", devpath)

    with open(
        devpath, "rb+", buffering=0,
        opener=lambda nm, flg: os.open(nm, flg | os.O_NOCTTY)
    ) as tty, DefaultSelector() as sel, rawstdin():
        setcbreak(tty)
        sel.register(stdin, EVENT_READ)
        sel.register(tty, EVENT_READ)
        ctx = PPK2CTX(tty).setcallback(show)
        print("Q/q: quit, P/p: Power on/off, M/m: measuring start/stop")
        ctx.cmd(PPK2Cmd.GET_META_DATA)
        running = True
        while running:
            try:
                evlist = sel.select()
                # print("Select dropped with evlist", evlist)
                for key, events in evlist:
                    # print("On", key, "events", events)
                    if events == EVENT_READ and key.fileobj == stdin:
                        k = stdin.read(1)
                        # print("Got character", k)
                        if k in ("Q", "q"):
                            running = False
                        elif k in ("P", "p"):
                            ctx.cmd(PPK2Cmd.DEVICE_RUNNING_SET, int(k == "P"))
                        elif k == "v":
                            ctx.cmd(PPK2Cmd.REGULATOR_SET, 14, 116)
                        else:
                            print("Unknown command", k)
                    elif events == EVENT_READ and key.fileobj == tty:
                        data = tty.read(1024)
                        # print("Read", len(data), "bytes")
                        ctx.inject(data)
                    else:
                        raise RuntimeError(
                            f"Events {events} on unknown fileobj {key}"
                        )
            except KeyboardInterrupt:
                running = False
    print("Exit")
