# RBAC Matrix (Phase 0)

## Roles
- `admin`
- `operator`
- `auditor`

## Permissions

| Permission | admin | operator | auditor |
|---|---|---|---|
| auth:login | Y | Y | Y |
| system:status:read | Y | Y | Y |
| ingestion:status:read | Y | Y | Y |
| search:run | Y | Y | Y |
| search:feedback:write | Y | Y | N |
| alert:read | Y | Y | Y |
| alert:ack | Y | Y | N |
| audit:read | Y | N | Y |
| camera:test | Y | Y | N |
| camera:roi:write | Y | Y | N |
| capture:delete | Y | N | N |
| cleanup:run | Y | N | N |
| user:manage | Y | N | N |
| config:update | Y | N | N |

## Notes
- `auditor` is strictly read-only.
- API checks both token validity and permission claim.
- Admin console pages must declare required permission per route.
