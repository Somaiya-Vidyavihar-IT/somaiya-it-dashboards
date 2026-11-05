"""netmon command line.

  python -m netmon [--config PATH] run [--trigger scheduled|manual] [--by EMAIL]
  python -m netmon [--config PATH] reach
  python -m netmon [--config PATH] serve [--dev-http]
  python -m netmon [--config PATH] capture --ip 10.0.0.1 [--platform auto|cisco_ios|cisco_nxos|cisco_s300] | --wlc 10.0.0.5
  python -m netmon [--config PATH] set-password switch|wlc
  python -m netmon [--config PATH] check-config
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
    s = sub.add_parser("serve", help="HTTPS API for the hub page")
    s.add_argument("--dev-http", action="store_true", help="local testing only: plain HTTP on 127.0.0.1")
    c = sub.add_parser("capture", help="save raw read-only command output from one device for parser checks")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--ip")
    g.add_argument("--wlc")
    c.add_argument("--platform", default="auto", choices=sorted(config_mod.PLATFORMS))
    p = sub.add_parser("set-password", help="store a password in Windows Credential Manager")
    p.add_argument("kind", choices=["switch", "wlc"])
    sub.add_parser("check-config", help="validate config.toml and credentials")
    x = sub.add_parser("import-pfx", help="convert the wildcard certificate (.pfx) to the PEM files in config.toml")
    x.add_argument("pfx")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config)
    data_dir = cfg["collector"]["data_dir"]
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(data_dir, "logs", f"netmon-{args.cmd}.log"), encoding="utf-8"),
                  logging.StreamHandler()])
    # Netmiko/paramiko are chatty at INFO and can echo device banners; keep them quiet.
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("netmiko").setLevel(logging.WARNING)

    if args.cmd == "run":
        from .run import full_run
        return full_run(cfg, args.trigger, args.by)
    if args.cmd == "reach":
        from .run import reach_run
        return reach_run(cfg)
    if args.cmd == "serve":
        from .server import serve
        serve(cfg, dev_http=args.dev_http)
        return 0
    if args.cmd == "capture":
        return _capture(cfg, args)
    if args.cmd == "set-password":
        from . import creds
        pw = getpass.getpass(f"{args.kind} password: ")
        user = creds.store(cfg, args.kind, pw)
        print(f"Stored {args.kind} password for user {user} in Windows Credential Manager.")
        return 0
    if args.cmd == "import-pfx":
        return _import_pfx(cfg, args.pfx)
    if args.cmd == "check-config":
        from . import creds
        print(f"Config OK: {len(cfg['subnets'])} subnets, {len(cfg['wlc']['hosts'])} WLCs, "
              f"{len(cfg['server']['network_team'])} network-team emails")
        for kind in ("switch",) + (("wlc",) if cfg["wlc"]["hosts"] else ()):
            try:
                creds.get(cfg, kind)
                print(f"{kind} credentials: found")
            except creds.CredentialError as exc:
                print(f"{kind} credentials: MISSING - {exc}")
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
        user, pw = creds.get(cfg, "wlc")
        rec = wlc.collect(args.wlc, user, pw, cfg, raw_sink=raw)
        target, platform = args.wlc, "aruba_os"
    else:
        user, pw = creds.get(cfg, "switch")
        platform = args.platform if args.platform != "auto" else devices.detect_platform(args.ip, user, pw, cfg)
        rec = devices.collect(args.ip, platform, user, pw, cfg, raw_sink=raw)
        target = args.ip
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
