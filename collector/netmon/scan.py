"""TCP reachability probes: the campus IP scan (tcp 22/443/80), SSH banner reads for the Cisco
check, and the 15-minute up/down check."""
import asyncio
import re
import time


async def _probe(ip, port, timeout, sem):
    async with sem:
        start = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
        except (OSError, asyncio.TimeoutError):
            return ip, None
        latency = round((time.perf_counter() - start) * 1000, 1)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return ip, latency


async def _probe_all(ips, port, timeout, concurrency):
    sem = asyncio.Semaphore(concurrency)
    return dict(await asyncio.gather(*(_probe(ip, port, timeout, sem) for ip in ips)))


def probe(ips, port=22, timeout=1.5, concurrency=512):
    """Return {ip: latency_ms or None}. None means the port did not answer."""
    ips = list(dict.fromkeys(ips))
    if not ips:
        return {}
    return asyncio.run(_probe_all(ips, port, timeout, concurrency))


BANNER_MAX = 255  # RFC 4253: the identification line is at most 255 bytes including CR LF
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


async def _banner(ip, port, timeout, sem):
    """Read the server's first line on connect. Nothing is ever written to the socket (no SSH
    handshake, no authentication); the connection is closed as soon as the line is in."""
    async with sem:
        writer = None

        async def read_line():
            nonlocal writer
            reader, writer = await asyncio.open_connection(ip, port)
            buf = b""
            while b"\n" not in buf and len(buf) < BANNER_MAX:
                chunk = await reader.read(BANNER_MAX - len(buf))
                if not chunk:
                    break
                buf += chunk
            return buf
        try:
            buf = await asyncio.wait_for(read_line(), timeout)
        except (OSError, asyncio.TimeoutError):
            return ip, None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
    line = buf[:BANNER_MAX].split(b"\n", 1)[0].rstrip(b"\r")
    if not line.startswith(b"SSH-"):
        return ip, None  # silent, slow or not an SSH server: never Cisco
    return ip, _CONTROL.sub("", line.decode("utf-8", "replace"))


async def _banners_all(ips, timeout, concurrency, port):
    sem = asyncio.Semaphore(concurrency)
    return dict(await asyncio.gather(*(_banner(ip, port, timeout, sem) for ip in ips)))


def ssh_banners(ips, timeout=3.0, concurrency=256, port=22):
    """Return {ip: SSH identification line or None} for tcp/22. None: no answer, timeout, EOF or a
    first line that does not start with "SSH-". Read-only: the collector sends nothing."""
    ips = list(dict.fromkeys(ips))
    if not ips:
        return {}
    return asyncio.run(_banners_all(ips, timeout, concurrency, port))


def sweep_targets(cfg):
    """All host addresses in sweep-enabled subnets, minus exclusions."""
    excluded = cfg["collector"]["exclude_nets"]
    seen = set()
    for s in cfg["subnets"]:
        if not s["sweep"]:
            continue
        for addr in s["net"].hosts():
            ip = str(addr)
            if ip not in seen and not any(addr in n for n in excluded):
                seen.add(ip)
                yield ip
