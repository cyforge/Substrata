import { defineConfig } from 'vocs/config'
import pages from './content.json' with { type: 'json' }

const sections = [...new Set(pages.map((page) => page.section))]

export default defineConfig({
  title: 'Substrata',
  titleTemplate: '%s · Substrata',
  description: 'Python SDK for policy-controlled agent payments on Stellar. Build with x402, durable budgets, and auditable payment flows.',
  renderStrategy: 'full-static',
  checkDeadlinks: true,
  accentColor: 'light-dark(#16735d, #8dddba)',
  colorScheme: 'light dark',
  iconUrl: '/icon.svg',
  logoUrl: { light: '/logo-light.svg', dark: '/logo-dark.svg' },
  ogImageUrl: '/social.svg',
  sidebar: [
    { text: 'Overview', link: '/' },
    ...sections.map((section) => ({
      text: section,
      collapsed: section === 'Project',
      items: pages
        .filter((page) => page.section === section)
        .map((page) => ({ text: page.text, link: `/docs/${page.route}` })),
    })),
  ],
  topNav: [
    { text: 'Documentation', link: '/docs/getting-started/quickstart' },
    { text: 'API reference', link: '/docs/reference/python-api' },
    { text: 'v0.1.0', link: '/docs/project/changelog' },
  ],
  socials: [{ icon: 'github', link: 'https://github.com/cyforge/Substrata' }],
  codeHighlight: { themes: { light: 'github-light', dark: 'github-dark-dimmed' } },
})
