# Member administration contract, version 1

All routes require an active **browser** administrator. The same-origin mutation check applies. Membership controls application access; it never grants access to another member's private content. The Access issuer is deployment configuration, and identity is its verified `sub`, not an email address or a caller-provided owner selector.

`GET /v1/admin/members?after=<person_id>` returns `{members, next_after}` in person-ID lexical order, at most 100 records. Only `after` is permitted, exactly once. Each member has `person_id`, `access_subject`, `role` (`member` or `admin`), `active` (boolean), `revision`, `created_at`, and `updated_at`. Newly created person IDs are server-generated UUIDs; migrated opaque IDs remain supported. Access subject UUIDs must first be verified by the authorized identity administrator.

`POST /v1/admin/members` accepts exactly:

```json
{"schema_version":"1","request_id":"10000000-0000-4000-8000-000000000001","access_subject":"20000000-0000-4000-8000-000000000001","role":"member"}
```

It creates an active member at revision 1, returns 201 `{member}`, and rejects existing subjects with 409. The example is synthetic. Subjects are preserved exactly as verified; an uppercase/lowercase UUID duplicate is rejected as well. Do not substitute an email or guess a subject. IDs and subject cannot be updated or supplied as alternate ownership selectors.

`PUT /v1/admin/members/:person_id` accepts exactly:

```json
{"schema_version":"1","request_id":"10000000-0000-4000-8000-000000000002","base_revision":1,"role":"member","active":false}
```

Successful updates return 200 `{member}`, increment revision and record the update time. A stale revision returns 409. Self-demotion and self-deactivation return 403. The transaction rechecks the actor's current authority and retains at least one active administrator under concurrent operations. Deactivation permanently revokes all existing installations in the same transaction; reactivation requires enrollment with a new key. In-flight operations authenticated before deactivation may finish, but enrollment insertion itself checks active membership.

Request IDs are scoped to the administering person. The same ID and canonical request/target replay the original metadata receipt, including after later changes; a different payload or target returns 409. Receipts contain only the above membership metadata and are retained without expiry in this MVP, up to 100,000 globally; when full, new mutations fail with 429 and existing retries remain available. No content-bearing mutation log is introduced. Responses are private/no-store. The implementation never creates an initial administrator automatically; secure operator bootstrap is deployment-specific.
