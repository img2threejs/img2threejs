#!/usr/bin/env node
// Real Git fixtures; no network and no writes outside a disposable temp directory.
import assert from 'node:assert/strict'
import { execFileSync, spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, after } from 'node:test'

const cli = fileURLToPath(new URL('./img2threejs.mjs', import.meta.url))
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'img2threejs-test-')))
after(() => fs.rmSync(root, { recursive: true, force: true }))
const realGit = execFileSync('which', ['git'], { encoding: 'utf8' }).trim()
const repo = path.join(root, 'source')
fs.mkdirSync(repo)
const gitEnv = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
  GIT_AUTHOR_NAME: 'Test', GIT_AUTHOR_EMAIL: 'test@example.invalid',
  GIT_COMMITTER_NAME: 'Test', GIT_COMMITTER_EMAIL: 'test@example.invalid' }
function git(...args) {
  return execFileSync(realGit, args, { cwd: repo, env: gitEnv, encoding: 'utf8' }).trim()
}
git('init', '--quiet')
const commits = {}
// v2.2.0 is tagged but never released: only the releases/latest redirect may select a default.
for (const version of ['1.5.2', '2.0.0-beta.2', '2.0.0-beta.10', '2.0.0', '2.1.0', '2.2.0']) {
  fs.writeFileSync(path.join(repo, 'SKILL.md'), `---\nname: img2threejs\nversion: ${version}\n---\nfixture ${version}\n`)
  fs.mkdirSync(path.join(repo, 'forge'), { recursive: true })
  fs.writeFileSync(path.join(repo, 'forge', 'fixture.py'), `VERSION = '${version}'\n`)
  git('add', '.')
  git('commit', '--quiet', '-m', version)
  git('tag', `v${version}`)
  commits[version] = git('rev-parse', 'HEAD')
}
const tools = path.join(root, 'tools')
fs.mkdirSync(tools)
// Only redirect the remote URL; fetch, checkout, SHA and status use the real Git binary.
fs.writeFileSync(path.join(tools, 'git'), `#!${process.execPath}\nimport { spawnSync } from 'node:child_process';\nconst args = process.argv.slice(2).map(a => a === 'https://github.com/img2threejs/img2threejs.git' ? process.env.FIXTURE_REPO : a);\nconst result = spawnSync(process.env.REAL_GIT, args, {stdio:'inherit'});\nprocess.exit(result.status ?? 1);\n`, { mode: 0o755 })
// Extensionless executables default to CommonJS outside a package.
fs.writeFileSync(path.join(tools, 'package.json'), '{"type":"module"}')
// The release server runs in its own process because the CLI is run with spawnSync.
// /tag/<tag> redirects to that release (/tag/<tag>/<code> with that status), /status/<code> answers <code>,
// /none redirects without a location, /malformed redirects to an unparseable URL.
const hits = path.join(root, 'release-hits.log')
const releaseServer = spawn(process.execPath, ['-e', `
const http = require('node:http'), fs = require('node:fs')
const server = http.createServer((req, res) => {
  fs.appendFileSync(process.env.HITS, req.method + ' ' + req.url + '\\n')
  const [, kind, value, code] = req.url.split('/')
  if (kind === 'status') res.writeHead(Number(value))
  else if (kind === 'none') res.writeHead(302)
  else if (kind === 'malformed') res.writeHead(302, { location: 'http://[bad' })
  else res.writeHead(Number(code || 302), { location: 'https://github.com/img2threejs/img2threejs/releases/tag/' + value })
  res.end()
})
server.listen(0, '127.0.0.1', () => console.log(server.address().port))
`], { env: { ...process.env, HITS: hits }, stdio: ['ignore', 'pipe', 'inherit'] })
after(() => releaseServer.kill())
const port = await new Promise((resolve) => releaseServer.stdout.once('data', (d) => resolve(String(d).trim())))
const latest = (tag) => ({ IMG2THREEJS_RELEASES_LATEST_URL: `http://127.0.0.1:${port}/tag/${tag}` })
const hitCount = () => fs.existsSync(hits) ? fs.readFileSync(hits, 'utf8').split('\n').length - 1 : 0
let sequence = 0
function home(hosts = ['claude']) {
  const dir = path.join(root, `home-${sequence++}`)
  fs.mkdirSync(dir)
  for (const host of hosts) fs.mkdirSync(host === 'opencode' ? path.join(dir, 'xdg', 'opencode') : path.join(dir, `.${host}`), { recursive: true })
  return dir
}
function skills(dir, host = 'claude') {
  return host === 'opencode' ? path.join(dir, 'xdg', 'opencode', 'skills') : path.join(dir, `.${host}`, 'skills')
}
function runEnv(extra, dir, ...args) {
  return spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 20000,
    env: { ...gitEnv, HOME: dir, XDG_CONFIG_HOME: path.join(dir, 'xdg'),
      PATH: `${tools}${path.delimiter}${process.env.PATH}`, FIXTURE_REPO: repo, REAL_GIT: realGit,
      ...latest('v2.0.0'), ...extra } })
}
function run(dir, ...args) {
  return runEnv({}, dir, ...args)
}
function success(result) {
  assert.equal(result.status, 0, result.stderr || result.error?.message)
}
function installed(dir, host = 'claude') {
  const link = path.join(skills(dir, host), 'img2threejs')
  assert.ok(fs.lstatSync(link).isSymbolicLink())
  return fs.realpathSync(link)
}

