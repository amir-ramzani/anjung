# Backup and restore

| Data | Backup | Restore |
|---|---|---|
| DynamoDB `anjung-main` | Point-in-time recovery (35 days), deletion protection on | Restore to a new table (`anjung-main-restore`), verify, then point `TABLE_NAME` at it or copy back. Never restore over the live table |
| Documents bucket | Versioning, KMS, noncurrent versions kept 365 days | Restore the previous version of an object (`aws s3api list-object-versions`, copy the version back) |
| Secrets | Secrets Manager replication or recovery window | `aws secretsmanager restore-secret` |
| Cognito users | No export of passwords | Users are recreated by `AdminCreateUser` from memberships and tenants in the table, then re-invited |

## Drill (plan P5-08)
1. Restore the table to a point 1 hour ago into a scratch table.
2. Run the integrity job against it (`python -m app.cli run-job integrity_check`, with `TABLE_NAME` set to the scratch table): it must report no mismatch.
3. Restore one document version and download it through a presigned URL.
4. Record RPO (data lost, target 5 minutes via PITR) and RTO (time taken, target 4 hours) in the drill report.
