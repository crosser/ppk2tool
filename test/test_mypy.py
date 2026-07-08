from glob import glob
from subprocess import call
from unittest import TestCase


class MypyTest(TestCase):
    """Class for mypy type check"""

    def test_run_mypy(self) -> None:
        result = call(
            [
                "mypy",
                "--strict",
                "-p",
                "ppk2tool",
                "-p",
                "test",
            ]
        )
        self.assertEqual(result, 0, "mypy typecheck")
