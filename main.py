"""
main.py — FastAPI Prometheus exporter for Docker Engine resource usage
Tracks images, containers, volumes, and build cache disk usage — the things
`docker system df` and `docker stats` show — as Prometheus metrics.
- Exposes /metrics for Prometheus scraping
- Exposes /api/docker for direct inspection
- Interactive Swagger UI available at /docs
"""

import argparse

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from docker_metrics import get_docker_overview, get_container_stats

app = FastAPI(
    title="Docker Resource Exporter",
    description="Prometheus exporter for Docker image/container/volume/build-cache disk usage.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Prometheus Gauges
# ---------------------------------------------------------------------------
DOCKER_UP                      = Gauge("docker_exporter_up",            "1 if the Docker Engine API is reachable, else 0")
DOCKER_CONTAINERS              = Gauge("docker_containers",             "Container count by state",                       ["state"])
DOCKER_IMAGES_TOTAL            = Gauge("docker_images_total",           "Number of images")
DOCKER_IMAGES_SIZE             = Gauge("docker_images_size_bytes",      "Total size of all images")
DOCKER_IMAGES_RECLAIMABLE      = Gauge("docker_images_reclaimable_bytes", "Size of images unused by any container")
DOCKER_VOLUMES_TOTAL           = Gauge("docker_volumes_total",          "Number of volumes")
DOCKER_VOLUMES_SIZE            = Gauge("docker_volumes_size_bytes",     "Total size of all volumes")
DOCKER_VOLUMES_RECLAIMABLE     = Gauge("docker_volumes_reclaimable_bytes", "Size of volumes unused by any container")
DOCKER_BUILD_CACHE_SIZE        = Gauge("docker_build_cache_size_bytes", "Total size of the build cache")
DOCKER_BUILD_CACHE_RECLAIMABLE = Gauge("docker_build_cache_reclaimable_bytes", "Size of build cache entries not in use")
DOCKER_CONTAINER_CPU           = Gauge("docker_container_cpu_percent",  "Per-container CPU %",                            ["name", "image"])
DOCKER_CONTAINER_MEMORY        = Gauge("docker_container_memory_bytes", "Per-container memory usage",                     ["name", "image"])
DOCKER_CONTAINER_RUNNING       = Gauge("docker_container_running",      "1 if the container is running, else 0",          ["name", "image"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_gauges() -> dict:
    """Collect Docker Engine resource usage, push to Prometheus gauges, return raw values."""
    overview = get_docker_overview()
    DOCKER_UP.set(1 if overview.get("available") else 0)
    if not overview.get("available"):
        return {"overview": overview, "containers": []}

    DOCKER_CONTAINERS.labels(state="running").set(overview["containers_running"])
    DOCKER_CONTAINERS.labels(state="stopped").set(overview["containers_stopped"])
    DOCKER_IMAGES_TOTAL.set(overview["images_total"])
    DOCKER_IMAGES_SIZE.set(overview["images_size_bytes"])
    DOCKER_IMAGES_RECLAIMABLE.set(overview["images_reclaimable_bytes"])
    DOCKER_VOLUMES_TOTAL.set(overview["volumes_total"])
    DOCKER_VOLUMES_SIZE.set(overview["volumes_size_bytes"])
    DOCKER_VOLUMES_RECLAIMABLE.set(overview["volumes_reclaimable_bytes"])
    DOCKER_BUILD_CACHE_SIZE.set(overview["build_cache_size_bytes"])
    DOCKER_BUILD_CACHE_RECLAIMABLE.set(overview["build_cache_reclaimable_bytes"])

    containers = get_container_stats()
    for c in containers:
        labels = dict(name=c["name"], image=c["image"])
        DOCKER_CONTAINER_CPU.labels(**labels).set(c["cpu_percent"])
        DOCKER_CONTAINER_MEMORY.labels(**labels).set(c["memory_bytes"])
        DOCKER_CONTAINER_RUNNING.labels(**labels).set(1 if c["status"] == "running" else 0)

    return {"overview": overview, "containers": containers}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint."""
    _update_gauges()
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/api/docker", summary="Docker images/containers/volumes/build cache snapshot", tags=["Docker"])
def api_docker():
    """Returns image/volume/build-cache disk usage plus per-container CPU/memory, mirroring `docker system df` and `docker stats`."""
    vals = _update_gauges()
    return vals["overview"] | {"containers": vals["containers"]}


@app.get("/health", summary="Health check", tags=["Status"])
def health():
    overview = get_docker_overview()
    return {"status": "healthy", "docker_api_reachable": overview.get("available", False)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Docker resource-usage Prometheus exporter")
    parser.add_argument("--port", type=int, default=8010, help="Port to run the exporter on")
    args = parser.parse_args()

    print(f">>> docker-exp running  |  port={args.port} <<<")
    print(f">>> Swagger UI:         http://localhost:{args.port}/docs")
    print(f">>> Prometheus scrape:  http://localhost:{args.port}/metrics")
    uvicorn.run(app, host="0.0.0.0", port=args.port, reload=False)
