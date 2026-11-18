"""Native acceptance of resource enforcement, not just configured constants."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SUPERVISOR = Path(__file__).resolve().parents[2] / 'windows_job.py'


@unittest.skipUnless(sys.platform == 'win32', 'Windows Job Objects require Windows')
class WindowsJobTests(unittest.TestCase):
    def run_child(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / 'child.py'
            child.write_text(source, encoding='utf-8')
            return subprocess.run(
                [sys.executable, str(SUPERVISOR), '--memory-mb', '128',
                 '--cpu-percent', '10', '--', sys.executable, str(child)],
                capture_output=True, text=True, timeout=30)

    def test_child_exit_status_is_preserved(self):
        result = self.run_child('raise SystemExit(23)')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn('Windows job enforced:', result.stdout)

    def test_child_cannot_exceed_job_memory_limit(self):
        result = self.run_child(
            'try:\n data=bytearray(512*1024*1024)\n'
            'except MemoryError:\n print("allocation denied")\n'
            'else:\n raise SystemExit(24)\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('allocation denied', result.stdout)