test('unknown commands and missing values fail without a host', () => {
  const dir = home([])
  assert.equal(run(dir, 'unknown').status, 3)
  assert.equal(run(dir, 'install', '--host').status, 3)
  assert.equal(run(dir, 'install', '--ref', '--dry-run').status, 3)
})

test('branches, short SHAs and malformed tags are refused', () => {
  const dir = home()
  for (const ref of ['main', '123abcd', 'v01.2.3', 'v2.0.0-beta..1', 'v2.0.0-01']) {
    assert.equal(run(dir, 'install', '--ref', ref).status, 2, ref)
  }
  assert.ok(!fs.existsSync(path.join(dir, '.img2threejs')))
})

test('no detected hosts or an unknown host is refused', () => {
  assert.equal(run(home([]), 'install').status, 2)
  assert.equal(run(home(), 'install', '--host', 'invalid').status, 3)
})

test('dry-run is offline and does not modify the home', () => {
  const dir = home()
  const before = fs.readdirSync(dir)
  const requests = hitCount()
  const noGit = path.join(root, 'no-git')
  fs.mkdirSync(noGit, { recursive: true })
  const result = runEnv({ PATH: noGit }, dir, 'install', '--dry-run')
  success(result)
  assert.match(result.stdout, /@ latest release \(resolved at run time\)/)
  assert.equal(hitCount(), requests)
  assert.deepEqual(fs.readdirSync(dir), before)
  assert.ok(!fs.existsSync(skills(dir)))
})

test('version reports the latest GitHub release as the default ref', () => {
  for (const arg of ['version', '--version']) {
    const result = run(home([]), arg)
    success(result)
    assert.match(result.stdout, /default skill ref: latest GitHub release/)
  }
})

test('no --ref installs the latest release, not the newest tag', () => {
  const dir = home()
  const result = runEnv(latest('v2.1.0'), dir, 'install')
  success(result)
  assert.match(result.stdout, /@ v2\.1\.0 /)
  assert.equal(path.basename(path.dirname(installed(dir))), commits['2.1.0'])
})

test('update follows the latest release, then reports already up to date', () => {
  const dir = home()
  success(run(dir, 'install'))
  assert.equal(path.basename(path.dirname(installed(dir))), commits['2.0.0'])
  success(runEnv(latest('v2.1.0'), dir, 'update'))
  const link = path.join(skills(dir), 'img2threejs')
  const before = fs.lstatSync(link)
  assert.equal(path.basename(path.dirname(installed(dir))), commits['2.1.0'])
  const again = runEnv(latest('v2.1.0'), dir, 'update')
  success(again)
  assert.match(again.stdout, new RegExp(`✓ claude: already up to date \\(2\\.1\\.0, ${commits['2.1.0']}\\)`))
  assert.equal(fs.lstatSync(link).ino, before.ino)
})

