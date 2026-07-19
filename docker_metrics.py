"""docker_metrics.py — collectors for Docker Engine resource usage.

Talks to the Docker Engine API over /var/run/docker.sock (mirrors what
`docker system df` and `docker stats` show), so the exporter container
needs that socket bind-mounted read-only — see docker-compose.yml.
"""

from concurrent.futures import ThreadPoolExecutor

import docker
from docker.errors import DockerException

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get_docker_overview() -> dict:
    """Counts and disk usage for images, containers, volumes, and build cache."""
    try:
        client = _get_client()
        df = client.api.df()
        containers = client.containers.list(all=True)
    except DockerException:
        return {"available": False}

    images = df.get("Images") or []
    volumes = df.get("Volumes") or []
    build_cache = df.get("BuildCache") or []

    running = sum(1 for c in containers if c.status == "running")

    images_size = sum(i.get("Size", 0) for i in images)
    images_reclaimable = sum(i.get("Size", 0) for i in images if i.get("Containers", 0) == 0)

    volumes_size = sum((v.get("UsageData") or {}).get("Size") or 0 for v in volumes)
    volumes_reclaimable = sum(
        (v.get("UsageData") or {}).get("Size") or 0
        for v in volumes
        if (v.get("UsageData") or {}).get("RefCount", 0) == 0
    )

    cache_size = sum(b.get("Size", 0) for b in build_cache)
    cache_reclaimable = sum(b.get("Size", 0) for b in build_cache if not b.get("InUse"))

    return {
        "available": True,
        "containers_total": len(containers),
        "containers_running": running,
        "containers_stopped": len(containers) - running,
        "images_total": len(images),
        "images_size_bytes": images_size,
        "images_reclaimable_bytes": images_reclaimable,
        "volumes_total": len(volumes),
        "volumes_size_bytes": volumes_size,
        "volumes_reclaimable_bytes": volumes_reclaimable,
        "build_cache_entries": len(build_cache),
        "build_cache_size_bytes": cache_size,
        "build_cache_reclaimable_bytes": cache_reclaimable,
    }


def get_container_stats() -> list[dict]:
    """Per-container CPU %, memory usage, and status.

    Each running container needs its own blocking `stats(stream=False)` call
    (~1s apiece against Docker Desktop's socket proxy), so they're fetched
    concurrently — sequential calls easily blow past Prometheus's scrape
    timeout once there are more than a handful of containers.
    """
    try:
        client = _get_client()
        containers = client.containers.list(all=True)
    except DockerException:
        return []

    def _entry(c) -> dict:
        entry = {
            "name": c.name,
            "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            "status": c.status,
            "cpu_percent": 0.0,
            "memory_bytes": 0,
            "memory_limit_bytes": 0,
        }
        if c.status == "running":
            try:
                entry.update(_running_stats(c.stats(stream=False)))
            except DockerException:
                pass
        return entry

    if not containers:
        return []
    with ThreadPoolExecutor(max_workers=len(containers)) as pool:
        return list(pool.map(_entry, containers))


def _running_stats(stats: dict) -> dict:
    mem = stats.get("memory_stats", {})
    return {
        "cpu_percent": _cpu_percent(stats),
        "memory_bytes": mem.get("usage", 0),
        "memory_limit_bytes": mem.get("limit", 0),
    }


def _cpu_percent(stats: dict) -> float:
    try:
        cpu = stats["cpu_stats"]
        precpu = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - precpu["system_cpu_usage"]
        online_cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        if system_delta > 0 and cpu_delta > 0:
            return (cpu_delta / system_delta) * online_cpus * 100.0
    except (KeyError, ZeroDivisionError):
        pass
    return 0.0
