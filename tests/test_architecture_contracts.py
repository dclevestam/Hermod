import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureContractTests(unittest.TestCase):
    def test_contract_checker_passes(self):
        script = ROOT / 'tools' / 'check_architecture_contracts.py'
        subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


if __name__ == '__main__':
    unittest.main()