test('a beta install ahead of the latest stable release is left alone', () => {
  const dir = home()
  success(run(dir, 'install', '--ref', 'v2.0.0-beta.10'))
  const target = installed(dir)
  const result = runEnv(latest('v1.5.2'), dir, 'update')
  success(result)
  assert.match(result.stdout, /claude: 2\.0\.0-beta\.10 is ahead of latest stable v1\.5\.2; nothing to do \(use --ref to pin\)/)
  assert.equal(installed(dir), target)
})

test('a failed or non-stable latest-release lookup exits 1 and keeps the install', () => {
  const dir = home()
  success(run(dir, 'install'))
  const target = installed(dir)
  const base = `http://127.0.0.1:${port}`
  for (const url of [`${base}/status/500`, `${base}/status/200`, `${base}/none`, `${base}/malformed`, `${base}/tag/main`,
    `${base}/tag/${commits['2.1.0']}`, `${base}/tag/v2.2.0-beta.1`, `${base}/tag/v02.1.0`, 'http://127.0.0.1:1/']) {
    const result = runEnv({ IMG2THREEJS_RELEASES_LATEST_URL: url }, dir, 'update')
    assert.equal(result.status, 1, `${url}: ${result.stderr}`)
    assert.match(result.stderr, url.endsWith(':1/') ? /cannot resolve the latest release/ : /latest release lookup returned HTTP/)
    assert.equal(installed(dir), target)
  }
  assert.deepEqual(fs.readdirSync(path.join(dir, '.img2threejs', 'releases')), [commits['2.0.0']])
})

test('any permanent or temporary redirect status is accepted', () => {
  for (const code of [301, 303, 307, 308]) {
    const dir = home()
    success(runEnv({ IMG2THREEJS_RELEASES_LATEST_URL: `http://127.0.0.1:${port}/tag/v2.1.0/${code}` }, dir, 'install'))
    assert.equal(path.basename(path.dirname(installed(dir))), commits['2.1.0'])
  }
})

test('a host ahead of stable is skipped while an empty host still gets the latest release', () => {
  const dir = home(['claude', 'codex'])
  success(run(dir, 'install', '--host', 'claude', '--ref', 'v2.0.0-beta.10'))
  const target = installed(dir)
  const result = runEnv(latest('v1.5.2'), dir, 'install')
  success(result)
  assert.match(result.stdout, /claude: 2\.0\.0-beta\.10 is ahead of latest stable v1\.5\.2/)
  assert.equal(installed(dir), target)
  assert.equal(path.basename(path.dirname(installed(dir, 'codex'))), commits['1.5.2'])
})

test('a dangling host link is replaced', () => {
  const dir = home()
  fs.mkdirSync(skills(dir), { recursive: true })
  const missing = path.join(dir, 'deleted-checkout')
  fs.symlinkSync(missing, path.join(skills(dir), 'img2threejs'))
  const result = run(dir, 'install')
  success(result)
  assert.ok(result.stdout.includes(`replaced dangling link ${path.join(skills(dir), 'img2threejs')} (was -> ${missing})`))
  assert.equal(path.basename(path.dirname(installed(dir))), commits['2.0.0'])
})

test('an unmanaged link to a skill checkout names both ways forward', () => {
  const dir = home()
  const checkout = path.join(dir, 'workspace-checkout')
  fs.mkdirSync(checkout)
  execFileSync(realGit, ['init', '--quiet', checkout], { env: gitEnv })
  execFileSync(realGit, ['-C', checkout, 'remote', 'add', 'origin', 'git@github.com:img2threejs/img2threejs.git'], { env: gitEnv })
  fs.mkdirSync(skills(dir), { recursive: true })
  const link = path.join(skills(dir), 'img2threejs')
  fs.symlinkSync(checkout, link)
  const result = run(dir, 'install')
  assert.equal(result.status, 2)
  assert.ok(result.stderr.includes(`git -C ${link} pull --ff-only`), result.stderr)
  assert.match(result.stderr, /move the link and re-run/)
  assert.equal(fs.readlinkSync(link), checkout)
})

