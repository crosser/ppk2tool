"""ppk2 API"""

# APT dependency: python3-more-itertools # for `partition()`

from abc import ABC
from collections import deque
from enum import Enum
from itertools import groupby
from more_itertools import partition
from typing import (
    Callable,
    get_args,
    get_type_hints,
    get_origin,
    NamedTuple,
    Sequence,
    Tuple,
)


class PPK2Cmd(Enum):
    """Command bytes"""

    # Shamelessly stolen from https://github.com/IRNAS/ppk2-api-python
    NO_OP = 0x00
    TRIGGER_SET = 0x01
    AVG_NUM_SET = 0x02  # no-firmware
    TRIGGER_WINDOW_SET = 0x03
    TRIGGER_INTERVAL_SET = 0x04
    TRIGGER_SINGLE_SET = 0x05
    AVERAGE_START = 0x06
    AVERAGE_STOP = 0x07
    RANGE_SET = 0x08
    LCD_SET = 0x09
    TRIGGER_STOP = 0x0A
    DEVICE_RUNNING_SET = 0x0C
    REGULATOR_SET = 0x0D
    SWITCH_POINT_DOWN = 0x0E
    SWITCH_POINT_UP = 0x0F
    TRIGGER_EXT_TOGGLE = 0x10  # Not certain, was a typo upstream?
    SET_POWER_MODE = 0x11
    RES_USER_SET = 0x12
    SPIKE_FILTERING_ON = 0x15
    SPIKE_FILTERING_OFF = 0x16
    GET_META_DATA = 0x19
    RESET = 0x20
    SET_USER_GAINS = 0x25


class PPK2Cali(NamedTuple):
    R: float
    GS: float
    GI: float
    O: float
    S: float
    I: float
    UG: float


class PPK2Meta(NamedTuple):
    Calibrated: bool
    VDD: int
    HW: int
    mode: int
    IA: int
    cali: Tuple[PPK2Cali, ...]

    @classmethod
    def parse(cls, data: bytes) -> "PPK2Meta":
        at, ft = partition(
            lambda x: x[0][-1] in "0123456789",
            (
                ln.split(": ")
                for ln in data.decode("utf-8").split("\n")
                if ln and ln != "END"
            ),
        )
        # print(tuple(at), tuple(ft))
        attrs = {k: v for k, v in at}
        factors = (
            {k[:-1]: v for k, v in d}
            for _, d in groupby(
                sorted(ft, key=lambda x: x[0][-1]), key=lambda x: x[0][-1]
            )
        )
        # print(tuple(factors))

        cali = tuple(
            PPK2Cali(
                **{
                    attr: acls(el.get(attr))
                    for attr, acls in get_type_hints(PPK2Cali).items()
                }
            )
            for el in factors
        )
        kwargs = {
            attr: acls(attrs.get(attr))
            for attr, acls in get_type_hints(cls).items()
            if get_origin(acls) is not tuple
        }
        # print(kwargs, cali)
        if extradata := set(attrs) - set(kwargs):
            print("Unknown meta attributes", extradata)
        if missingdata := set(kwargs) - set(attrs):
            raise RuntimeError("Missing meta attributes {misingdata}")
        return cls(**kwargs, cali=cali)


def inseq(x: int, y: int) -> bool:
    return ((x >> 2) + 1) % 0x40 == (y >> 2) % 0x40


class PPK2Sample(NamedTuple):
    """Response / data received from the kit"""

    logic: int  # 8 bits with GPIO channels
    count: int  # 6-bit wrappable sample counter
    band: int  # 3-bit precision band. Valid values from 0 to 4.
    radc: int  # 14-bit raw ADC reading
    amps: float  # converted ADC reading


_adc_mult = 1.8 / 163840.0


class PPK2CTX:
    """ppk2 context object"""

    def __init__(self, tty) -> None:
        # buffer has space for three samples (of 4 bytes each).
        self.buffer = b""
        self.fifo = deque(maxlen=12)
        self.tty = tty
        self.lastcmd = None
        self.waitmeta = False
        self.cali = (
            PPK2Cali(R=1031.64, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=101.65, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=10.15, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=0.94, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=0.043, GS=1, GI=1, O=0, S=0, I=0, UG=1),
        )
        self.vdd = 3.7

    def cmd(self, cmd: PPK2Cmd, *args: int) -> None:
        print("Writing command", cmd, args)
        self.lastcmd = cmd
        if cmd is PPK2Cmd.GET_META_DATA:
            self.waitmeta = True
        self.tty.write(bytes((*(cmd.value,), *args)))

    def setcallback(
        self, cb: Callable[[PPK2Cmd, PPK2Meta | PPK2Sample], None]
    ) -> "PPK2CTX":
        """Register function to call when something is ready"""
        self.cb = cb
        return self

    def setvdd(self, vdd: float) -> None:
        self.vdd = vdd

    def inject(self, data: bytearray | bytes) -> None:
        """Accept raw data from the kit's serial interface"""
        # if self.printlimit:
        #     self.printlimit -= 1
        #     print("Inject data", len(self.buffer), data.hex())
        if self.waitmeta:
            self.buffer += data
            if self.buffer.endswith(b"END\n"):
                meta = PPK2Meta.parse(self.buffer)
                self.cali = meta.cali
                self.setvdd(meta.VDD)
                self.waitmeta = False
                self.buffer = b""
                self.cb(self.lastcmd, meta)
        else:
            for b in data:
                self.fifo.append(b)
                if (
                    len(self.fifo) > 10
                    and inseq(self.fifo[2], self.fifo[6])
                    and inseq(self.fifo[6], self.fifo[10])
                ):
                    # we are in sync, can consume four bytes

                    # One sample is four bytes. When treated
                    # as little endian 32bit int, the structure is:
                    #
                    # 3        2        1        0
                    # -------- -------- -------- --------
                    # llllllll cccccc-r rraaaaaa aaaaaaaa
                    #
                    # for logic line bits, wrappable sample counter,
                    # precision rangem and ADC

                    b3 = self.fifo.popleft()
                    b2 = self.fifo.popleft()
                    b1 = self.fifo.popleft()
                    b0 = self.fifo.popleft()
                    # print(b0, b1, b2, b3)
                    radc = (b2 & 0x7F) << 8 | b3
                    band = (b1 & 0x01) << 2 | (b2 >> 6)

                    # https://github.com/nordicsemi/pc-nrfconnect-ppk/\
                    #     blob/4cbb4c41c420ddb8125638fffc6d72f6b21c5aaa/\
                    #     src/device/serialDevice.ts#L137
                    c = self.cali[4 if band > 4 else band]
                    rwg = ((radc << 2) - c.O) * (_adc_mult / c.R)
                    amps = c.UG * (
                        rwg * (c.GS * rwg + c.GI)
                        + (c.S * (self.vdd / 1000.0) + c.I)
                    )
                    self.cb(
                        self.lastcmd,
                        PPK2Sample(
                            logic=b0,
                            count=b1 >> 2,
                            band=band,
                            radc=radc,
                            amps=amps,
                        ),
                    )
                # else:
                #     if len(self.fifo) > 10:
                #         print("Resync", self.fifo)
