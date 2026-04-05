import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const baseConfigPath = path.join(root, 'src-tauri', 'tauri.conf.json')
const outDir = path.join(root, '.build', 'release')
const outPath = path.join(outDir, 'tauri.release.generated.json')

const baseConfig = JSON.parse(fs.readFileSync(baseConfigPath, 'utf8'))
const config = structuredClone(baseConfig)

const updaterEndpoint = (process.env.TAURI_UPDATER_ENDPOINTS || '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean)
const updaterPubkey = (process.env.TAURI_UPDATER_PUBKEY || '').trim()
const updaterSigningKey = (process.env.TAURI_SIGNING_PRIVATE_KEY || '').trim()
const enableUpdater = updaterEndpoint.length > 0 && updaterPubkey && updaterSigningKey

if (enableUpdater) {
  config.bundle = {
    ...config.bundle,
    createUpdaterArtifacts: true,
  }
  config.plugins = {
    ...(config.plugins || {}),
    updater: {
      pubkey: updaterPubkey,
      endpoints: updaterEndpoint,
      windows: {
        installMode: 'passive',
      },
    },
  }
}

const signingIdentity = (process.env.APPLE_SIGNING_IDENTITY || '').trim()
if (signingIdentity) {
  config.bundle = {
    ...config.bundle,
    macOS: {
      ...(config.bundle?.macOS || {}),
      signingIdentity,
    },
  }
}

fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(outPath, `${JSON.stringify(config, null, 2)}\n`, 'utf8')
process.stdout.write(`${outPath}\n`)
