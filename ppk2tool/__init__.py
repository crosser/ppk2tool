"""ppk2 API"""

# APT dependency: python3-more-itertools # for `partition()`

from abc import ABC
from enum import Enum
from itertools import groupby
from more_itertools import partition
from struct import unpack
from typing import (
    Callable,
    get_args,
    get_type_hints,
    get_origin,
    IO,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

__all__ = "PPK2CTX", "PPK2Cmd", "PPK2Sample", "PPK2Meta"


class PPK2Cmd(Enum):
    """Command bytes"""

    # Shamelessly stolen from https://github.com/IRNAS/ppk2-api-python
    # that itself was very likely shamelessly stolen from
    # https://github.com/nordicsemi/pc-nrfconnect-ppk/blob/main/\
    #   src/constants.ts
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
    TRIGGER_EXT_TOGGLE = 0x10  # Looks like it's a typo in the upsream code?
    SET_POWER_MODE = 0x11
    RES_USER_SET = 0x12
    SPIKE_FILTERING_ON = 0x15
    SPIKE_FILTERING_OFF = 0x16
    GET_META_DATA = 0x19
    RESET = 0x20
    SET_USER_GAINS = 0x25


class PPK2Cali(NamedTuple):
    """Calibration attributes. There are 5 sets of them."""

    R: float
    GS: float
    GI: float
    O: float
    S: float
    I: float
    UG: float


class PPK2Meta(NamedTuple):
    """Characteristins and calibration attributes given by the device"""

    Calibrated: bool
    VDD: int  # in millivolt
    HW: int
    mode: int
    IA: int
    cali: Tuple[PPK2Cali, ...]

    @classmethod
    def parse(cls, data: bytes) -> "PPK2Meta":
        # Attributes are printed as "key: value" pairs, one per line.
        # Keys that end with a numeric character refer to indexed calibration
        # values. Separate indexed and "normal" attributes:
        at, ft = partition(
            lambda x: x[0][-1] in "0123456789",
            (
                ln.split(": ")
                for ln in data.decode("utf-8").split("\n")
                if ln and ln != "END"
            ),
        )
        # print(tuple(at), tuple(ft))
        attrs = {k: v for k, v in at}  # "normal" attributes
        factors = (  # Group them by the value of the index, sorted
            {k[:-1]: v for k, v in d}  # Drop the last char from the key
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
    """Does the second integer follow the first, modulo 64?"""

    return ((x >> 2) + 1) % 0x40 == (y >> 2) % 0x40


class PPK2Sample(NamedTuple):
    """Response / data received from the kit"""

    logic: int  # 8 bits with GPIO channels
    count: int  # type: ignore [assignment]  # 6-bit wrappable sample counter
    band: int  # 3-bit precision band. Valid values from 0 to 4.
    radc: int  # 14-bit raw ADC reading
    amps: float  # converted ADC reading in Amps


_adc_mult = 1.8 / 163840.0


class PPK2CTX:
    """ppk2 context object"""

    def __init__(self) -> None:
        self.buffer = b""
        # buffer has space for three samples (of 4 bytes each).
        self.fifo = bytearray(12)
        self.lastcmd: Optional[PPK2Cmd] = None
        self.waitmeta = False
        # Default calibration matrix
        # https://github.com/nordicsemi/pc-nrfconnect-ppk/blob/main\
        #   /src/device/serialDevice.ts#L46
        self.cali: Tuple[PPK2Cali, ...] = (
            PPK2Cali(R=1031.64, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=101.65, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=10.15, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=0.94, GS=1, GI=1, O=0, S=0, I=0, UG=1),
            PPK2Cali(R=0.043, GS=1, GI=1, O=0, S=0, I=0, UG=1),
        )
        self.vdd = 3.7  # in Volt

    def cmd(self, cmd: PPK2Cmd, *args: int) -> bytes:
        self.lastcmd = cmd
        if cmd is PPK2Cmd.GET_META_DATA:
            self.waitmeta = True
        return bytes((*(cmd.value,), *args))

    def setcallback(
        self, cb: Callable[[PPK2Cmd, PPK2Meta | PPK2Sample], None]
    ) -> "PPK2CTX":
        """Register function to call when something is ready"""
        self.cb = cb
        return self

    def setvdd(self, vdd: float) -> None:
        # Store VDD in Volts for calculating current mesaurement
        self.vdd = vdd

    def inject(self, data: bytearray | bytes) -> None:
        """Accept raw data from the kit's serial interface"""
        assert self.lastcmd is not None
        # if self.printlimit:
        #     self.printlimit -= 1
        #     print("Inject data", len(self.buffer), data.hex())
        if self.waitmeta:
            self.buffer += data
            if self.buffer.endswith(b"END\n"):
                meta = PPK2Meta.parse(self.buffer)
                self.cali = meta.cali
                self.setvdd(meta.VDD / 1000.0)
                self.waitmeta = False
                self.buffer = b""
                self.cb(self.lastcmd, meta)
        else:
            skipmatch = 0  # To avoid accidental matches
            for b in data:
                self.fifo[1:] = self.fifo[:-1]
                self.fifo[0] = b
                if skipmatch:
                    skipmatch -= 1
                    continue
                # print(self.fifo.hex(":", 4))
                if inseq(self.fifo[5], self.fifo[1]) and inseq(
                    self.fifo[9], self.fifo[5]
                ):
                    skipmatch = 3  # After match found, do not match next three
                    # we are in sync, can consume four bytes
                    # print(self.fifo[0], self.fifo[1],
                    #   self.fifo[2], self.fifo[3])

                    # One sample is four bytes. When treated
                    # as little endian 32bit int, the structure is:
                    #
                    # 0        1        2        3
                    # -------- -------- -------- --------
                    # llllllll cccccc-r rraaaaaa aaaaaaaa
                    #
                    # for logic line bits, wrappable sample counter,
                    # precision range, and ADC

                    radc = (self.fifo[2] & 0x3F) << 8 | self.fifo[3]
                    band = (self.fifo[1] & 0x01) << 2 | (self.fifo[2] >> 6)
                    # print(self.fifo[0], self.fifo[1] >> 2, band, radc)

                    # https://github.com/nordicsemi/pc-nrfconnect-ppk/\
                    #     blob/main/src/device/serialDevice.ts#L137
                    c = self.cali[4 if band > 4 else band]
                    rwg = ((radc * 4.0) - c.O) * (_adc_mult / c.R)
                    amps = c.UG * (
                        rwg * (c.GS * rwg + c.GI) + (c.S * self.vdd + c.I)
                    )
                    self.cb(
                        self.lastcmd,
                        PPK2Sample(
                            logic=self.fifo[0],
                            count=self.fifo[1] >> 2,
                            band=band,
                            radc=radc,
                            amps=amps,
                        ),
                    )
                # else:
                #     if len(self.fifo) > 10:
                #         print("Resync", self.fifo)
