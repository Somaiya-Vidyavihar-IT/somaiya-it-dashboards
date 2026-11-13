"""netmon command line.

  python -m netmon [--config PATH] run [--trigger scheduled|manual] [--by EMAIL]
  python -m netmon [--config PATH] reach
  python -m netmon [--config PATH] refresh --ip 10.0.0.1 [--by EMAIL]
  python -m netmon [--config PATH] serve [--dev-http]
  python -m netmon [--config PATH] capture --ip 10.0.0.1 [--platform auto|cisco_ios|cisco_nxos|cisco_s300] | --wlc 10.0.0.5
  python -m netmon [--config PATH] cctv
  python -m netmon [--config PATH] set-password switch --set NAME | wlc | solarwinds
  python -m netmon [--config PATH] check-config
  python -m netmon [--config PATH] make-cert --host netmon.somaiya.edu [--ip VM_IP]
  python -m netmon [--config PATH] import-pfx wildcard.pfx
"""
import argparse
import getpass
import logging
import os
import sys

from . import config as config_mod


def main(argv=None):
    ap = argparse.ArgumentParser(prog="netmon", description="Read-only campus network collector and API")
    ap.add_argument("--config", default=None, help="config.toml path (default: NETMON_CONFIG or C:\\netmon\\config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="full discovery run (LLD workflow)")
    r.add_argument("--trigger", default="scheduled", choices=["scheduled", "manual"])
    r.add_argument("--by", default="")
    sub.add_parser("reach", help="15-minute TCP/22 reachability check")
    f = sub.add_parser("refresh", help="re-read one switch from the last full run and rebuild the snapshot")
    f.add_argument("--ip", required=True)
    f.add_argument("--by", default="")
    f.add_argument("--locked", action="store_true", help=argparse.SUPPRESS)  # the API already took refresh.lock
    sub.add_parser("cctv", help="read cameras and NVRs from SolarWinds once (the API does this every few minutes)")
    s = sub.add_parser("serve", help="HTTPS API for the hub page")
    s.add_argument("--dev-http", action="store_true", help="local testing only: plain HTTP on 127.0.0.1")
    c = sub.add_parser("capture", help="save raw read-only command output from one device for parser checks")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--ip")
    g.add_argument("--wlc")
    c.add_argument("--platform", default="auto", choices=sorted(config_mod.PLATFORMS))
    c.add_argument("--set", dest="cred_set", default="", help="switch credential set to use (default: try each)")
    p = sub.add_parser("set-password", help="store a password in Windows Credential Manager")
    p.add_argument("kind", choices=["switch", "wlc", "solarwinds"])
    p.add_argument("--set", dest="cred_set", default="", help="switch credential set name from config.toml")
    sub.add_parser("check-config", help="validate config.toml and credentials")
    x = sub.add_parser("import-pfx", help="convert the wildcard certificate (.pfx) to the PEM files in config.toml")
    x.add_argument("pfx")
    m = sub.add_parser("make-cert", help="create a self-signed certificate for the API (until a trusted one is used)")
    m.add_argument("--host", required=True, help="DNS name users open, e.g. netmon.somaiya.edu")
    m.add_argument("--ip", action="append", default=[], help="also valid for this IP address (repeatable)")
    args = ap.parse_args(argv)

    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 2
    data_dir = cfg["collector"]["data_dir"]
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(data_dir, "logs", f"netmon-{args.cmd}.log"), encoding="utf-8"),
                  logging.StreamHandler()])
    # Netmiko/paramiko are chatty and print full tracebacks for every refused SSH session; each
    # failure is already recorded on that switch's record (Findings tab), so keep them quiet.
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    logging.getLogger("netmiko").setLevel(logging.WARNING)

    if args.cmd == "run":
        from .run import full_run
        return full_run(cfg, args.trigger, args.by)
    if args.cmd == "refresh":
        from .run import refresh_switch
        return refresh_switch(cfg, args.ip, args.by, locked=args.locked)
    if args.cmd == "reach":
        from .run import reach_run
        return reach_run(cfg)
    if args.cmd == "cctv":
        return _cctv(cfg)
    if args.cmd == "serve":
        from .server import serve
        serve(cfg, dev_http=args.dev_http)
        return 0
    if args.cmd == "capture":
        return _capture(cfg, args)
    if args.cmd == "set-password":
        from . import creds
        if args.kind == "switch" and not args.cred_set:
            names = ", ".join(s["name"] for s in cfg["collector"]["switch_credential_sets"])
            raise SystemExit(f"Add --set NAME. Configured switch credential sets: {names}")
        label = {"switch": f"switch set '{args.cred_set}'", "wlc": "WLC", "solarwinds": "SolarWinds (read-only)"}[args.kind]
        pw = getpass.getpass(f"Password for {label} (typing is hidden): ")
        if not pw or pw != getpass.getpass("Type it again: "):
            raise SystemExit("Passwords were empty or did not match; nothing stored.")
        user = creds.store(cfg, args.kind, pw, args.cred_set or None)
        print(f"Stored the {label} password for user {user} in Windows Credential Manager.")
        return 0
    if args.cmd == "make-cert":
        return _make_cert(cfg, args.host, args.ip)
    if args.cmd == "import-pfx":
        return _import_pfx(cfg, args.pfx)
    if args.cmd == "check-config":
        from . import creds
        print(f"Config OK: {len(cfg['subnets'])} subnets, {len(cfg['wlc']['hosts'])} WLCs, "
              f"{len(cfg['server']['network_team'])} network-team emails")
        missing = 0
        for label, user, stored in creds.status(cfg):
            print(f"  {label:20} user {user or '(none)':20} password {'stored' if stored else 'MISSING'}")
            missing += not stored
        if not cfg["collector"]["seeds"] and not any(s["sweep"] for s in cfg["subnets"]):
            print("  WARNING: collector.seeds is empty; with sweep = false nothing will be discovered.")
        for f in (cfg["server"]["cert_file"], cfg["server"]["key_file"]):
            print(f"  {'found  ' if f and os.path.exists(f) else 'MISSING'} {f or '(not set)'}")
        cc = cfg["cctv"]
        if cc["enabled"] and cc["source"] == "sheet":
            sa = cc["service_account_file"]
            print(f"  CCTV: sheet {cc['sheet_id'][:12]}..., tabs {', '.join(cc['sheet_tabs'])}, {cc['check']} check, "
                  f"{len(cc['viewers'])} viewer emails, every {cc['poll_minutes']} min")
            print(f"  {'found  ' if os.path.exists(sa) else 'MISSING'} {sa} (service account key for the CCTV sheet)")
            missing += not os.path.exists(sa)
        elif cc["enabled"]:
            print(f"  CCTV: SolarWinds {cc['solarwinds_host']}:{cc['solarwinds_port']}, {len(cc['viewers'])} viewer emails, "
                  f"{len(cc['camera_ranges_nets'])} camera ranges, every {cc['poll_minutes']} min")
        else:
            print("  CCTV: off (set cctv.sheet_id with source = \"sheet\", or cctv.solarwinds_host)")
        if cc["enabled"] and not cc["viewers"]:
            print("  WARNING: cctv.viewers is empty; nobody can open the CCTV dashboard.")
        return 1 if missing else 0


