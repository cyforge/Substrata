import { mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const site = fileURLToPath(new URL('../', import.meta.url))
const repository = path.resolve(site, '..')

export function rewriteLinks(markdown, source, pages) {
  const routes = new Map(pages.map((page) => [page.source, `/docs/${page.route}`]))
  const rewrite = (target) => {
    if (/^(?:[a-z]+:|#|\/)/i.test(target)) return target
    const [file, ...fragment] = target.split('#')
    const normalized = path.posix.normalize(path.posix.join(path.posix.dirname(source), file))
    const suffix = fragment.length ? `#${fragment.join('#')}` : ''
    return routes.has(normalized)
      ? routes.get(normalized) + suffix
      : `https://github.com/cyforge/Substrata/blob/main/${normalized}${suffix}`
  }
  // Code fences and inline code are examples, not navigation targets.
  return markdown.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)/g)
    .map((part, index) => index % 2 ? part : part.replace(/(\]\()([^\s)]+)(\))/g,
      (_, before, target, after) => before + rewrite(target) + after))
    .join('')
}

export async function syncDocs() {
  const pages = JSON.parse(await readFile(path.join(site, 'content.json'), 'utf8'))
  const output = path.join(site, 'src/pages/docs')
  const routes = new Set()
  for (const page of pages) {
    if (!/^[a-z0-9-]+\/[a-z0-9-]+$/.test(page.route) || routes.has(page.route)) {
      throw new Error(`Invalid or duplicate documentation route: ${page.route}`)
    }
    routes.add(page.route)
    await readFile(path.join(repository, page.source), 'utf8')
  }
  await rm(output, { recursive: true, force: true })
  for (const page of pages) {
    const markdown = await readFile(path.join(repository, page.source), 'utf8')
    const target = path.join(output, `${page.route}.mdx`)
    await mkdir(path.dirname(target), { recursive: true })
    const edit = `https://github.com/cyforge/Substrata/edit/main/${page.source}`
    const body = rewriteLinks(markdown, page.source, pages)
    await writeFile(target, `---\ntitle: ${JSON.stringify(page.text)}\nshowAskAi: false\n---\n\n${body}\n\n---\n\n[Edit this page on GitHub](${edit})\n`)
  }
  console.log(`Synced ${pages.length} documentation pages from repository Markdown.`)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await syncDocs()
}
