# Library and rudimentary CLI for Nordic PPK2

Vendor app source code can be found
[here](https://github.com/nordicsemi/pc-nrfconnect-ppk).
IANAL. In my reading, I am allowed to "use the software in source form" in
order to glean the details of the protocol controlling the device; of the
four limitations in their license, two apply only binary distribution, the
other two are not violated by me reading and understanding the code.

One sample is four bytes. When treated as little endian 32bit int, the
structure is:
```
3        2        1        0
-------- -------- -------- --------
llllllll cccccc-r rraaaaaa aaaaaaaa
```
for logic line bits, wrappable sample counter, precision rangem and ADC
