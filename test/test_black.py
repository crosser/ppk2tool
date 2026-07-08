from glob import glob
from subprocess import call
from unittest import TestCase


class BlackTest(TestCase):
    """Class for back formatting check"""

    def test_run_black(self):
        result = call(
            [
                "black",
                "--check",
                "--diff",
                "-l",
                "79",
            ]
            + glob("ppk2tool/**/*.py", recursive=True)
            + glob("test/**/*.py", recursive=True)
        )
        self.assertEqual(result, 0, "black formatting")
