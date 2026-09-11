# MyHermes Starter

Public, reusable companion and contracts. Keep company settings, user content, actual logs and credentials out of this repository and its packages. Use fictional examples only.

- Read README.md and docs/API.md before changing a contract; change versions deliberately.
- Preserve one Hermes context across account categories. Sync only documented allowlisted files.
- Treat templates as data, never shell. Credentials belong in the OS secure store; never in arguments, stdout or Git.
- Server monitoring and relay policies must be implemented here before private consumers use them.
- Run Python tests and the public core checks for affected changes. Keep Ubuntu/macOS test claims separate.
- No push, release, account authorization or production deployment without explicit user permission.
