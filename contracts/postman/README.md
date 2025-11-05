# Postman collection

`anjung.postman_collection.json` is **generated** from `../openapi.json`: one folder per tag, every operation, plus the
hand-written `Flows` folder (`flows.mjs`). Do not edit it by hand.

```bash
cd contracts
npm install
npm run postman          # regenerate after changing openapi.json
npm run postman:check    # what CI runs: fails if the committed file is out of date
```

## Using it

1. Import the collection and the environment for dev or staging. There is no prod environment on purpose: the `postman`
   app client does not exist in prod.
2. Fill in `client_id` (the `postman` client of the Auth stack) in the environment. Nothing secret is committed.
3. In the collection's **Authorization** tab press **Get New Access Token**. It signs in through the Cognito hosted UI
   with OAuth 2.0 and PKCE (callback `https://oauth.pstmn.io/v1/callback`). Every request inherits it.
4. Requests that need an `Idempotency-Key` get a fresh one each time they are sent (collection pre-request script).

## Flows

Run the `Flows` folder with the Collection Runner. Each flow creates its own data and asserts as it goes:

| Flow | Covers |
|---|---|
| Onboard tenant | create, invite, consent, duplicate email refused, a shareable set-password link (public look-up, weak password refused), delete the tenant and the link stops working |
| Lease to receipt | property, unit, tenant, lease, activate (signing switched off in the flow's own organisation), invoice run, QR, simulated gateway payment, paid, receipt, ledger |
| Maintenance | raise, triage, assign, start, resolve, close, the refusals after closing, delete the closed request |
| Deactivate and delete | deactivate a property and its unit, reactivate both, preview the delete, a reason required, delete |
| Parking bays | a bay with a unit and a property bay, unique labels, the unit's bay refused on its own, a property bay let with a plate, listing by type, clean-up |
| Company plan | the organisation's plan in `/v1/org` and `/v1/me`, and an org admin cannot change it |
| Company branding | set it, the public sign-in page, `/v1/me`, a reserved handle refused, switch off, gone |
| Staff welcome email | invite a manager, resend, read the welcome email from `/dev/outbox`, the link is for staff, set a password, the link works once, remove the user |
| Reports and CSV | an urgent repair in the maintenance summary, occupancy as a CSV download, an unknown format refused |

Setting a company's plan or correcting its details needs the platform admin (`PATCH /v1/platform/organizations/{org_id}/plan`
and `/details` in the Platform folder). Against the local stack, `npm run postman:flows` runs 130 assertions.

The gateway step uses the fake provider, which exists outside prod only. Put its webhook secret in the **current value**
of `fake_webhook_secret` (Postman does not sync current values).

## Checking the flows against a local stack

`npm run postman:flows` runs them with Newman against `docker compose up` (mock auth). Newman cannot do the browser
sign-in, so that script swaps the OAuth block for a token from `/dev/token`. It creates a fresh organisation each run.

## How the file stays reproducible

The converter (`openapi-to-postmanv2`) invents random example values, which would make every build differ. The build keeps
its folders and descriptions but rebuilds each request's URL, query, headers and body from the contract's own schemas
(`example`, `default`, `enum`, then a fixed value per format). Saved example responses are dropped.
