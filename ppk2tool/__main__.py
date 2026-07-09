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
from typing import Any, ContextManager, Dict, List, Literal, Optional

from . import PPK2CTX, PPK2Cmd, PPK2Sample, PPK2Meta


class rawstdin(ContextManager[None]):
    """Run nexted code with stdin in raw mode if it is a tty"""

    # This really ought to be in Python's standard library...
    def __init__(self) -> None:
        self.old_tcattr: Optional[List[Any]] = None

    def __enter__(self) -> None:
        if stdin.isatty():
            self.old_tcattr = setcbreak(stdin)

    def __exit__(self, *_ex: Any) -> Literal[False]:
        if self.old_tcattr is not None:
            tcsetattr(stdin, TCSAFLUSH, self.old_tcattr)
        return False


class receiver:
    def __init__(self) -> None:
        self.metadata: Optional[PPK2Meta] = None
        self.vdd: float = 5000.0
        self.accum: int = 0
        self.pos: int = 0
        self.avg: float = 0.0
        self.timer_up: bool = False

    def timer(self) -> None:
        self.timer_up = True

    def process(self, cmd: PPK2Cmd, data: PPK2Meta | PPK2Sample) -> None:
        if isinstance(data, PPK2Meta):
            self.metadata = data
            print("metadata", self.metadata)
            self.vdd = self.metadata.VDD
        else:
            # print(data)
            self.avg = self.avg * 0.99 + data.amps * 0.01
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
    frequency: int = int(opts.get("-f", 200))
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
        assert devname is not None, "listdir() returned entry None?!"
        devpath = os.path.join("/dev/serial/by-id", devname)
    print("Using PPK on", devpath, "voltage", voltage, "V")

    with (
        open(
            devpath,
            "rb+",
            buffering=0,
            opener=lambda nm, flg: os.open(nm, flg | os.O_NOCTTY),
        ) as tty,
        open(
            "timerfl",
            "rb",
            opener=lambda nm, flg: os.timerfd_create(
                CLOCK_MONOTONIC, flags=os.TFD_NONBLOCK
            ),
        ) as timerfl,
        DefaultSelector() as sel,
        rawstdin(),
    ):
        setcbreak(tty.fileno())
        os.timerfd_settime_ns(
            timerfl.fileno(),
            initial=frequency * 1_000_000,
            interval=frequency * 1_000_000,
        )
        sel.register(stdin, EVENT_READ)
        sel.register(tty, EVENT_READ)
        sel.register(timerfl, EVENT_READ)
        rctx = receiver()
        ctx = PPK2CTX().setcallback(rctx.process)
        buffer = bytearray(1024)

        def send(cmd: PPK2Cmd, *args: int) -> None:
            print("Writing command", cmd, args)
            tty.write(ctx.cmd(cmd, *args))

        send(PPK2Cmd.REGULATOR_SET, *divmod(int(voltage * 1000.0), 256))
        send(PPK2Cmd.SET_POWER_MODE, 1 if passthrough else 2)
        send(PPK2Cmd.GET_META_DATA)

        print(
            "P/p: Power on/off, M/m: measuring start/stop,"
            " V/v: voltage 100mV up/down\nQ or q: quit"
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
                            send(PPK2Cmd.DEVICE_RUNNING_SET, int(k == "P"))
                        elif k in ("V", "v"):
                            voltage = bracket(
                                voltage + 0.1 if k == "V" else voltage - 0.1
                            )
                            print("Output voltage changed to", voltage)
                            vdd = int(voltage * 1000.0)
                            ctx.setvdd(vdd)
                            send(PPK2Cmd.REGULATOR_SET, *divmod(vdd, 256))
                        elif k in ("M", "m"):
                            send(
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
                        times = int.from_bytes(timerfl.read(8), "little")
                        # print("timer event", times)
                        rctx.timer()
                    else:
                        raise RuntimeError(
                            f"Events {events} on unknown fileobj {key}"
                        )
            except KeyboardInterrupt:
                running = False
        send(PPK2Cmd.RESET)
    print("Exit")
