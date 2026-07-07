# Library and rudimentary CLI for Nordic PPK2

Power Profiler II is a gadget that measures current used by a piece of
equipment (usually a dev board), optionally supplying the power. It is
controlled using a serial over USB interface (and is powered from USB).

Vendor offers an application to control the gadget and take measurements,
but it has so many suprefluous dependencies (including one closed source
library) that it is practically impossible to make work. However, control
and measurement protocol can be gleaned from published vendor's source
code, that can be found
[here](https://github.com/nordicsemi/pc-nrfconnect-ppk).

IANAL. In my reading, I am allowed to "use the software in source form" in
order to discover the details of the protocol controlling the device; of the
four limitations in their license, two apply only binary distribution, the
other two are not violated by me reading and understanding the code.

There exists
[another independent Python application](https://github.com/IRNAS/ppk2-api-python)
that can control the device; the code largely mirrors vendor's TypeScript
implementation, that I've found unsatisfactory.

To use the library,

```
from ppk2tool import PPK2CTX, PPK2Cmd, PPK2Sample, PPK2Meta
```

The library does not contain any code to communicate with the device; calling
application code needs to send byte blobs provided by the module
(NOTE: not the way it is implemented at the time of publication, to be fixed),
and inject byte blobs that arrived from the usb-serial line. The library will
return parsed results via application-provided callback.

Use `__main__.py` as an example.
