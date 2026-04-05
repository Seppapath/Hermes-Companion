import { copyFile, cp, mkdir, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(scriptDir, '..')
const resourceRoot = path.join(rootDir, 'src-tauri', 'resources')
const daemonBinary = process.argv[2]

await mkdir(resourceRoot, { recursive: true })
await rm(path.join(resourceRoot, 'services'), { recursive: true, force: true })
await cp(path.join(rootDir, 'daemon', 'service'), path.join(resourceRoot, 'services'), {
  recursive: true,
})

if (daemonBinary) {
  const daemonDir = path.join(resourceRoot, 'daemon')
  await mkdir(daemonDir, { recursive: true })
  await copyFile(path.resolve(daemonBinary), path.join(daemonDir, path.basename(daemonBinary)))
}
