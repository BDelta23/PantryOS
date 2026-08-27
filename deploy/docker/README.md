# PantryOS NAS Docker Deployment

This deployment runs PantryOS Core on a NAS or other always-on local Docker host. Home Assistant should connect from another machine using the NAS LAN address, for example `http://<NAS-LAN-IP>:8765`. Do not use `127.0.0.1` in Home Assistant unless Home Assistant and PantryOS Core run on the same host.

## Install

1. Copy `.env.example` to `.env`.
2. Set `PANTRYOS_API_TOKEN` to a long random value.
3. Keep `PANTRYOS_LISTEN_HOST=0.0.0.0` so Docker can publish the service to the LAN.
4. Run `docker compose up -d --build` from this directory when building locally, or set `PANTRYOS_IMAGE` to a published image tag and run `docker compose up -d`.
5. Confirm readiness from another LAN machine with `curl http://<NAS-LAN-IP>:8765/api/v1/health/ready`.
6. Open `http://<NAS-LAN-IP>:8765` and sign in with the same token.

## Persistent Data

The compose file bind-mounts `./data` to `/data` in the container. The SQLite database is `/data/pantryos.sqlite3`; browser sessions, receipt uploads, migration backups, and backup archives also live under `/data` unless explicitly configured otherwise. Deleting and recreating the container must not delete this directory.

## Updates

For a published image, set `PANTRYOS_IMAGE` to an explicit version such as `ghcr.io/<owner>/<repo>:0.1.0`, run `docker compose pull`, then run `docker compose up -d`. Keep `./data` in place.

Do not expose port 8765 directly to the public internet. PantryOS is designed for a trusted local network with token authentication.
