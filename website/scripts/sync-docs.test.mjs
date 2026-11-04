import assert from 'node:assert/strict'
import test from 'node:test'
import { rewriteLinks } from './sync-docs.mjs'

const pages = [{ source: 'docs/client.md', route: 'guides/buyer-client' }]

test('maps canonical Markdown links to website routes and preserves fragments', () => {
  assert.equal(
    rewriteLinks('[Buyer](../client.md#usage)', 'docs/architecture/payments.md', pages),
    '[Buyer](/docs/guides/buyer-client#usage)',
  )
})

test('keeps examples, external URLs, and anchors unchanged', () => {
  const markdown = '[Source](https://example.test) [Heading](#heading)\n' +
    '```python\nlink = "[Buyer](client.md)"\n```\n`[Buyer](client.md)`'
  assert.equal(rewriteLinks(markdown, 'docs/quickstart.md', pages), markdown)
})

test('links repository files outside the website to their source', () => {
  assert.equal(
    rewriteLinks('[Example](../examples/basic_app.py)', 'docs/quickstart.md', pages),
    '[Example](https://github.com/cyforge/Substrata/blob/main/examples/basic_app.py)',
  )
})