test('explicit host installs the full pinned skill without linking other hosts', () => {
  const dir = home(['claude', 'codex', 'hermes', 'opencode'])
  success(run(dir, 'install', '--host', 'claude'))
  const target = installed(dir)
  assert.match(fs.readFileSync(path.join(target, 'SKILL.md'), 'utf8'), /version: 2\.0\.0\n/)
  assert.equal(fs.readFileSync(path.join(target, 'forge', 'fixture.py'), 'utf8'), "VERSION = '2.0.0'\n")
  assert.equal(execFileSync(realGit, ['rev-parse', 'HEAD'], { cwd: target, encoding: 'utf8' }).trim(), commits['2.0.0'])
  for (const host of ['codex', 'hermes', 'opencode']) assert.ok(!fs.existsSync(skills(dir, host)))
})

test('all four hosts share one checkout; reinstallation is idempotent', () => {
  const dir = home(['claude', 'codex', 'hermes', 'opencode'])
  success(run(dir, 'install'))
  const target = installed(dir)
  for (const host of ['codex', 'hermes', 'opencode']) assert.equal(installed(dir, host), target)
  success(run(dir, 'install'))
  assert.equal(installed(dir), target)
  assert.deepEqual(fs.readdirSync(path.join(dir, '.img2threejs', 'releases')), [commits['2.0.0']])
  const report = run(dir, 'doctor')
  success(report)
  assert.match(report.stdout, /managed →/)
})

test('update advances a pinned checkout and refuses tag or SHA downgrades', () => {
  const dir = home()
  success(run(dir, 'install', '--ref', 'v1.5.2'))
  success(run(dir, 'update'))
  const target = installed(dir)
  for (const ref of ['v1.5.2', commits['1.5.2']]) {
    const result = run(dir, 'update', '--ref', ref)
    assert.equal(result.status, 2, result.stderr)
    assert.match(result.stderr, /downgrade/)
    assert.equal(installed(dir), target)
  }
  assert.ok(!fs.readdirSync(path.join(dir, '.img2threejs', 'releases')).some(n => n.startsWith('.staging-')))
})

test('prerelease precedence uses numeric identifiers and stable beats prerelease', () => {
  const dir = home()
  success(run(dir, 'install', '--ref', 'v2.0.0-beta.2'))
  success(run(dir, 'update', '--ref', 'v2.0.0-beta.10'))
  assert.equal(run(dir, 'update', '--ref', 'v2.0.0-beta.2').status, 2)
  success(run(dir, 'update', '--ref', commits['2.0.0']))
  assert.equal(run(dir, 'update', '--ref', 'v2.0.0-beta.10').status, 2)
})

test('unmanaged directories and symlinks are preserved; no hosts partially installed', () => {
  for (const kind of ['directory', 'symlink']) {
    const dir = home(['claude', 'codex'])
    fs.mkdirSync(skills(dir, 'codex'), { recursive: true })
    const link = path.join(skills(dir, 'codex'), 'img2threejs')
    if (kind === 'directory') {
      fs.mkdirSync(link)
      fs.writeFileSync(path.join(link, 'keep'), 'user data')
    } else {
      fs.mkdirSync(path.join(dir, 'elsewhere'))
      fs.symlinkSync(path.join(dir, 'elsewhere'), link)
    }
    assert.equal(run(dir, 'install').status, 2)
    assert.ok(!fs.existsSync(skills(dir)))
    if (kind === 'directory') assert.equal(fs.readFileSync(path.join(link, 'keep'), 'utf8'), 'user data')
    else assert.equal(fs.readlinkSync(link), path.join(dir, 'elsewhere'))
    assert.ok(!fs.existsSync(path.join(dir, '.img2threejs')))
  }
})

test('a missing ref preserves the existing installation and cleans staging', () => {
  const dir = home()
  success(run(dir, 'install'))
  const target = installed(dir)
  assert.equal(run(dir, 'update', '--ref', 'v99.0.0').status, 1)
  assert.equal(installed(dir), target)
  assert.deepEqual(fs.readdirSync(path.join(dir, '.img2threejs', 'releases')), [commits['2.0.0']])
})

test('local edits in an existing managed release are never overwritten', () => {
  const dir = home()
  success(run(dir, 'install'))
  const target = installed(dir)
  fs.writeFileSync(path.join(target, 'forge', 'fixture.py'), 'user edit\n')
  assert.equal(run(dir, 'install').status, 2)
  assert.equal(fs.readFileSync(path.join(target, 'forge', 'fixture.py'), 'utf8'), 'user edit\n')
})
