"""Example frontend for PPK2 profiler"""

from getopt import getopt  # pylint: disable=deprecated-module
import os
from selectors import DefaultSelector, EVENT_READ
from sys import argv, stdin
from struct import unpack
from termios import tcsetattr, TCSAFLUSH
from tty import setcbreak
from typing import ContextManager, Dict, Literal

from . import PPK2CTX, PPK2Cmd, PPK2Data, PPK2Meta


class rawstdin(ContextManager[None]):
    """Run nexted code with stdin in raw mode if it is a tty"""

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


class receiver:
    count: int
    remainder: bytes

    def __init__(self) -> None:
        self.count = 0
        self.remainder = b""

    def process(self, cmd: PPK2Cmd, data: PPK2Data) -> None:
        if cmd is PPK2Cmd.GET_META_DATA:
            print("metadata", PPK2Meta.parse(data))
        else:  # assume that they are samples
            if self.count < 100:
                print("incoming data", data[:8].hex(), len(data))
            view = memoryview(self.remainder + data)
            self.remainder = b""
            for i in range(0, len(view), 4):
                chunk = view[i:i+4]
                if len(chunk) < 4:  # last in buffer
                    self.remainder = bytes(chunk)
                    if self.count < 100:
                        print("short read", self.remainder, len(chunk))
                else:
                    self.count += 1
                    if self.count < 100:
                        print("sample", cmd, "got", hex(unpack("<I", chunk)[0]))


def bracket(voltage: float) -> float:
    if voltage < 0.8:
        return 0.8
    if voltage > 5.0:
        return 5.0
    return voltage


if __name__ == "__main__":
    topts, args = getopt(argv[1:], "dsv:")
    opts: Dict[str, str] = dict(topts)
    debug: bool = "-d" in opts
    passthrough: bool = "-s" in opts
    voltage: float = bracket(float(opts.get("-v", 3.7)))
    if len(args) == 1:
        devpath = args[0]
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
    print("Using PPK on", devpath, "voltage", voltage, "V")

    with open(
        devpath,
        "rb+",
        buffering=0,
        opener=lambda nm, flg: os.open(nm, flg | os.O_NOCTTY),
    ) as tty, DefaultSelector() as sel, rawstdin():
        setcbreak(tty)
        sel.register(stdin, EVENT_READ)
        sel.register(tty, EVENT_READ)
        rctx = receiver()
        ctx = PPK2CTX(tty).setcallback(rctx.process)

        ctx.cmd(PPK2Cmd.REGULATOR_SET, *divmod(int(voltage * 1000), 256))
        ctx.cmd(PPK2Cmd.SET_POWER_MODE, 1 if passthrough else 2)
        ctx.cmd(PPK2Cmd.GET_META_DATA)

        print(
            "P/p: Power on/off, M/m: measuring start/stop, V/v: voltage 100mV up/down"
            "\nQ or q: quit"
        )
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
                        elif k in ("V", "v"):
                            voltage = bracket(
                                voltage + 0.1 if k == "V" else voltage - 0.1
                            )
                            print("Output voltage changed to", voltage)
                            ctx.cmd(
                                PPK2Cmd.REGULATOR_SET,
                                *divmod(int(voltage * 1000), 256),
                            )
                        elif k in ("M", "m"):
                            ctx.cmd(
                                PPK2Cmd.AVERAGE_START
                                if k == "M"
                                else PPK2Cmd.AVERAGE_STOP
                            )
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
