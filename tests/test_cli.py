import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from signalpost.__main__ import main


class ConfigurationTests(unittest.TestCase):
    def test_hosted_environment_is_passed_to_server(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "persistent.sqlite3"
            server = Mock()
            server.server_port = 9999
            server.serve_forever.side_effect = KeyboardInterrupt
            environment = {
                "SIGNALPOST_DB": str(database), "SIGNALPOST_HOST": "0.0.0.0", "PORT": "9999",
                "SIGNALPOST_AUTH_TOKEN": "test-password-with-more-than-32-characters",
                "SIGNALPOST_ALLOWED_HOSTS": "dashboard.example,other.example",
                "SIGNALPOST_ALLOWED_ORIGINS": "https://dashboard.example,https://other.example",
                "SIGNALPOST_MAX_BATCH": "5", "SIGNALPOST_MAX_CONCURRENT": "3",
            }
            with patch.dict(os.environ, environment, clear=True), \
                    patch("sys.argv", ["signalpost", "serve"]), \
                    patch("signalpost.__main__.make_server", return_value=server) as factory:
                main()
            args, kwargs = factory.call_args
            self.assertEqual(args[0].store.path, str(database))
            self.assertEqual(args[1:], ("0.0.0.0", 9999))
            self.assertEqual(kwargs["allowed_hosts"], ["dashboard.example", "other.example"])
            self.assertEqual(kwargs["allowed_origins"], ["https://dashboard.example", "https://other.example"])
            self.assertEqual(kwargs["auth_token"], environment["SIGNALPOST_AUTH_TOKEN"])
            self.assertEqual(kwargs["max_batch"], 5)
            self.assertEqual(kwargs["max_concurrent"], 3)
            server.server_close.assert_called_once()
            self.assertTrue(database.exists())

    def test_smoke_failure_exits_one_after_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {}, clear=True), \
                patch("sys.argv", ["signalpost", "--db", str(Path(directory) / "state.sqlite3"),
                                   "smoke", "--input", "input.txt", "--output", "output.json",
                                   "--report", "report.json"]), \
                patch("signalpost.__main__.run_evaluation",
                      return_value={"failures": [{"status": "failed"}]}) as evaluate:
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 1)
            self.assertTrue(evaluate.call_args.kwargs["smoke"])
            self.assertTrue(evaluate.call_args.kwargs["refresh"])
