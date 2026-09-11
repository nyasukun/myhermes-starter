# @myhermes/core 0.3.0

Public source package for the implemented MyHermes contracts, validation, synchronization, monitoring and body-free inference accounting. Private applications vendor an immutable npm tarball and implement company-specific authentication, storage adapters, permissions and UI around these public modules. TypeScript-aware bundlers consume the exported source; Node24 runs the contract and SQLite tests directly.

The package defines:

- Personality synchronization for `SOUL.md`, `memories/MEMORY.md` and `memories/USER.md`, with Unicode-aware limits, revisioned tombstones and explicit same-path conflicts.
- Immutable service templates, multiple explicit account connections, resource-scoped usage grants and installation binding evidence.
- Complete personal/company skill packages, file integrity, safe paths, versioned mutations, audience selection and explicit publication withdrawal.
- Strict relay request parsing, fixed-origin forwarding/SSE, server-observed usage, keyed internal deduplication, bounded reservations and metadata-only generation reconciliation.
- UUIDv7 relay admission, finite known-accounting retention, permanent unknown reservations and a persistent clock floor that prevents expired-ID replay.
- Published audit/OTLP field allowlists, validation, deduplication, persistent metadata projections, retention and aggregation, separate from owner content and relay accounting.
- Membership and role mutation schemas without administrator access to personal content or impersonation.

No company settings, credentials, owner data or actual logs belong in this package. The exact wire/storage rules are in the repository's [API index](../../docs/API.md), [skills contract](../../docs/SKILL_PACKAGES.md), [monitoring contract](../../docs/MONITORING.md) and [relay contract](../../docs/RELAY.md). Client event manifest1.0.0 and relay manifest1.3.0 are separate versioned disclosures.

`npm test` runs public validation, metadata, SQLite, conflict, replay and retention tests. `npm pack` produces `myhermes-core-0.3.0.tgz`; this local build command does not publish it. Full Worker/Cloudflare adapter behavior and cross-language/OS client acceptance are tested by the private application with synthetic fixtures.