def _cctv(cfg):
    from . import cctv
    from .store import Store
    if not cfg["cctv"]["enabled"]:
        raise SystemExit("CCTV is off: set cctv.sheet_id (source = \"sheet\") or cctv.solarwinds_host in config.toml first")
    store = Store(cfg["collector"]["data_dir"])
    st = cctv.poll_once(cfg, store, by="command line")
    if not st["ok"]:
        print(f"CCTV read failed: {st['error']}")
        return 1
    snap = store.read("cctv.json", default={}) or {}
    s = snap.get("summary", {})
    cam, nvr = s.get("cameras", {}), s.get("nvrs", {})
    if snap.get("source") == "sheet":
        print(f"Checked {snap.get('nodesRead')} devices from the CCTV sheet ({', '.join(cfg['cctv']['sheet_tabs'])}) "
              f"by {cfg['cctv']['check']} in {st.get('seconds')} s.")
        if snap.get("sheetError"):
            print(f"  WARNING: sheet read failed, using the copy from {snap.get('sheetReadAt')}: {snap['sheetError']}")
        print(f"  Sheet problems (bad/duplicate IPs, unknown types): {s.get('sheetProblems')}")
    else:
        print(f"Read {snap.get('nodesRead')} SolarWinds nodes in {st.get('seconds')} s.")
    print(f"  Cameras: {cam.get('total')} ({cam.get('up')} up, {cam.get('warning')} warning, {cam.get('down')} down, "
          f"{cam.get('unmonitored')} not monitored)")
    print(f"  NVRs:    {nvr.get('total')} ({nvr.get('up')} up, {nvr.get('warning')} warning, {nvr.get('down')} down)")
    print(f"  Down more than {cfg['cctv']['escalate_hours']} h: {s.get('escalated')}")
    if snap.get("source") != "sheet" and cfg["cctv"]["ipam_ranges_nets"]:
        print(f"  Live in IPAM CCTV ranges but not in SolarWinds: {s.get('notInSolarWinds')}"
              + (f" (IPAM check failed: {snap.get('notInSolarWindsError')})" if snap.get("notInSolarWindsError") else ""))
    print(f"Saved {store.path('cctv.json')}")
    return 0


