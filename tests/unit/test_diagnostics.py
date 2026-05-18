from django.test import override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory


def test_pool_diagnostics_endpoint_reports_capacity_budget():
    resource_limits = {
        "internal_pool": 9,
        "checkout": 7,
        "payment": 5,
        "batch": 3,
    }
    with override_settings(
        INSTANCE_ID="unit-instance",
        GUNICORN_WORKERS=2,
        GUNICORN_THREADS=3,
        GUNICORN_WORKER_CLASS="sync",
        GUNICORN_TIMEOUT=30,
        CELERY_CONCURRENCY=4,
        INTERNAL_POOL_MAX_CONCURRENCY=9,
        RESOURCE_ACQUIRE_TIMEOUT_SECONDS=0.25,
        RESOURCE_LIMITS=resource_limits,
    ):
        match = resolve("/api/v1/_diag/pool/")
        request = APIRequestFactory().get("/api/v1/_diag/pool/")
        response = match.func(request)

    assert response.status_code == 200
    assert response.data["instance_id"] == "unit-instance"
    assert response.data["outer_caps"] == {
        "gunicorn_workers": 2,
        "gunicorn_threads": 3,
        "gunicorn_worker_class": "sync",
        "gunicorn_timeout": 30,
        "celery_concurrency": 4,
    }
    assert response.data["resource_acquire_timeout_seconds"] == 0.25
    assert response.data["pools"]["checkout"]["limit"] == 7
    assert response.data["pools"]["payment"]["limit"] == 5
    assert response.data["pools"]["batch"]["limit"] == 3


def test_process_diagnostics_endpoint_reports_runtime_metrics():
    with override_settings(INSTANCE_ID="unit-instance"):
        match = resolve("/api/v1/_diag/process/")
        request = APIRequestFactory().get("/api/v1/_diag/process/")
        response = match.func(request)

    assert response.status_code == 200
    assert response.data["instance_id"] == "unit-instance"
    assert response.data["pid"] > 0
    assert response.data["uptime_seconds"] >= 0
    assert response.data["cpu"]["process_cpu_seconds"] >= 0
    assert response.data["cpu"]["process_cpu_per_uptime_percent"] >= 0
    assert "system_load_average" in response.data["cpu"]
    assert "rss_kb" in response.data["memory"]
    assert "peak_rss_kb" in response.data["memory"]
    assert response.data["threads"]["python_active_count"] >= 1
    assert "process_thread_count" in response.data["threads"]
