from re import findall
from setuptools import setup

def pep_version(s: str) -> str:
    """Take initial numeric part from the string, to comply with PEP-440"""
    for i in range(0, len(s)):
        if not s[i] in "0123456789.":
            break
    return s[:i].rstrip(".")

with open("debian/changelog", "r") as clog:
    _, version, _ = findall(
        r"(?P<src>.*) \((?P<version>.*)\) (?P<suite>.*); .*",
        clog.readline().strip(),
    )[0]

setup(
    version=pep_version(version),
)
