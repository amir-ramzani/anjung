# Personal data breach response

PDPA (as amended 2024) requires notifying the Commissioner as soon as practicable, and within 72 hours of becoming aware of a notifiable breach, and notifying affected individuals where harm is likely. Confirm the current rules and your role (controller or processor, decision D1) with counsel before relying on this page.

## First hour
1. Name an incident lead and open a private channel. Record the time you became aware.
2. Contain: disable the affected users in Cognito (`AdminDisableUser` + `AdminUserGlobalSignOut`), rotate the exposed secret in Secrets Manager, block the source IP in the WAF.
3. Preserve evidence: do not delete logs. CloudTrail data events cover the table and documents bucket; GuardDuty and Security Hub findings go to the alarm topic.

## Assess (within 24 hours)
- What data (identity numbers, contact details, financial records), whose (organisation, tenants, count), how long, is it still happening.
- Use the audit log (`GET /v1/audit-log`) and CloudTrail to bound the exposure. Request logs carry `org_id`, `user_id`, `route`, never personal values.
- Decide: is harm likely? If unsure, treat it as notifiable.

## Notify
- Regulator: use the template in `docs/runbooks/breach-notice-regulator.md` (counsel to review).
- Individuals: use `docs/runbooks/breach-notice-individuals.md` (English and Malay). Short, plain language; send through the organisation.
- Organisations (landlords) are told immediately if Anjung is their processor.

## After
Write the timeline, the cause and the fix. Add the missing control or alarm. Run the tabletop exercise again against the new gap.
