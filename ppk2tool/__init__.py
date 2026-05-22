"""ppk2 API"""

from abc import ABC
from enum import Enum
from typing import Callable, get_args, get_type_hints, get_origin, NamedTuple


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


class PPK2Meta(NamedTuple):
    Calibrated: bool
    R0: float
    R1: float
    R2: float
    R3: float
    R4: float
    GS0: float
    GS1: float
    GS2: float
    GS3: float
    GS4: float
    GI0: float
    GI1: float
    GI2: float
    GI3: float
    GI4: float
    O0: float
    O1: float
    O2: float
    O3: float
    O4: float
    VDD: int
    HW: int
    mode: int
    S0: float
    S1: float
    S2: float
    S3: float
    S4: float
    I0: float
    I1: float
    I2: float
    I3: float
    I4: float
    UG0: float
    UG1: float
    UG2: float
    UG3: float
    UG4: float
    IA: int

    @classmethod
    def parse(cls, data: bytes) -> "PPK2Cmd":
        datadict = dict(
            ln.split(": ")
            for ln in data.decode("utf-8").split("\n")
            if ln and ln != "END"
        )
        kwargs = {
            attr: acls(datadict.get(attr))
            for attr, acls in get_type_hints(cls).items()
        }
        # print(datadict)
        # print(kwargs)
        if (extradata := set(datadict) - set(kwargs)):
            print("Unknown meta attributes", extradata)
        if (missingdata := set(kwargs) - set(datadict)):
            raise RuntimeError("Missing meta attributes {misingdata}")
        return cls(**kwargs)


class PPK2Data(ABC):
    """Response / data received from the kit"""

    pass


class PPK2CTX:
    """ppk2 context object"""

    def __init__(self, tty) -> None:
        self.buffer = b""
        self.tty = tty
        self.lastcmd = None
        self.waitfor = None

    def cmd(self, cmd: PPK2Cmd, *args: int) -> None:
        print("Writing command", cmd, args)
        self.lastcmd = cmd
        if cmd is PPK2Cmd.GET_META_DATA:
            self.waitfor = lambda b: b.endswith(b"END\n")
        else:
            self.waitfor = None
        self.tty.write(bytes((*(cmd.value,), *args)))

    def setcallback(
        self, cb: Callable[[PPK2Cmd, PPK2Data], None]
    ) -> "PPK2CTX":
        """Register function to call when something is ready"""
        self.cb = cb
        return self

    def inject(self, data: bytes) -> None:
        """Accept raw data from the kit's serial interface"""
        # print("Inject data", data)
        self.buffer += data
        if self.waitfor is not None:
            if self.waitfor(self.buffer):
                self.cb(self.lastcmd, self.buffer)
                self.waitfor = None
                self.buffer = b""
        else:
            self.cb(self.lastcmd, self.buffer)
            self.buffer = b""
