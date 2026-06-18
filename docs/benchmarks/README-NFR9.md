NFR9 Stress Test - Locust

This document explains how to run the NFR9 stress scenario and where reports are stored.

Quick command (bash):

    HOST=http://localhost USERS=100 SPAWN_RATE=10 RUN_TIME=10m ./scripts/run_nfr9.sh

Quick command (PowerShell):

    .\scripts\run_nfr9.ps1 -Host 'http://localhost' -Users 100 -SpawnRate 10 -RunTime '10m'

Notes:
- Ensure Postgres, Redis, web instances and Celery are running before starting the test.
- Seed users 1..300 must exist for the seeded locust scenario; see tests/stress/locustfile.py for details.
- Results (HTML & CSV) will be written to `docs/benchmarks/` with prefix `nfr9-<users>-users`.
