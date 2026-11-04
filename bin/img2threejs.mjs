#!/usr/bin/env node
// Installs the base skill from a pinned Git ref. The img2 harness installs plugins,
// not this repository (which intentionally has no plugin.json).

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SKILL_REPO = 'img2threejs/img2threejs'
const SKILL_URL = `https://github.com/${SKILL_REPO}.git`
// Without --ref, install the latest GitHub Release: it exists only after the governed tag
// workflow passes, unlike a raw tag. The env override is a test hook.
const RELEASES_LATEST = process.env.IMG2THREEJS_RELEASES_LATEST_URL ||
  `https://github.com/${SKILL_REPO}/releases/latest`
const STABLE_TAG_RE = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/

const HOSTS = {
  hermes: {
    label: 'Hermes Agent',
    detect: () => fs.existsSync(path.join(os.homedir(), '.hermes')),
    skills: () => path.join(os.homedir(), '.hermes', 'skills'),
  },
  claude: {
    label: 'Claude Code',
    detect: () => fs.existsSync(path.join(os.homedir(), '.claude')),
    skills: () => path.join(os.homedir(), '.claude', 'skills'),
  },
  codex: {
    label: 'OpenAI Codex',
    detect: () => fs.existsSync(path.join(os.homedir(), '.codex')),
    skills: () => path.join(os.homedir(), '.codex', 'skills'),
  },
  opencode: {
    label: 'OpenCode',
    detect: () => fs.existsSync(
      path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'opencode'),
    ),
    skills: () => path.join(
      process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'),
      'opencode',
      'skills',
    ),
  },
}

const EXIT = { OK: 0, FAIL: 1, REFUSED: 2, NEEDS_INPUT: 3 }

// Ref validation: must be a tag like `v1.2.3`, `v2.0.0-beta.1` or a full 40-char SHA.
// Anything mutable (branch name, HEAD, short SHA) is rejected — a moved branch is a different
// skill on a different day, and a short SHA can become two things after a push.
const VERSION_RE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?$/
const REF_RE = /^[0-9a-f]{40}$/

function die(code, msg, detail) {
  const e = new Error(msg)
  e.code = code
  if (detail) e.detail = detail
  throw e
}

function readArgs(argv) {
  const out = { cmd: argv[2], host: 'all', ref: null, dryRun: false }
  for (let i = 3; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--host' || a === '--ref') {
      const value = argv[++i]
      if (!value || value.startsWith('--')) die(EXIT.NEEDS_INPUT, `${a} needs a value`)
      out[a === '--host' ? 'host' : 'ref'] = value
    } else if (a === '--dry-run') out.dryRun = true
    else die(EXIT.NEEDS_INPUT, `unknown argument: ${a}`)
  }
  return out
}

function printHelp() {
  process.stdout.write(`img2threejs — install the img2threejs skill into agent hosts

Usage:
  img2threejs install [--host <host>] [--ref <tag|sha>] [--dry-run]
  img2threejs update   [--host <host>] [--ref <tag|sha>] [--dry-run]
  img2threejs doctor
  img2threejs version

Hosts:
  hermes, claude, codex, opencode, all (default: auto-detect all installed)

Ref:
  Default: the latest GitHub release (${RELEASES_LATEST}).
  --ref takes a semantic tag (vX.Y.Z, optionally prerelease) or 40-char commit SHA.
  Branches and short SHAs are refused. Requires Git and network access.
  --dry-run prints the plan without downloads or filesystem changes.

Platforms: macOS and Linux. Windows is not supported yet (directory symlinks
  need Developer Mode and the skill expects \`python3\`).

Examples:
  img2threejs install
  img2threejs install --host hermes --ref v2.0.0
  img2threejs install --ref 6e60b5e22419464b4853e01ddb6c0e6f6659a733
  img2threejs install --dry-run
`)
}

