"""Unittest for pylint check"""

from os import path
from re import match
from subprocess import call
from unittest import TestCase, skipUnless


class PylintTest(TestCase):
    """Class for pylint check"""

    def test_run_pylint(self) -> None:
        result = call(["pylint", "ppk2tool"])
        self.assertEqual(result, 0, "pylint")
