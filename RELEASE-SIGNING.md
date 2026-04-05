# Release Signing

Hermes Companion now has release scaffolding for:

- Tauri updater metadata generation
- macOS notarization
- Windows code-signing hooks
- release environment validation

## Release Environment

Start from `.env.release.example` and export the values in your shell or CI secret store.

Validate the required values with:

```bash
npm run release:validate
```

You can also validate a single target:

```bash
node scripts/validate-release-env.mjs macos
node scripts/validate-release-env.mjs windows
node scripts/validate-release-env.mjs updater
```

## Tauri Release Config

The build scripts now generate a merged Tauri release config automatically:

- `scripts/generate-release-config.mjs`

When updater signing values are present, the generated config enables updater artifact creation and injects updater endpoints and the public key.

## macOS Release Flow

Prerequisites:

- Tauri build signing identity configured through `APPLE_SIGNING_IDENTITY`
- Apple notarytool keychain profile configured as `APPLE_NOTARY_PROFILE`

Build and notarize:

```bash
./scripts/build-macos.sh
```

If `APPLE_NOTARY_PROFILE` is set, the script will run:

- `xcrun notarytool submit ... --wait`
- `xcrun stapler staple ...`

Manual notarization is also available:

```bash
./scripts/notarize-macos.sh "/path/to/Hermes Companion.app" "/path/to/Hermes Companion.dmg"
```

## Windows Release Flow

Prerequisites:

- `signtool` available from the Windows SDK / Developer PowerShell
- either `WINDOWS_SIGN_CERT_PATH` or `WINDOWS_SIGN_SUBJECT_NAME`
- `WINDOWS_TIMESTAMP_URL`
- set `HERMES_REQUIRE_SIGNED_RELEASE=1` in CI or release shells when unsigned artifacts should fail the build

Build and sign:

```powershell
pwsh .\scripts\build-windows.ps1
```

If Windows signing values are present, the build will call:

- `scripts/sign-windows.ps1`

That script signs with SHA-256 digests and verifies the signature afterwards.
If `HERMES_REQUIRE_SIGNED_RELEASE=1`, the build also runs:

- `scripts/assert-windows-signatures.ps1`

This makes unsigned Windows artifacts a hard failure instead of a silent footgun.

## Release Manifest

When `HERMES_RELEASE_BASE_URL` is set, the build scripts also generate updater metadata:

```bash
npm run release:manifest
```

The manifest is written to:

- `.build/release/latest.json`

## Notes

- Local dev builds still work without any signing environment.
- Updater metadata generation only succeeds when signed updater artifacts are present.
- macOS secure token storage is already verified locally; Windows and Linux still need native runtime validation for secure storage and signing.
