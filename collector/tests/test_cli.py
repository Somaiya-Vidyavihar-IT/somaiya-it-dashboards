import os
import ssl
import tempfile
import unittest
from unittest import mock

from netmon import __main__ as cli, config, creds


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = config.validate({
            "collector": {"switch_credential_sets": [{"name": "set1", "username": "u1"}, {"name": "set2", "username": "u2"}],
                          "wlc_username": "nms", "data_dir": os.path.join(d, "data")},
            "subnets": [{"cidr": "10.0.0.0/30"}], "wlc": {"hosts": ["10.0.0.5"]},
            "server": {"cert_file": os.path.join(d, "tls", "cert.pem"), "key_file": os.path.join(d, "tls", "key.pem")}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_make_cert_produces_a_pair_the_api_can_serve(self):
        self.assertEqual(cli._make_cert(self.cfg, "netmon.example.test", ["10.0.0.9"]), 0)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cfg["server"]["cert_file"], self.cfg["server"]["key_file"])
        from cryptography import x509
        with open(self.cfg["server"]["cert_file"], "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(san.get_values_for_type(x509.DNSName), ["netmon.example.test"])
        self.assertLessEqual((cert.not_valid_after_utc - cert.not_valid_before_utc).days, 398)

    def test_credential_lookup_by_set_from_environment(self):
        env = {"NETMON_SWITCH_SET2_PASSWORD": "p2", "NETMON_WLC_PASSWORD": "pw"}
        with mock.patch.dict(os.environ, env), mock.patch("keyring.get_password", return_value=None):
            self.assertEqual(creds.switch_sets(self.cfg), [("set2", "u2", "p2")])
            self.assertEqual(creds.get(self.cfg), ("nms", "pw"))
            self.assertEqual([r[2] for r in creds.status(self.cfg)], [False, True, True])
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("keyring.get_password", return_value=None):
            with self.assertRaises(creds.CredentialError):
                creds.switch_sets(self.cfg)

    def test_config_with_bom_loads_and_mistakes_are_explained(self):
        path = os.path.join(self.tmp.name, "config.toml")
        body = "[collector]\nswitch_username = 'u'\ndata_dir = 'C:\\netmon\\data'\n[[subnets]]\ncidr = '10.0.0.0/24'\n"
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + body.encode())
        self.assertEqual(config.load(path)["collector"]["data_dir"], "C:\\netmon\\data")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("'C:\\netmon\\data'", '"C:\\netmon\\data"'))
        with self.assertRaises(config.ConfigError) as ctx:
            config.load(path)
        self.assertIn("single quotes", str(ctx.exception))
        self.assertEqual(cli.main(["--config", os.path.join(self.tmp.name, "missing.toml"), "check-config"]), 2)

    def test_store_rejects_unknown_set(self):
        with self.assertRaises(creds.CredentialError):
            creds.store(self.cfg, "switch", "x", "set9")


if __name__ == "__main__":
    unittest.main()
