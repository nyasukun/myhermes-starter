"""Small, source-hash-checked adaptations of the one supported Desktop pin.

These edits apply only to a companion-owned copy, never the original runtime.
Function insertions retain upstream code and license notices for review.
"""

import ast
import hashlib
import json
from pathlib import Path

from .errors import CompanionError


PYTHON_EDITS = {
    "agent/auxiliary_client.py": {
        "resolve_provider_client": (
            "from myhermes_desktop_policy import auxiliary\n"
            "return auxiliary(provider, model, async_mode, explicit_base_url, explicit_api_key, api_mode, raw_codex)"
        ),
        "_resolve_task_provider_model": (
            "from myhermes_desktop_policy import auxiliary_route\n"
            "return auxiliary_route(provider, model, base_url, api_key)"
        ),
    },
    "hermes_cli/inventory.py": {
        "build_models_payload": "from myhermes_desktop_policy import inventory\nreturn inventory()",
    },
    "hermes_cli/runtime_provider.py": {
        "resolve_runtime_provider": (
            "from myhermes_desktop_policy import resolve\n"
            "return resolve(requested=requested, explicit_api_key=explicit_api_key, "
            "explicit_base_url=explicit_base_url, target_model=target_model)"
        ),
    },
    "hermes_cli/web_server_config.py": {
        "_apply_model_assignment_sync": (
            "from myhermes_desktop_policy import assignment\n"
            "return assignment(scope, provider, model, task, base_url, api_key)"
        ),
    },
    "hermes_cli/profiles.py": {
        "get_profile_dir": "from myhermes_desktop_policy import profile_home\nreturn profile_home(name)",
    },
    "hermes_constants.py": {
        "set_hermes_home_override": "from myhermes_desktop_policy import guard_home\nguard_home(path)",
    },
}

# All replacements are unique in the reviewed pin. A changed upstream source
# fails before any edit or build; updating the pin requires a new review.
JS_EDITS = [
    (
        "const USER_DATA_OVERRIDE =",
        """// MyHermes managed adapter: only the starter supplies a live session.
if (!process.env.MYHERMES_DESKTOP_RELAY_URL || !process.env.AUXILIARY_MYHERMES_API_KEY ||
    !process.env.MYHERMES_DESKTOP_HOME || !process.env.HERMES_MANAGED_DIR ||
    process.env.HERMES_HOME !== process.env.MYHERMES_DESKTOP_HOME) {
  console.error('Start this Desktop with myhermes desktop.')
  app.exit(1)
  throw new Error('MyHermes starter session required.')
}
app.relaunch = () => { throw new Error('Quit and run myhermes desktop again.') }
const USER_DATA_OVERRIDE =""",
    ),
    (
        "function resolveHermesBackend(backendArgs) {",
        """function resolveHermesBackend(backendArgs) {
  const managedRoot = process.env.HERMES_DESKTOP_HERMES_ROOT
  const managedPython = process.env.HERMES_DESKTOP_PYTHON
  if (!managedRoot || !managedPython || !fileExists(managedPython)) {
    throw new Error('Prepare the pinned runtime with myhermes prepare-desktop.')
  }
  return {
    kind: 'python', label: 'MyHermes', command: managedPython,
    args: ['-m', 'hermes_cli.main', ...backendArgs],
    env: { PYTHONPATH: managedRoot }, root: managedRoot, bootstrap: false, shell: false
  }
""",
    ),
    (
        "async function checkUpdates() {",
        """async function checkUpdates() {
  return { supported: false, reason: 'myhermes-managed', message: 'Use myhermes upgrade and prepare-desktop.' }
""",
    ),
    (
        "async function applyUpdates(opts: { stopSafeBlockers?: boolean } = {}) {",
        """async function applyUpdates(opts: { stopSafeBlockers?: boolean } = {}) {
  throw new Error('Use myhermes upgrade and prepare-desktop.')
""",
    ),
    ("function readActiveDesktopProfile() {", "function readActiveDesktopProfile() {\n  return 'default'\n"),
    (
        "function resolveHermesHome() {",
        """function resolveHermesHome() {
  return process.env.MYHERMES_DESKTOP_HOME
""",
    ),
    (
        "function writeActiveDesktopProfile(name) {",
        """function writeActiveDesktopProfile(name) {
  if (name && name !== 'default') throw new Error('MyHermes uses the enrolled home only.')
""",
    ),
    (
        "function readDesktopConnectionConfig() {",
        """function readDesktopConnectionConfig() {
  return { mode: 'local', remote: {}, profiles: {} }
""",
    ),
    (
        "async function saveRegistryConnection(input: any = {}) {",
        """async function saveRegistryConnection(input: any = {}) {
  throw new Error('MyHermes Desktop uses the enrolled local connection.')
""",
    ),
    (
        "function writeDesktopConnectionConfig(config) {",
        """function writeDesktopConnectionConfig(config) {
  throw new Error('MyHermes Desktop uses the enrolled local connection.')
""",
    ),
    (
        "function readDesktopConnectionsRegistry() {",
        """function readDesktopConnectionsRegistry() {
  return migrateV1ToRegistry({ mode: 'local', remote: {}, profiles: {} })
""",
    ),
    (
        "async function resolveRemoteBackend(profile, options: { poolKey?: string; primary?: boolean } = {}) {",
        "async function resolveRemoteBackend(profile, options: { poolKey?: string; primary?: boolean } = {}) {\n"
        "  return null\n",
    ),
    (
        "async function spawnPoolBackend(profile, entry, opts: { forceLocal?: boolean; poolKey?: string } = {}) {",
        "async function spawnPoolBackend(profile, entry, opts: { forceLocal?: boolean; poolKey?: string } = {}) {\n"
        "  if (profile && profile !== 'default') throw new Error('MyHermes uses the enrolled home only.')\n",
    ),
    (
        "localModels: process.argv.includes('--local') || process.platform === 'win32' || process.platform === 'darwin'",
        "localModels: false",
    ),
    ("TERMINAL_CWD: hermesCwd,", "TERMINAL_CWD: '/workspace',"),
]


def source_hashes():
    return json.loads(Path(__file__).with_name("desktop-sources.json").read_text())


def adapted_source(name, source):
    if hashlib.sha256(source).hexdigest() != source_hashes().get(name):
        raise CompanionError("desktop_source_mismatch", "Desktop source differs from the reviewed pin.", 3)
    text = source.decode("utf-8")
    if name in PYTHON_EDITS:
        lines = text.splitlines(keepends=True)
        functions = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
        inserts = []
        for function, code in PYTHON_EDITS[name].items():
            node = functions[function]
            first = node.body[0]
            docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            index = first.end_lineno if docstring else first.lineno - 1
            inserts.append((index, "".join("    " + line + "\n" for line in code.splitlines())))
        for index, code in sorted(inserts, reverse=True):
            lines.insert(index, code)
        return "".join(lines).encode()
    for before, after in JS_EDITS:
        expected = 2 if before == "TERMINAL_CWD: hermesCwd," else 1
        if text.count(before) != expected:
            raise CompanionError("desktop_source_mismatch", "Desktop adapter anchor differs from the reviewed pin.", 3)
        text = text.replace(before, after)
    return text.encode()