function detectHosts(hostArg) {
  if (hostArg === 'all') {
    return Object.entries(HOSTS).filter(([, h]) => h.detect()).map(([name]) => name)
  }
  if (!HOSTS[hostArg]) die(EXIT.NEEDS_INPUT, `unknown host: ${hostArg}`, Object.keys(HOSTS).join(', '))
  const host = HOSTS[hostArg]
  if (!host.detect()) die(EXIT.REFUSED, `host ${hostArg} not detected on this machine (${host.label} not installed)`)
  return [hostArg]
}

function validateRef(ref) {
  if (!(ref?.startsWith('v') && VERSION_RE.test(ref.slice(1))) && !REF_RE.test(ref)) {
    die(EXIT.REFUSED, `ref must be a vX.Y.Z tag or 40-char SHA — got: ${ref}`,
        'branches and short SHAs are refused because a moving ref would be a different skill')
  }
}

async function resolveLatestRelease() {
  let res
  try {
    res = await fetch(RELEASES_LATEST, { method: 'HEAD', redirect: 'manual', signal: AbortSignal.timeout(30_000) })
  } catch (err) {
    die(EXIT.FAIL, `cannot resolve the latest release from ${RELEASES_LATEST}: ${err.cause?.message || err.message}`,
      'check network access to github.com; behind HTTPS_PROXY this lookup cannot pass the proxy, so pass --ref <tag>')
  }
  const location = res.headers.get('location')
  let tag
  try { tag = /\/releases\/tag\/([^/]+)$/.exec(new URL(location, RELEASES_LATEST).pathname)?.[1] } catch {}
  if (![301, 302, 303, 307, 308].includes(res.status) || !STABLE_TAG_RE.test(tag || '')) {
    die(EXIT.FAIL, `latest release lookup returned HTTP ${res.status} with location ${location || '(none)'}`,
      'expected a redirect to a stable vX.Y.Z release; pass --ref <tag> to install a specific release')
  }
  return tag
}

function releasesDir() {
  return path.join(os.homedir(), '.img2threejs', 'releases')
}

function git(args, cwd) {
  return execFileSync('git', args, {
    cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 120_000,
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
  }).trim()
}

function skillVersion(dir) {
  const text = fs.readFileSync(path.join(dir, 'SKILL.md'), 'utf8')
  const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(text)?.[1]
  const version = /^version:\s*(\S+)\s*$/m.exec(frontmatter || '')?.[1]
  if (!VERSION_RE.test(version || '')) die(EXIT.FAIL, `invalid skill version in ${dir}/SKILL.md`)
  return version
}

function compareVersions(a, b) {
  const left = VERSION_RE.exec(a), right = VERSION_RE.exec(b)
  for (let i = 1; i <= 3; i++) {
    if (BigInt(left[i]) !== BigInt(right[i])) return BigInt(left[i]) > BigInt(right[i]) ? 1 : -1
  }
  if (left[4] === right[4]) return 0
  if (!left[4]) return 1
  if (!right[4]) return -1
  const x = left[4].split('.'), y = right[4].split('.')
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] === undefined) return -1
    if (y[i] === undefined) return 1
    if (x[i] === y[i]) continue
    const xn = /^\d+$/.test(x[i]), yn = /^\d+$/.test(y[i])
    if (xn && yn) return BigInt(x[i]) > BigInt(y[i]) ? 1 : -1
    if (xn !== yn) return xn ? -1 : 1
    return x[i] > y[i] ? 1 : -1
  }
  return 0
}

function managedLink(link) {
  try {
    if (!fs.lstatSync(link).isSymbolicLink()) return false
    const target = fs.realpathSync(link)
    const root = fs.realpathSync(releasesDir())
    const relative = path.relative(root, target)
    return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..' &&
      !path.isAbsolute(relative) && path.basename(target) === 'img2threejs'
  } catch {
    return false
  }
}

function danglingTarget(link) {
  try {
    return fs.lstatSync(link).isSymbolicLink() && !fs.existsSync(link) ? fs.readlinkSync(link) : null
  } catch (err) {
    if (err.code === 'ENOENT') return null
    throw err
  }
}

