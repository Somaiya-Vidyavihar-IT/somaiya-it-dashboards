import io
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

    def test_ssh_library_still_speaks_to_old_switches(self):
        """Regression (live run, 2026-11-05): paramiko 5 dropped SHA-1 key exchange, so older Catalyst
        and SG switches failed with 'no acceptable kex algorithm'. requirements.txt pins paramiko<4."""
        try:
            import paramiko
        except ImportError:
            self.skipTest("paramiko not installed")
        t = paramiko.Transport
        for kex in ("diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1"):
            self.assertIn(kex, t._preferred_kex, f"{kex} missing: old switches cannot connect")
        self.assertIn("ssh-rsa", t._preferred_keys)
        modern = [k for k in t._preferred_kex if "sha1" not in k]
        self.assertLess(t._preferred_kex.index(modern[0]), t._preferred_kex.index("diffie-hellman-group1-sha1"),
                        "modern key exchange must stay preferred")

    def test_capture_uses_what_the_full_run_learned(self):
        """Regression (live): capture probed from scratch and timed out on a switch the run reads fine."""
        import types
        from netmon.store import Store
        store = Store(self.cfg["collector"]["data_dir"])
        store.write({"10.0.0.9": "cisco_s300"}, "state", "platforms.json")
        store.write({"10.0.0.9": "set2"}, "state", "credentials.json")
        calls = []

        def collect(ip, platform, user, pw, cfg, raw_sink=None):
            calls.append((platform, user))
            return {"ok": True, "error": None, "warnings": [], "hostname": "h", "model": "m", "serial": "s",
                    "version": "v", "neighbors": [], "ports": [], "poe": None, "cpu": None}
        args = types.SimpleNamespace(wlc=None, ip="10.0.0.9", platform="auto", cred_set="")
        with mock.patch("netmon.creds.switch_sets", return_value=[("set1", "u1", "p1"), ("set2", "u2", "p2")]), \
                mock.patch("netmon.devices.detect_platform") as detect, \
                mock.patch("netmon.devices.collect", side_effect=collect), mock.patch("builtins.print"):
            self.assertEqual(cli._capture(self.cfg, args), 0)
        detect.assert_not_called()
        self.assertEqual(calls, [("cisco_s300", "u2")], "learned driver and learned account first")

    def test_fingerprint_reads_only_banners_and_titles(self):
        from netmon.store import Store
        store = Store(self.cfg["collector"]["data_dir"])
        dev = lambda ip, model, status="ok": {"ip": ip, "model": model, "status": status, "hostname": "h" + ip[-1]}
        store.write({"devices": [dev("10.0.0.1", "N9K-C93180YC-FX"), dev("10.0.0.2", "C9300-48P"), dev("10.0.0.3", "C9300-48P"),
                                 dev("10.0.0.4", "CBS350-24P-4G"), dev("10.0.0.5", "C9300-48P", "failed"),
                                 dev("10.0.0.6", "SG350-28P", "down")]}, "latest.json")
        banners = {"10.0.0.1": "SSH-2.0-OpenSSH_8.3", "10.0.0.2": "SSH-2.0-Cisco-1.25", "10.0.0.3": "SSH-2.0-Cisco-1.25",
                   "10.0.0.4": "SSH-2.0-OpenSSH_7.4"}
        pages = {"10.0.0.4": "Cisco CBS350 | "}
        boom = AssertionError("no login")
        out = []
        with mock.patch("netmon.config.load", return_value=self.cfg), mock.patch("logging.basicConfig"), \
                mock.patch("logging.FileHandler"), \
                mock.patch("netmon.scan.ssh_banners", side_effect=lambda ips, *a, **k: {ip: banners.get(ip) for ip in ips}) as sb, \
                mock.patch("netmon.identify.web_hint", side_effect=lambda ip, ports, timeout=4.0: pages.get(ip, "")) as wh, \
                mock.patch("netmiko.ConnectHandler", side_effect=boom), mock.patch("netmon.devices.collect", side_effect=boom), \
                mock.patch("netmon.devices.detect_platform", side_effect=boom), mock.patch("netmon.creds.switch_sets", side_effect=boom), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            self.assertEqual(cli.main(["fingerprint"]), 0)
        self.assertEqual(sorted(sb.call_args.args[0]), ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"], "status ok only")
        self.assertEqual(sorted(c.args[0] for c in wh.call_args_list), ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"])
        self.assertEqual(wh.call_args_list[0].args[1], [443, 80])
        with open(store.path("fingerprint.txt"), encoding="utf-8") as fh:
            text = fh.read()
        lines = [l for l in text.splitlines() if " | " in l and not l.startswith("#")]
        self.assertEqual(lines, ["C9300-48P | SSH-2.0-Cisco-1.25 | (none) | (none) | 2",
                                 "CBS350-24P-4G | SSH-2.0-OpenSSH_7.4 | Cisco CBS350 |  | 1",
                                 "N9K-C93180YC-FX | SSH-2.0-OpenSSH_8.3 | (none) | (none) | 1"])
        self.assertIn("N9K-C93180YC-FX", text.split("Not recognised as Cisco")[1], "models the patterns miss are named")
        self.assertNotIn("CBS350", text.split("Not recognised as Cisco")[1])
        self.assertTrue(any("fingerprint.txt" in l for l in out))

    def _raw(self, **collector):
        return {"collector": {"switch_username": "u", **collector}, "subnets": [{"cidr": "10.0.0.0/30"}]}

    def test_management_ranges(self):
        import ipaddress
        cfg = config.validate(self._raw(management_ranges=["10.20.0.0/16", "10.30.5.9/24"]))
        col = cfg["collector"]
        self.assertEqual(col["management_nets"], [ipaddress.ip_network("10.20.0.0/16"), ipaddress.ip_network("10.30.5.0/24")])
        self.assertTrue(config.in_management(cfg, "10.30.5.200"))
        self.assertFalse(config.in_management(cfg, "10.40.0.1"))
        self.assertFalse(config.in_management(cfg, "not-an-ip"))
        bare = config.validate(self._raw())
        self.assertEqual((bare["collector"]["management_ranges"], bare["collector"]["management_nets"]), ([], []))
        self.assertFalse(config.in_management(bare, "10.30.5.200"))
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self._raw(management_ranges=["bad"]))
        self.assertIn("collector.management_ranges", str(ctx.exception))

    def test_check_config_reports_management_ranges(self):
        cfg = config.validate(self._raw(management_ranges=["10.20.0.0/16", "10.30.5.9/24"]))
        out = []
        with mock.patch("netmon.config.load", return_value=cfg), mock.patch("logging.basicConfig"),                 mock.patch("logging.FileHandler"), mock.patch("netmon.creds.status", return_value=[]),                 mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            cli.main(["check-config"])
        self.assertTrue(any("management ranges: 2 (1 with host bits, read as 10.30.5.0/24)" in l for l in out), out)

    def test_store_rejects_unknown_set(self):
        with self.assertRaises(creds.CredentialError):
            creds.store(self.cfg, "switch", "x", "set9")

    def test_forget_command_passes_ip_by_and_lock(self):
        with mock.patch("netmon.config.load", return_value=self.cfg), mock.patch("logging.basicConfig"), \
                mock.patch("logging.FileHandler"), mock.patch("netmon.run.forget_switch", return_value=0) as forget:
            self.assertEqual(cli.main(["forget", "--ip", "10.0.0.2", "--by", "net@somaiya.edu", "--locked"]), 0)
            forget.assert_called_once_with(self.cfg, "10.0.0.2", "net@somaiya.edu", locked=True)
            cli.main(["forget", "--ip", "10.0.0.2"])
            forget.assert_called_with(self.cfg, "10.0.0.2", "", locked=False)

    def test_forget_help_names_down_and_ssh_failed(self):
        """R10: the CLI's forget is the same Remove as the dashboard: Down or SSH failed rows."""
        out = io.StringIO()
        with mock.patch("sys.stdout", out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn("remove a Down or SSH-failed switch", " ".join(out.getvalue().split()))


if __name__ == "__main__":
    unittest.main()
