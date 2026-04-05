import crypto from 'node:crypto'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const bundleRoot = process.env.HERMES_BUNDLE_ROOT
  ? path.resolve(root, process.env.HERMES_BUNDLE_ROOT)
  : path.join(root, 'src-tauri', 'target', 'release', 'bundle')
const baseUrl = (process.env.HERMES_RELEASE_BASE_URL || '').replace(/\/+$/, '')
const version = (process.env.HERMES_RELEASE_VERSION || process.env.npm_package_version || '').trim()
const notes = (process.env.HERMES_RELEASE_NOTES || '').trim()
const outputPath = process.env.HERMES_RELEASE_MANIFEST_OUT
  ? path.resolve(root, process.env.HERMES_RELEASE_MANIFEST_OUT)
  : path.join(root, '.build', 'release', 'latest.json')

if (!baseUrl) {
  console.error('HERMES_RELEASE_BASE_URL is required to generate release metadata.')
  process.exit(1)
}

if (!version) {
  console.error('HERMES_RELEASE_VERSION or npm_package_version is required to generate release metadata.')
  process.exit(1)
}

const platformPatterns = {
  'darwin-aarch64': { dir: 'macos', extension: '.app.tar.gz' },
  'darwin-x86_64': { dir: 'macos', extension: '.app.tar.gz' },
  'linux-x86_64': { dir: 'appimage', extension: '.AppImage.tar.gz' },
  'windows-x86_64': { dir: 'nsis', extension: '.exe.zip' },
}

const platforms = {}

for (const [platform, { dir, extension }] of Object.entries(platformPatterns)) {
  const artifactDir = path.join(bundleRoot, dir)
  if (!fs.existsSync(artifactDir)) {
    continue
  }

  const file = fs
    .readdirSync(artifactDir)
    .find((name) => name.endsWith(extension))

  if (!file) {
    continue
  }

  const artifactPath = path.join(artifactDir, file)
  const signaturePath = `${artifactPath}.sig`
  if (!fs.existsSync(signaturePath)) {
    continue
  }

  const signature = fs.readFileSync(signaturePath, 'utf8').trim()
  const contentLength = fs.statSync(artifactPath).size
  const hash = crypto
    .createHash('sha256')
    .update(fs.readFileSync(artifactPath))
    .digest('hex')

  const relativeArtifactPath = path.relative(root, artifactPath).split(path.sep).join('/')
  platforms[platform] = {
    signature,
    url: `${baseUrl}/${relativeArtifactPath}`,
    length: contentLength,
    hash: `sha256:${hash}`,
  }
}

if (Object.keys(platforms).length === 0) {
  console.error(`No signed updater artifacts were found under ${bundleRoot}.`)
  process.exit(1)
}

const manifest = {
  version,
  notes,
  pub_date: new Date().toISOString(),
  platforms,
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true })
fs.writeFileSync(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
console.log(outputPath)
