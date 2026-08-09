import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositorySafetyTests(unittest.TestCase):
    def test_documentation_does_not_pipe_remote_code_to_a_shell(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("| bash", readme)
        self.assertNotIn("| iex", readme)

    def test_default_serial_port_is_portable(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("auto", config["serial"]["port"])


if __name__ == "__main__":
    unittest.main()
