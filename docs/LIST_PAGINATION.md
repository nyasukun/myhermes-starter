# Bounded metadata lists and installation bindings

All pages contain at most 100 records and `next_cursor`, which is `null` only when that page reaches the current end. Never interpret absence from a partial page as deletion, loss of permission or an empty inventory. A cursor selects a position in the authenticated caller's existing scope; it never selects another owner or grants access. These are live inventories, not transactionally frozen snapshots; refresh to observe concurrent inserts, permission changes and revocations.

| Endpoint | List field | Cursor/order |
| --- | --- | --- |
| `GET /v1/installations` | `installations` | `after=<installation UUID>`, ascending installation ID, authenticated owner only |
| `GET /v1/admin/installations` | `installations` | Same, browser administrator metadata only |
| `GET /v1/admin/templates` | `templates` | Opaque `after`, ascending `(template_id, version)`; browser template editor |
| `GET /v1/admin/content-editors` | `editors` | Opaque `after`, ascending `(person_id, kind)`; browser administrator |
| `GET /v1/connections/:id/bindings` | `bindings` | `after=<installation UUID>`, ascending installation ID; owner browser |
| `GET /v1/admin/connections/:id/bindings` | `bindings` | Same; browser administrator, connection metadata only |

Preserve the returned cursor exactly and URL-encode it in the next request. Template/editor cursors are canonical unpadded base64url of the public `{kind,keys}` object, validated by `parseMetadataCursor` in the pinned public module. Invalid or cross-kind cursors, unknown fields/selectors and duplicate query keys return 400. Semver ordering here is lexicographic, not a recommendation about the newest version. Existing public template and connection lists retain their documented ascending `after` cursors; grant/history pages retain their existing `before` cursors.

Device `GET /v1/connections/:id` retains `{connection,usage_grant,bindings}`, but `bindings` contains **exactly one row for the authenticated installation**, including a synthesized `needs_auth` state when that binding has not been authorized. The owner comes from authentication, not a URL/body selector. The Python client checks both the logical owner and this installation. A device cannot use the browser binding-list API to fetch every environment's state.

For browser compatibility, connection detail contains at most the first 100 binding rows and a `bindings_next_cursor` field. The portal uses the dedicated paginated bindings endpoint. The binding list includes revoked environments and records; it does not purge history to keep responses small. Each row keeps its existing client-reported test evidence and server-derived effective status. A template or connection grant can change between pages, so a list is not authorization for an external action; clients revalidate the current logical connection, template, grant and their own binding before use.

No new content or monitoring fields are collected. Page records remain the existing public metadata projections. These bounded pages do not expand administrator access to owner persona, personal skill content or credentials.
