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
    def __init__(self) -> None:
        self.count: int = 200
        self.metadata: Optional[PPK2Meta] = None
        self.accum: int = 0
        self.pos: int = 0

    def process(self, cmd: PPK2Cmd, data: PPK2Data) -> None:
        if cmd is PPK2Cmd.GET_META_DATA:
            self.metadata = PPK2Meta.parse(data)
            print("metadata", self.metadata)
        else:  # assume that they are samples
            if self.count:
                print("incoming data", data[:8].hex(), len(data))
            for i in data:
                if i == 0xFF:
                    adc = self.accum & 0x007FFF
                    rng = (self.accum & 0x01C000) >> 14
                    flg = (self.accum & 0x020000) >> 17
                    ind = (self.accum & 0xFC0000) >> 18
                    bts = i

                    if self.count:
                        self.count -= 1
                        print(
                            hex(self.accum),
                            "ind",
                            ind,
                            "flg",
                            flg,
                            "rng",
                            rng,
                            "adc",
                            adc,
                            "bts",
                            bts,
                            "pos",
                            self.pos,
                        )

                    self.accum = 0
                    self.pos = 0
                else:
                    self.accum = (self.accum >> 8) | (i << 16)
                    self.pos += 8
                    if self.pos > 24:
                        raise RuntimeError(
                            f"no 0xff at {self.pos}, i={i} of {len(data)}"
                        )


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
        buffer = bytearray(1024)

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
                        length = tty.readinto(buffer)
                        ctx.inject(buffer[:length])
                    else:
                        raise RuntimeError(
                            f"Events {events} on unknown fileobj {key}"
                        )
            except KeyboardInterrupt:
                running = False
        ctx.cmd(PPK2Cmd.RESET)
    print("Exit")