function checkLinks(hosts) {
  for (const h of hosts) {
    const link = path.join(HOSTS[h].skills(), 'img2threejs')
    try {
      fs.lstatSync(link)
    } catch (err) {
      if (err.code === 'ENOENT') continue
      throw err
    }
    if (managedLink(link) || danglingTarget(link) !== null) continue
    let origin = ''
    try { origin = git(['-C', link, 'remote', 'get-url', 'origin']) } catch {}
    die(EXIT.REFUSED, `refusing to replace an unmanaged skill at ${link}`,
      /[/:]img2threejs\/img2threejs(\.git)?$/.test(origin)
        ? `it is a git checkout of ${SKILL_REPO}: keep yours with \`git -C ${link} pull --ff-only\`, ` +
          'or move the link and re-run to let this CLI manage it'
        : 'move the existing entry yourself before installing with this CLI')
  }
}

async function cmdInstall(args) {
  const latest = args.ref === null
  if (!latest) validateRef(args.ref)
  const hosts = detectHosts(args.host)
  if (hosts.length === 0) die(EXIT.REFUSED, 'no supported agent host detected on this machine')
  checkLinks(hosts)
  if (args.dryRun) {
    process.stdout.write(`→ ${args.cmd} skill ${SKILL_REPO} @ ${args.ref || 'latest release (resolved at run time)'} into: ${hosts.join(', ')}\n` +
      '(dry-run: no downloads or filesystem changes; ref availability and downgrade checks require a real run)\n')
    return
  }
  if (latest) args.ref = await resolveLatestRelease()
  process.stdout.write(`→ ${args.cmd} skill ${SKILL_REPO} @ ${args.ref} into: ${hosts.join(', ')}\n`)

  const root = releasesDir()
  fs.mkdirSync(root, { recursive: true })
  const staging = fs.mkdtempSync(path.join(root, '.staging-'))
  const checkout = path.join(staging, 'img2threejs')
  try {
    fs.mkdirSync(checkout)
    git(['init', '--quiet', checkout])
    git(['remote', 'add', 'origin', SKILL_URL], checkout)
    const fetchRef = args.ref.startsWith('v') ? `refs/tags/${args.ref}` : args.ref
    git(['fetch', '--quiet', '--depth=1', 'origin', fetchRef], checkout)
    git(['checkout', '--quiet', '--detach', 'FETCH_HEAD'], checkout)
    const sha = git(['rev-parse', 'HEAD'], checkout)
    if (REF_RE.test(args.ref) && sha !== args.ref) die(EXIT.FAIL, 'fetched commit does not match requested SHA')
    const version = skillVersion(checkout)
    const skip = new Set()
    for (const h of latest || args.cmd === 'update' ? hosts : []) {
      const link = path.join(HOSTS[h].skills(), 'img2threejs')
      if (!managedLink(link) || compareVersions(version, skillVersion(link)) >= 0) continue
      if (latest) {
        process.stdout.write(`${h}: ${skillVersion(link)} is ahead of latest stable ${args.ref}; nothing to do (use --ref to pin)\n`)
        skip.add(h)
      } else if (args.cmd === 'update') {
        die(EXIT.REFUSED, `refusing downgrade for ${h}: ${skillVersion(link)} → ${version}`)
      }
    }
    if (skip.size === hosts.length) return
    const release = path.join(root, sha)
    const target = path.join(release, 'img2threejs')
    if (!fs.existsSync(release)) {
      fs.renameSync(staging, release)
    } else {
      if (git(['rev-parse', 'HEAD'], target) !== sha ||
          git(['status', '--porcelain', '--untracked-files=all'], target) !== '') {
        die(EXIT.REFUSED, `managed checkout has local changes: ${target}`)
      }
      skillVersion(target)
    }
    checkLinks(hosts)
    for (const h of hosts) {
      if (skip.has(h)) continue
      const dir = HOSTS[h].skills()
      const link = path.join(dir, 'img2threejs')
      if (managedLink(link) && fs.realpathSync(link) === fs.realpathSync(target)) {
        process.stdout.write(`✓ ${h}: already up to date (${version}, ${sha})\n`)
        continue
      }
      const dangling = danglingTarget(link)
      fs.mkdirSync(dir, { recursive: true })
      // Rename a temporary symlink over an owned link; never delete a user directory.
      const temporary = path.join(dir, `.img2threejs-${process.pid}`)
      try {
        fs.symlinkSync(target, temporary, 'dir')
        fs.renameSync(temporary, link)
      } finally {
        if (fs.existsSync(temporary)) fs.unlinkSync(temporary)
      }
      if (fs.realpathSync(link) !== fs.realpathSync(target)) die(EXIT.FAIL, `host link verification failed: ${link}`)
      if (dangling !== null) process.stdout.write(`replaced dangling link ${link} (was -> ${dangling})\n`)
      process.stdout.write(`✓ ${h}: ${link} → ${target} (${version}, ${sha})\n`)
    }
  } catch (err) {
    if (typeof err.code === 'number') throw err
    die(EXIT.FAIL, `skill ${args.cmd} failed: ${err.message}`, err.stderr?.toString().trim())
  } finally {
    fs.rmSync(staging, { recursive: true, force: true })
  }
}

