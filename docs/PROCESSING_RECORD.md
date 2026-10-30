# Record of personal data processing — DemandIQ

Kept under Article 17 of Law No. 058/2021 of 13/10/2021 relating to the protection
of personal data and privacy. Last reviewed 29 October 2026.

## Controller and contact

| Field | Detail |
| --- | --- |
| Controller | Kelvin Tawe, capstone project, African Leadership University, Kigali |
| Contact | k.tawe@alustudent.com |
| Data protection officer (Art. 40) | Kelvin Tawe, same contact |
| Registration | Not registered as a data controller; no real operator data is processed while the REC decision is pending |

## Processing activities

| Activity | Data subjects | Categories of personal data | Purpose | Retention |
| --- | --- | --- | --- | --- |
| Account and workspace | Owners, administrators, data clerks, analysts, viewers | Name, email, hashed password, role, workspace, session and device records | Sign-in, five seats inside one workspace, session revocation | Until the account is deleted |
| Demand records | None (business figures, not people) | Date, quantity, channel, location | Forecast the next day's demand and show a range | Until the import or account is deleted |
| Imported files and photos | Anyone whose handwriting appears on an uploaded log | The image itself, its MIME type, the rows read from it | Read a paper log into reviewable rows | Photo cleared 30 days after upload by the `photo_retention` job; rows stay with the import |
| Device and connector keys | Workspace staff | Per-device key, location mapping | Attribute a record to a location without a shared password | Until revoked |
| Research data | None — no participant has taken part | — | — | — |

## Recipients and transfers

- No recipient outside the workspace: every query is scoped to the caller's
  workspace, and a seat in one workspace cannot read another's rows.
- No transfer of personal data outside Rwanda. Photos are read on the
  deployment's own server; the optional outside vision service is not
  configured, and no workspace can switch it on (Arts. 48 to 50).
- No advertising, no sale, no profiling of customers.

## Security measures (Art. 47)

- Password sign-in with hashed credentials; optional TOTP two-factor, required
  for Administrator seats; recovery codes stored hashed.
- One session row per device, each revocable from Settings; changing the password
  signs out the other sessions.
- Workspace-scoped data access on every route; no cross-workspace read path.
- Bot protection on sign-in and sign-up when Cloudflare Turnstile is configured.
- Every job run is recorded so ingestion, forecasting and report delivery can be
  traced.

## Rights (Arts. 18, 20, 23 and 24)

Access, portability, erasure and rectification are handled through the Privacy
Policy in the app and by email to the contact above. Account deletion runs
`DELETE /me/account`; a full copy of demand records is sent by email within 30
days. Photo deletion earlier than 30 days is also by email.

## Children

DemandIQ is not intended for anyone under 16, and no account may be created for a
child (Art. 9). The app cannot verify age; the policy says so.