def _make_cert(cfg, host, ips):
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    cert_file, key_file = cfg["server"]["cert_file"], cfg["server"]["key_file"]
    if not cert_file or not key_file:
        raise SystemExit("Set server.cert_file and server.key_file in config.toml first")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Somaiya Vidyavihar IT (netmon self-signed)")])
    alt = [x509.DNSName(host)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=397))  # browsers reject longer server certs
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    os.makedirs(os.path.dirname(cert_file), exist_ok=True)
    with open(cert_file, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_file, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    print(f"Self-signed certificate for {host} written to {cert_file}, valid until {cert.not_valid_after_utc:%Y-%m-%d}.")
    print("Each team member opens https://" + host + ":" + str(cfg["server"]["port"]) + "/api/v1/health once and accepts the warning.")
    return 0


def _import_pfx(cfg, pfx_path):
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
    cert_file, key_file = cfg["server"]["cert_file"], cfg["server"]["key_file"]
    if not cert_file or not key_file:
        raise SystemExit("Set server.cert_file and server.key_file in config.toml first")
    password = getpass.getpass("PFX password (blank if none): ").encode() or None
    with open(pfx_path, "rb") as fh:
        key, cert, chain = pkcs12.load_key_and_certificates(fh.read(), password)
    if key is None or cert is None:
        raise SystemExit("The PFX does not contain both a private key and a certificate")
    os.makedirs(os.path.dirname(cert_file), exist_ok=True)
    with open(cert_file, "wb") as fh:
        for c in [cert, *(chain or [])]:
            fh.write(c.public_bytes(Encoding.PEM))
    with open(key_file, "wb") as fh:
        fh.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    print(f"Wrote {cert_file} (certificate + {len(chain or [])} chain) and {key_file}.")
    print("Restrict the key to the service account, e.g.:")
    print(f'  icacls "{key_file}" /inheritance:r /grant:r "<SERVICE_ACCOUNT>:R" "Administrators:F"')
    return 0


def _capture(cfg, args):
    from . import creds, devices, wlc
    from .store import Store, stamp
    store = Store(cfg["collector"]["data_dir"])
    raw = {}
    if args.wlc:
        user, pw = creds.get(cfg)
        rec = wlc.collect(args.wlc, user, pw, cfg, raw_sink=raw)
        target, platform = args.wlc, "aruba_os"
    else:
        sets = [s for s in creds.switch_sets(cfg) if not args.cred_set or s[0] == args.cred_set]
        if not sets:
            raise SystemExit(f"No stored password for credential set {args.cred_set!r}")
        # Same knowledge as the full run: the driver and credential set that worked last time.
        learned = (store.read("state", "platforms.json", default={}) or {}).get(args.ip)
        known_set = (store.read("state", "credentials.json", default={}) or {}).get(args.ip)
        sets = sorted(sets, key=lambda s: s[0] != known_set)
        target, platform, rec, errors, logs = args.ip, args.platform, None, [], []
        for name, user, pw in sets:
            raw.clear()
            # Login trace per credential set, to see where a stalled login stops.
            cfg["collector"]["_session_log"] = store.path("captures", f"login-{args.ip}-{name}-{stamp()}.log")
            logs.append(cfg["collector"]["_session_log"])
            try:
                platform = args.platform if args.platform != "auto" else learned or devices.detect_platform(args.ip, user, pw, cfg)
            except Exception as exc:
                errors.append(f"set {name}: {devices._short_error(exc)}")
                print(f"Credential set {name}: {devices._short_error(exc)}, trying the next one")
                continue
            rec = devices.collect(args.ip, platform, user, pw, cfg, raw_sink=raw)
            if not rec["ok"]:
                errors.append(f"set {name}: {rec['error']}")
                print(f"Credential set {name}: {rec['error']}, trying the next one")
                rec = None
                continue
            print(f"Logged in with credential set {name} as {platform}")
            break
        cfg["collector"].pop("_session_log", None)
        if rec is None:
            print("Login traces (what the switch printed; passwords are masked):")
            for log_path in logs:
                print(f"  {log_path}")
            raise SystemExit(f"Could not log in to {args.ip}: " + "; ".join(errors))
    path = store.path("captures", f"capture-{target}-{stamp()}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# netmon capture {target} platform={platform}\n# Contains real device output: keep it out of git.\n")
        for cmd, text in raw.items():
            fh.write(f"\n===== {cmd} =====\n{text}\n")
    print(f"Saved raw output to {path}")
    print(f"Parsed: ok={rec['ok']} error={rec.get('error')} warnings={rec.get('warnings')}")
    if not args.wlc:
        print(f"  hostname={rec['hostname']} model={rec['model']} serial={rec['serial']} version={rec['version']}")
        print(f"  neighbours={len(rec['neighbors'])} ports={len(rec['ports'])} poe={rec['poe']} cpu={rec['cpu']}")
    else:
        print(f"  aps={len(rec['aps'])} clients={len(rec['clients'])} apLldp={len(rec['apLldp'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