async function cmdDoctor() {
  process.stdout.write('img2threejs — host detection report\n\n')
  for (const [name, h] of Object.entries(HOSTS)) {
    const dir = h.skills()
    const link = path.join(dir, 'img2threejs')
    let state = '(no link)'
    try {
      fs.lstatSync(link)
      state = `${managedLink(link) ? 'managed' : 'unmanaged'} → ${fs.realpathSync(link)} (${skillVersion(link)})`
    } catch (err) {
      if (err.code !== 'ENOENT' || fs.existsSync(dir) && fs.readdirSync(dir).includes('img2threejs')) {
        state = `invalid skill: ${err.message}`
      }
    }
    process.stdout.write(`  ${name.padEnd(10)}  ${h.detect() ? '✓ detected' : '✗ not detected'}  ${dir}  ${state}\n`)
  }
  try {
    process.stdout.write(`\n  ✓ ${git(['--version'])}\n`)
  } catch {
    process.stdout.write('\n  ✗ Git unavailable — required for install/update\n')
  }
}

async function cmdVersion() {
  const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  process.stdout.write(`img2threejs CLI: ${pkg.version}\n`)
  process.stdout.write('default skill ref: latest GitHub release\n')
}

const commands = {
  install: cmdInstall,
  update: cmdInstall,
  doctor: cmdDoctor,
  version: cmdVersion,
  '--version': cmdVersion,
}

async function main() {
  // Top-level flags: handle before command dispatch so `img2threejs --help` works
  // without first naming a subcommand.
  for (const a of process.argv.slice(2)) {
    if (a === '-h' || a === '--help' || a === 'help') { printHelp(); process.exit(EXIT.OK) }
    if (a === '--version') { await cmdVersion(); process.exit(EXIT.OK) }
  }
  if (!commands[process.argv[2]]) {
    if (process.argv[2]) process.stderr.write(`unknown command: ${process.argv[2]}\n\n`)
    printHelp()
    process.exit(process.argv[2] ? EXIT.NEEDS_INPUT : EXIT.OK)
  }
  try {
    const args = readArgs(process.argv)
    await commands[args.cmd](args)
  } catch (err) {
    if (err.code != null) {
      process.stderr.write(`error (${err.code}): ${err.message}\n`)
      if (err.detail) process.stderr.write(`detail: ${err.detail}\n`)
      process.exit(err.code)
    }
    throw err
  }
}

main().catch((err) => {
  process.stderr.write(`fatal: ${err.stack || err.message}\n`)
  process.exit(EXIT.FAIL)
})
