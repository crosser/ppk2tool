"""Example frontend for PPK2 profiler"""

from getopt import getopt  # pylint: disable=deprecated-module
from math import floor, log10
import os
from selectors import DefaultSelector, EVENT_READ
from sys import argv, stdin
from struct import unpack
from termios import tcsetattr, TCSAFLUSH
from time import CLOCK_MONOTONIC
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
        self.metadata: Optional[PPK2Meta] = None
        self.accum: int = 0
        self.pos: int = 0
        self.avg: float = 0.0
        self.timer_up: bool = False

    def timer(self) -> None:
        self.timer_up = True

    def process(self, cmd: PPK2Cmd, data: PPK2Data) -> None:
        if cmd is PPK2Cmd.GET_META_DATA:
            self.metadata = PPK2Meta.parse(data)
            print("metadata", self.metadata)
        else:  # assume that they are samples
            # if self.count < 200:
            #     print("incoming data", data[:8].hex(), len(data))
            for i in data:
                if i == 0xFF:
                    adc = self.accum & 0x007FFF
                    rng = (self.accum & 0x01C000) >> 14
                    flg = (self.accum & 0x020000) >> 17
                    ind = (self.accum & 0xFC0000) >> 18
                    bts = i

                    if self.pos == 24 and not flg:
                        adc_mult = 1.8 / 163840
                        if rng > 4:
                            print("\n rng", rng, "in", hex(self.accum))
                            rng = 4
                        c = self.metadata.cali[rng]
                        # rwg = (adc_value - O) * adc_mult / R
                        # adc = UG * (rwg * (GS * rwg + GI) + (S * (vdd/1000) + 1))
                        rwg = (adc - c.O) * (adc_mult / c.R)
                        amps = c.UG * (
                            rwg * (c.GS * rwg + c.GI)
                            + (c.S * self.metadata.VDD / 1000.0 + c.I)
                        )
                        self.avg = self.avg * 0.99 + amps * 0.01
                        if self.timer_up:
                            self.timer_up = False
                            print(
                                "{:-9.3f} : {}".format(
                                    self.avg * 1000,
                                    (
                                        "+" * floor((log10(self.avg) + 5) * 8)
                                        if self.avg > 0
                                        else ""
                                    ),
                                ),
                                end="\033[K\r",
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
    topts, args = getopt(argv[1:], "dsv:f:")
    opts: Dict[str, str] = dict(topts)
    debug: bool = "-d" in opts
    passthrough: bool = "-s" in opts
    voltage: float = bracket(float(opts.get("-v", 3.7)))
    frequency: int = opts.get("-f", 200)
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
    ) as tty, open(
        "timerfl",
        "rb",
        opener=lambda nm, flg: os.timerfd_create(
            CLOCK_MONOTONIC, flags=os.TFD_NONBLOCK
        ),
    ) as timerfl, DefaultSelector() as sel, rawstdin():
        setcbreak(tty)
        os.timerfd_settime_ns(
            timerfl.fileno(),
            initial=frequency * 1_000_000,
            interval=frequency * 1_000_000,
        )
        sel.register(stdin, EVENT_READ)
        sel.register(tty, EVENT_READ)
        sel.register(timerfl, EVENT_READ)
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
                    elif events == EVENT_READ and key.fileobj == timerfl:
                        times = int.from_bytes(key.fileobj.read(8), "little")
                        # print("timer event", times)
                        rctx.timer()
                    else:
                        raise RuntimeError(
                            f"Events {events} on unknown fileobj {key}"
                        )
            except KeyboardInterrupt:
                running = False
        ctx.cmd(PPK2Cmd.RESET)
    print("Exit")
