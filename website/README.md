# Documentation website

The SDK documentation runs on [Vocs](https://vocs.dev). Use Node.js 24 LTS and npm.
The Python package works independently of this website.
Python distributions exclude the website sources, build output, and Node.js dependencies.

## Run locally

From the repository root:

```sh
npm --prefix website ci
npm --prefix website run dev
```

Open the local URL printed by Vocs. The site includes keyword search, code
highlighting, mobile navigation, and light and dark themes. Search runs in the
browser and needs no API key.

## Edit a page

Edit the original Markdown files in `docs/`, `CONTRIBUTING.md`, `SECURITY.md`, or
`CHANGELOG.md`. `content.json` maps those files to website routes and sidebar
groups. The sync script generates `src/pages/docs/` and rewrites relative links
to their website destinations. Files outside the website link to GitHub.

Generated pages are ignored by Git. Both `dev` and `build` sync them before Vocs
starts. After editing a source Markdown file during development, run
`npm --prefix website run sync` in another terminal to refresh the page.

The homepage lives in `src/pages/index.mdx`; global styles are in
`src/pages/_root.css`. Logo and social assets live in `public/`.

## Check and build

```sh
npm --prefix website test
npm --prefix website run check
npm --prefix website run build
npm --prefix website run test:browser
npm --prefix website run preview
```

`test` checks link rewriting, `check` runs TypeScript, and `build` checks internal
links and renders the complete site. CI runs these checks and retains the static
website as an artifact.

The browser check loads the built payment lifecycle page and verifies its Mermaid
diagram. From `website/`, run `npx playwright install chromium` once before the
first browser test. Vocs needs the explicit `mermaid` dependency to render diagrams.

## Hosting

The `full-static` render strategy writes the deployable site to
`website/dist/public/`. Serve that directory from a static host with directory
indexes and `404.html` as its error page. It does not require a Python or Node.js
server. Keep the generated `RSC/` directory and all other assets in the upload;
client navigation uses them.

The repository-root `vercel.json` builds this site on Vercel using Node.js 24.
Set the project root to the repository root so the source Markdown remains
available. `VERCEL_PROJECT_PRODUCTION_URL` supplies the canonical hostname for
the sitemap and metadata.

The public site is [substrata-docs.vercel.app](https://substrata-docs.vercel.app).
Its Vercel project is `substrata-docs` in `ikemhoods-projects`. Until its Git
integration has access to `cyforge/Substrata`, deployments require a direct
source upload. The current routes assume hosting at the domain root.
