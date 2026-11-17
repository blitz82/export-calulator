import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { relative } from 'node:path'

export const bundleInventory = {
  name: 'epms-bundle-inventory',
  apply: 'build',
  generateBundle: {
    order: 'post',
    handler(_options, bundle) {
      const lockText = readFileSync('package-lock.json')
      const { packages } = JSON.parse(lockText.toString())
      const packageKeys = Object.keys(packages).filter(key=>key.startsWith('node_modules/'))
        .sort((a,b)=>b.length-a.length)
      const chunks = Object.values(bundle).flatMap(chunk => {
        if (chunk.type !== 'chunk') return []
        const used = new Set()
        for (const [id, module] of Object.entries(chunk.modules)) {
          if (!module.renderedLength || id.startsWith('\0')) continue
          const path = relative(process.cwd(), id).replaceAll('\\', '/')
          if (!path.startsWith('node_modules/')) continue
          const packageKey = packageKeys.find(key=>path.startsWith(key+'/'))
          if (packageKey) used.add(packageKey)
        }
        return [{name:chunk.fileName, sha256:createHash('sha256').update(chunk.code).digest('hex'), packages:[...used].sort()}]
      }).sort((a,b)=>a.name.localeCompare(b.name))
      this.emitFile({type:'asset',fileName:'bundle-components.json',source:JSON.stringify({
        lock_sha256:createHash('sha256').update(lockText).digest('hex'), chunks,
      },null,2)})
    },
  },
}
