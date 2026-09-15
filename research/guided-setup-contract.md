# Guided Setup contract and donor-reuse decision

## Scope

This slice provides a local, inert Setup workflow. It collects one installation and one Team interactively, validates a closed schema, generates one random opaque 128-bit `account_ref` per account, writes only `private/installation.toml`, and exports a deterministic public-safe synthetic example. It does not activate a configuration generation, access Windows Credential Manager, read existing lifecycle/runtime state, launch BlueStacks, send input, contact Discord or another network service, authorize a transaction, or spend resources.

Ordinary operators use the installed commands; no source or hand-authored JSON change is required:

- `clash-rush-rebuild setup --project-root <project>` creates a new private installation file and refuses to replace one.
- `clash-rush-rebuild export-setup-example --output <path>` creates, but never replaces, a synthetic TOML example.

Activation remains owned by the later CTL4 generation state machine. Credential values remain owned by the later CTL3 Windows Credential Manager slice; this schema accepts references only.

## Donor inventory

The pinned donors were already inventoried for the application-service boundary in `research/application-service-contract.md`. BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (MIT) stores mutable Python profile settings; CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` (MIT) uses mutable Python configuration and cache files; ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` (MIT) uses configuration globals; Auto Farmer `0120019918758e45feddf84fd5522e31cc6fd578` (MIT) is coupled to ADB settings. None has a complete compatible seam for a closed TOML installation schema, generated opaque account references, five independent default-off resource choices, private-path confinement, and synthetic export. No donor source was copied. The implementation is local because adapting any donor configuration seam would preserve incompatible mutable-code, ADB, lifecycle, network, or gameplay coupling.

## Closed schema

The top-level document contains exactly `schema`, `installation_name`, `central`, and `teams`. Schema is the exact integer `2`. `central` contains exactly the central URL, Discord-bot credential reference, Clash API credential reference, and Discord topology. Each Team contains exactly its stable key, display label, category/channel IDs, runner credential reference, captain assignments, `resources`, and `accounts`.

Every Team resource table contains all five exact booleans and no other fields:

- `home_gold_enabled`
- `home_elixir_enabled`
- `home_dark_elixir_enabled`
- `builder_gold_enabled`
- `builder_elixir_enabled`

There is no resource fallback or inferred permission. Guided Setup asks an explicit yes/no question for each resource. The synthetic export sets all five choices to false. These booleans are configuration intent only; this slice exposes no transaction executor.

Each account contains exactly a stable account key, generated `account_ref`, configured account name, player tag, exact BlueStacks display name, and mapped Discord channel ID. Setup accepts 1–10 accounts, defaults to five when the count response is empty, and derives cardinality from the account array. It accepts no caller-supplied `account_ref`. Account keys and player tags are unique within a Team; account references, BlueStacks names, and account-channel mappings are unique across the installation. Category, operations, and account-channel IDs are distinct. Generated references are exact 32-character lowercase hexadecimal values and are not derived from names, tags, slots, channels, or instances.

Unknown, missing, mistyped, duplicate identity, out-of-range cardinality, unsafe path, and malformed scalar inputs fail closed. Deterministic encoding followed by strict decoding is a canonical round trip.

## Persistence and privacy

Real installation values are written only beneath the resolved project root at ignored `private/installation.toml`. Setup rejects symlinks, Windows junctions/other reparse points, unsafe private paths, and any existing installation rather than overwriting it. It retains a verified no-delete-share handle to the plain private directory across destination creation so a concurrent directory swap cannot redirect the write. A safe pre-existing `private/` directory is preserved. Any failure leaves the private directory and any blocking partial destination in place rather than revisiting an unowned path after releasing the retained handle.

The public synthetic export contains only reserved `.invalid` connectivity, credential references, synthetic labels/tags/IDs, deterministic opaque references, and default-off resource choices. The CLI creates the selected export path exclusively and never overwrites an existing file.
