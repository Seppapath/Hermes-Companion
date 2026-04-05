const target = (process.argv[2] || 'all').toLowerCase()

const targetRequirements = {
  macos: [
    'APPLE_SIGNING_IDENTITY',
    'APPLE_NOTARY_PROFILE',
  ],
  windows: [
    ['WINDOWS_SIGN_CERT_PATH', 'WINDOWS_SIGN_SUBJECT_NAME'],
    'WINDOWS_TIMESTAMP_URL',
  ],
  linux: [],
  updater: [
    'TAURI_SIGNING_PRIVATE_KEY',
    'TAURI_SIGNING_PRIVATE_KEY_PASSWORD',
    'TAURI_UPDATER_PUBKEY',
    'TAURI_UPDATER_ENDPOINTS',
    'HERMES_RELEASE_BASE_URL',
  ],
}

const selectedTargets =
  target === 'all' ? Object.keys(targetRequirements) : [target]

let hasErrors = false

for (const selectedTarget of selectedTargets) {
  const requirements = targetRequirements[selectedTarget]
  if (!requirements) {
    console.error(`Unknown release target '${selectedTarget}'.`)
    process.exit(1)
  }

  const missing = []
  for (const requirement of requirements) {
    if (Array.isArray(requirement)) {
      const satisfied = requirement.some((name) => (process.env[name] || '').trim())
      if (!satisfied) {
        missing.push(requirement.join(' or '))
      }
      continue
    }

    if (!(process.env[requirement] || '').trim()) {
      missing.push(requirement)
    }
  }

  if (missing.length > 0) {
    hasErrors = true
    console.error(`[${selectedTarget}] Missing release environment values:`)
    for (const item of missing) {
      console.error(`- ${item}`)
    }
  }
}

if (hasErrors) {
  process.exit(1)
}

console.log(`Release environment looks good for ${selectedTargets.join(', ')}.`)
