"""TCP reachability probes: the LLD's port-22 sweep and the 15-minute up/down check."""
import asyncio
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
