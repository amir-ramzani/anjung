# Running the demo on a home NAS

A home-network demo on any NAS that runs Docker on x86-64 (tested target: UGREEN DXP2800, UGOS Pro).
It uses **mock auth**: anyone who can reach the NAS can sign in as any user with just an email. Do not
forward ports 8443 or 8088 on your router.

## What runs

| Service | Port on NAS | Notes |
|---|---|---|
| `https` (Caddy) | 8443 | open `https://<nas-ip>:8443`. HTTPS for the app and the documents, with a certificate Caddy makes itself (see [HTTPS](#https)) |
| `web` (nginx + built SPA, proxies `/v1` and `/dev` to the API) | 8088 | `http://<nas-ip>:8088` still works, without HTTPS |
| `s3` (SeaweedFS, S3-compatible) | not published | the browser uploads and downloads documents through Caddy (`https://<nas-ip>:8443/anjung-docs/...`). Files live in the `s3-data` volume and survive restarts and updates |
| `api`, `dynamodb` | not published | data lives in the `ddb-data` volume |

Not included: the SQS worker (needs AWS). Jobs run on demand instead, e.g.
`curl -k https://<nas-ip>:8443/dev/jobs/invoice_sweep -H 'content-type: application/json' -d '{"org_id":"<org id>"}'`.

## 1. Build the images (on your PC)

```bash
docker build -t anjung-api backend
docker build -t anjung-web frontend
docker save anjung-api anjung-web | gzip > anjung-images.tar.gz
```

## 2. Copy to the NAS

Copy `anjung-images.tar.gz`, `docker-compose.nas.yml` (renamed to `docker-compose.yml`) and `Caddyfile.nas` into a
shared folder, e.g. `/volume1/docker/anjung/`. Next to it, create `.env` with the NAS's LAN address (give the NAS a fixed IP
or a DHCP reservation, because presigned document URLs and set-password links embed it):

```
NAS_HOST=192.168.0.50
NAS_NAME=mynas.local   # optional: the NAS's network name, so https://mynas.local:8443 works too
```

If your NAS's compose UI ignores `.env`, edit the `192.168.0.100` defaults in the compose file instead.

## 3. Load and start

With the Docker app's UI: import the image archive, then create a project (compose) from the folder.
Or over SSH (enable SSH in the NAS settings):

```bash
cd /volume1/docker/anjung
sudo docker load -i anjung-images.tar.gz
sudo docker compose up -d
sudo docker compose exec api python -m app.cli seed-demo   # demo org, users and invoices (once)
```

Then open `https://<nas-ip>:8443`, accept the certificate warning once (or trust the certificate, below), and sign in as `boss@example.com`, `manager@example.com`, `siti@example.com`,
`ali@example.com` or `root@example.com`.

## Updating

Rebuild and re-export the images on your PC, load them on the NAS, then `sudo docker compose up -d`.
`sudo docker compose down -v` wipes all data, documents included.

Documents uploaded before the switch to SeaweedFS were kept in memory and are already gone: their
records stay, but downloading them fails. Delete them and upload again.

Sample addresses moved to `example.com`, a domain reserved for examples, so no message can reach a real
person. A NAS seeded before that keeps its old users (`boss@demo.my`, `root@anjung-corp.com` and so on), and they still
sign in. The new addresses only appear on a fresh start: `sudo docker compose down -v`, `sudo docker compose up -d`, then
`seed-demo` again. This wipes everything on the demo, uploads included.

## HTTPS

Caddy serves the app at `https://<nas-ip>:8443` with a certificate from its own local certificate authority, kept in
the `caddy-data` volume. HTTPS matters because browsers only allow some features on secure pages, as in production
(copying a set-password link with one click, for example). Uploads and downloads use the same address, so there is
only one certificate.

Browsers do not know Caddy's authority, so the first visit shows a "not private" warning. Either click through it
(Advanced → Proceed) on each device, or trust the authority once per device:

```bash
sudo docker compose cp https:/data/caddy/pki/authorities/local/root.crt ./anjung-demo-root.crt
```

Then install `anjung-demo-root.crt` as a trusted root certificate: on Windows, double-click it → Install → Local
Machine → "Trusted Root Certification Authorities"; on macOS, add it to Keychain Access → System and set it to
Always Trust; on Android and iOS, open it and install it as a CA certificate (iOS also needs Settings → General →
About → Certificate Trust Settings). Trust it only on your own devices. `docker compose down -v` deletes the
authority, and devices need the new one after that.

Once the certificate is trusted, the app can be installed on a phone or computer: "Install the app" in Chrome, Edge
and Android (at the foot of the menu, or on the sign-in page), or Share → "Add to Home Screen" in Safari on iPhone.
Browsers only allow this on a trusted HTTPS address.

**By name.** With `NAS_NAME` set, Caddy also issues a certificate for that name from the same authority, so both
`https://<nas-ip>:8443` and `https://<nas-name>:8443` work, and a device that trusts the root certificate trusts both.
Set-password links and document uploads and downloads still use `NAS_HOST`; with the root certificate trusted that
makes no difference. If you only click through the warning instead, do it for the IP address too, or uploads fail.
Adding the name to an existing NAS: add `NAS_NAME` to `.env`, copy the new `Caddyfile.nas` and `docker-compose.yml`,
then `sudo docker compose up -d`. The authority stays the same, so trusted devices need nothing new.

Set-password links in messages point to the HTTPS address. Upgrading from a version without the `https` service:
copy `Caddyfile.nas` too, then `sudo docker compose up -d`.

## Emails and WhatsApp messages

The demo sends no real email or WhatsApp. Each message, such as the welcome email with a set-password link for a
new tenant or staff member, is written to the API's log instead:

```bash
sudo docker compose logs api | grep "fake email"
```

## Company branding

An org admin can give the company its own sign-in page under Settings → Branding (handle, name, registration
number, colour, logo). It is then at `https://<NAS_HOST>:8443/login/<handle>`. A fresh `seed-demo` creates the demo
company with branding on, at `/login/demo-example-com`.

## Testing the demo from your PC

The same checks as on a developer machine run against the NAS, through the plain-HTTP port. Each run creates its own
throwaway company (named with a run id), so the demo company is left alone. Use the NAS's platform admin, here
`root@anjung-corp.com` for a NAS set up before the switch to `example.com`:

```bash
# API smoke flow, 45 checks (--insecure: the upload goes to the HTTPS port, which uses the NAS's own certificate)
uv run --with httpx smoke/local_flow.py --base-url http://<nas>:8088 --platform-admin root@anjung-corp.com --insecure
# Postman flows, 130 assertions
cd contracts && node postman/run-flows.mjs --base-url http://<nas>:8088 --platform-admin root@anjung-corp.com
# browser journeys and accessibility checks, 9 tests
cd frontend && E2E_BASE_URL=http://<nas>:8088 E2E_API_URL=http://<nas>:8088 E2E_INSECURE=1 \
  PLATFORM_ADMIN_EMAIL=root@anjung-corp.com npx playwright test
```

HTTPS answers at the address in `NAS_HOST` and, when it is set, at `NAS_NAME` (see HTTPS above). Without
`NAS_NAME`, `https://<nas-name>.local:8443` fails the TLS handshake.
