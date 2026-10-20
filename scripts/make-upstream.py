#!/usr/bin/env python3
"""Build the upstream submissions from this repository.

    make-upstream.py openwrt TREE [board|dsa-fix|pse-fix] [options]
    make-upstream.py netifd TREE [options]
    make-upstream.py linux TREE SERIES [options]
    make-upstream.py patches [-o DIR] [options]

openwrt  adds one pull request's commits to TREE, an openwrt.git checkout,
         and writes them with git format-patch to build/upstream/openwrt/PR:
         board    (the default) the board as three commits: the RTL8239
                  driver as a pending kernel patch with its kmod, the
                  base-files function for the PoE default, then the device;
                  without the PoE LED driver, whose LEDs follow with
                  openwrt/openwrt#25341
         dsa-fix  the link change fix in the realtek switch driver
         pse-fix  the PSE core fix as a generic pending kernel patch; with
                  --link, the patch links to its submission to netdev
netifd   adds the board.json default for the pse option to TREE, a
         netifd.git checkout, and writes it to build/upstream/netifd
linux    adds one series (pse, leds, pse-led-triggers, pse-fix,
         pse-prio-fix, which applies on top of pse-fix, pse-detect-fix or
         tps23881-disc-fix) as
         commits to TREE, a Linux checkout of the subsystem tree it goes
         to, with MAINTAINERS entries, and writes it with a cover letter and
         the subject prefix of that tree to build/upstream/linux/SERIES.
         The Kconfig and Makefile hunks are made against v6.18, the PSE
         core fixes against mainline; they are applied with a three-way
         merge. Earlier patches in that directory are removed first.
patches  writes the kernel patches as OpenWrt carries them (OpenWrt's
         kernel context) to DIR, for review; no tree needed.

Options:
  --author "Name <email>"   patch author and maintainer (default: git config)
  --signoff                 add your Signed-off-by. Only you can certify the
                            Developer's Certificate of Origin, so it is never
                            added without this option
  --assisted-by VALUE       add an Assisted-by tag with VALUE. The kernel
                            dropped the tag in October 2026 (docs: remove
                            Assisted-by references), so it is off by default
  --link URL                openwrt pse-fix: the netdev submission of the fix
  -v N, --reroll-count N    linux: version N of the series ([PATCH net v2]),
                            with the changes since the earlier versions from
                            upstream/linux/SERIES/changes.txt below the ---
                            line (or in the cover letter). Notes for the
                            reviewers in upstream/linux/SERIES/notes.txt go
                            there in every version

Commit messages live in upstream/; the drivers, bindings and patches are the
ones this repository builds. Standard library only.
"""

import argparse
import email.utils
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(REPO, 'openwrt/target/linux/realtek/files-6.18')
OWRT_PATCHES = os.path.join(REPO, 'openwrt/target/linux/realtek/patches-6.18')
UPSTREAM = os.path.join(REPO, 'upstream')
BOARD_PATCH = os.path.join(REPO, 'patches/0001-realtek-rtl930x-add-MokerLink-POE-10G07110GSM.patch')
KMOD_PATCH = os.path.join(REPO, 'patches/0002-realtek-add-RTL8239-PSE-and-port-LED-packages.patch')
DSA_PATCH = os.path.join(REPO, 'patches/0003-realtek-dsa-let-phylib-read-the-PHY-on-a-port-link-change.patch')
BASE_FILES_PATCH = os.path.join(REPO, 'patches/0004-base-files-add-a-uci-defaults-function-for-the-PSE-default.patch')
NETIFD_PATCH = os.path.join(REPO, 'openwrt/package/network/config/netifd/patches/'
                            '100-device-add-a-board.json-default-for-the-pse-option.patch')
PSE_FIX_NUMBER = '898'
DTS = 'target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts'

PSE_YAML = 'Documentation/devicetree/bindings/net/pse-pd/realtek,rtl8239.yaml'
PSE_C = 'drivers/net/pse-pd/realtek-rtl8239.c'
LEDS_YAML = 'Documentation/devicetree/bindings/leds/realtek,rtl9301-port-leds.yaml'
LEDS_C = 'drivers/leds/leds-rtl9300-port.c'

# Each patch: commit message file, new files, hunk files (OpenWrt context,
# Linux context with blob ids). An OpenWrt number and kmod per series, and
# the subject prefix of the tree it goes to.
SERIES = {
    'pse': {
        'openwrt': ('831', 'pse-realtek-rtl8239', '2-pse.txt'),
        'list': 'netdev@vger.kernel.org',
        'prefix': 'PATCH net-next',
        'maintainers': ('REALTEK RTL8239 PSE DRIVER', [PSE_YAML, PSE_C]),
        'patches': [
            ('1-binding.txt', [PSE_YAML], None, None),
            ('2-driver.txt', [PSE_C], '831-net-pse-pd-add-Realtek-RTL8239.patch', 'hooks.diff'),
        ],
    },
    # Not in the OpenWrt board: there, the PoE LEDs follow with the realtek
    # switch driver's software-controlled LEDs (openwrt/openwrt#25341)
    'leds': {
        'openwrt': None,
        'list': 'linux-leds@vger.kernel.org',
        'maintainers': ('REALTEK RTL9300 PORT LED DRIVER', [LEDS_YAML, LEDS_C]),
        'patches': [
            ('1-binding.txt', [LEDS_YAML], None, None),
            ('2-switch-binding.txt', [],
             '833-dt-bindings-net-realtek-rtl9301-switch-add-port-LED-controller.patch',
             'switch-binding.diff'),
            ('3-driver.txt', [LEDS_C], '832-leds-add-Realtek-RTL9300-port-LED-driver.patch', 'hooks.diff'),
        ],
    },
    'pse-led-triggers': {
        'openwrt': None,
        'list': 'linux-leds@vger.kernel.org',
        'maintainers': None,
        'patches': [
            ('1-binding.txt', [],
             '834-dt-bindings-leds-common-add-PSE-power-interface-triggers.patch', 'common.diff'),
        ],
    },
    # A fix for the net tree; in OpenWrt a generic pending patch
    'pse-fix': {
        'openwrt': None,
        'list': 'netdev@vger.kernel.org',
        'prefix': 'PATCH net',
        'maintainers': None,
        'patches': [
            ('1-fix.txt', [],
             '835-net-pse-pd-don-t-enable-a-PI-again-while-disabling-it.patch', 'fix.diff'),
        ],
    },
    # Applies on top of pse-fix
    'pse-prio-fix': {
        'openwrt': None,
        'list': 'netdev@vger.kernel.org',
        'prefix': 'PATCH net',
        'maintainers': None,
        'patches': [
            ('1-classify.txt', [],
             '836-net-pse-pd-don-t-allocate-the-budget-of-a-powered-PI-again.patch',
             'classify.diff'),
            ('2-fix.txt', [],
             '837-net-pse-pd-make-room-for-a-PI-without-recursing.patch', 'fix.diff'),
        ],
    },
    # Fixes not in this repository's build, as they change nothing for the
    # RTL8239: a TPS23881 driver fix, and one for a detection reported with
    # another event, which the RTL8239 driver never does
    'tps23881-disc-fix': {
        'openwrt': None,
        'list': 'netdev@vger.kernel.org',
        'prefix': 'PATCH net',
        'maintainers': None,
        'patches': [
            ('1-fix.txt', [], None, 'fix.diff'),
        ],
    },
    'pse-detect-fix': {
        'openwrt': None,
        'list': 'netdev@vger.kernel.org',
        'prefix': 'PATCH net',
        'maintainers': None,
        'patches': [
            ('1-fix.txt', [], None, 'fix.diff'),
        ],
    },
}

# Tags that end a commit message and belong in one block with the trailers
TAG = re.compile(r'(Fixes|Link|Closes|Reported-by|Suggested-by|Tested-by|Cc): ')


def die(msg):
    sys.exit(f'make-upstream: {msg}')


# UTF-8 and LF line endings also on Windows, where text files and pipes
# default to the ANSI code page and CRLF
def git(tree, *args, input=None):
    r = subprocess.run(['git', '-C', tree, *args], capture_output=True,
                       input=None if input is None else input.encode('utf-8'))
    if r.returncode:
        die(f'git {" ".join(args)} in {tree} failed:\n'
            f'{r.stderr.decode("utf-8", "replace").strip()}')
    return r.stdout.decode('utf-8')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def write(path, text, mode='w'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding='utf-8', newline='\n') as f:
        f.write(text)


def message(path):
    """(subject, body) of a commit message file"""
    text = read(path).strip('\n')
    subject, _, body = text.partition('\n')
    return subject.strip(), body.strip('\n')


def new_file_diff(path):
    lines = read(os.path.join(FILES, path)).splitlines()
    out = [f'diff --git a/{path} b/{path}', 'new file mode 100644',
           '--- /dev/null', f'+++ b/{path}', f'@@ -0,0 +1,{len(lines)} @@']
    out += ['+' + line for line in lines]
    return '\n'.join(out) + '\n'


def quilt(diff):
    """a diff as OpenWrt's make target/linux/refresh writes kernel patches:
    quilt with -p ab --no-index, so no git header lines, and diff -p, which
    cuts the function in a hunk header to 40 characters"""
    out = []
    for line in diff.splitlines(keepends=True):
        if re.match(r'(diff --git |index |(new|deleted) file mode )', line):
            continue
        m = re.match(r'(@@ -\S+ \+\S+ @@) (.+)', line)
        if m:
            line = f'{m.group(1)} {m.group(2).strip()[:40].rstrip()}\n'
        out.append(line)
    return ''.join(out)


def openwrt_hunks(name):
    """the diff part of one of this repository's hook patches"""
    text = read(os.path.join(OWRT_PATCHES, name))
    m = re.search(r'^(diff --git |--- a/)', text, re.M)
    return text[m.start():]


def trailers(args):
    t = []
    if args.assisted_by:
        t.append(f'Assisted-by: {args.assisted_by}')
    if args.signoff:
        t.append(f'Signed-off-by: {args.author}')
    return '\n'.join(t)


def with_trailers(body, args):
    """BODY and the trailers, in one block with the tags that end BODY"""
    t = trailers(args)
    if not t:
        return body
    last = body.rsplit('\n\n', 1)[-1].splitlines()
    sep = '\n' if all(TAG.match(line) for line in last) else '\n\n'
    return f'{body}{sep}{t}'


def kmod_block(name):
    """the KernelPackage definition of NAME from the kmod patch"""
    text = read(KMOD_PATCH)
    m = re.search(r'^\+define KernelPackage/%s\n.*?^\+\$\(eval \$\(call KernelPackage,%s\)\)\n'
                  % (re.escape(name), re.escape(name)), text, re.M | re.S)
    if not m:
        die(f'no KernelPackage/{name} in {KMOD_PATCH}')
    return ''.join(line[1:] + '\n' for line in m.group(0).splitlines())


def slug(subject):
    return re.sub(r'[^A-Za-z0-9]+', '-', subject).strip('-')


def check_clean(tree):
    if git(tree, 'status', '--porcelain', '--untracked-files=no').strip():
        die(f'{tree} has uncommitted changes')


def commit(tree, args, subject, body, paths):
    git(tree, 'add', *paths)
    msg = f'{subject}\n\n{with_trailers(body, args)}\n'
    git(tree, '-c', f'user.name={args.name}', '-c', f'user.email={args.email}',
        'commit', '-q', f'--author={args.author}', '-F', '-', input=msg)


def openwrt_patch_text(args, series, i, n):
    """one kernel patch of SERIES, with OpenWrt's kernel context"""
    msgfile, files, hookpatch, _ = SERIES[series]['patches'][i]
    subject, body = message(os.path.join(UPSTREAM, 'linux', series, msgfile))
    if series == 'pse-fix' and args.link:
        body += f'\nLink: {args.link}'   # after the Fixes tag that ends it
    diff = ''.join(new_file_diff(p) for p in files)
    if hookpatch:
        diff += openwrt_hunks(hookpatch)
    diff = quilt(diff)
    number = f' {i + 1}/{n}' if n > 1 else ''
    # The header of git format-patch, which OpenWrt's checks ask of pending
    # patches; dated by the last change to the commit message, so that the
    # patch only changes when it does
    msgpath = os.path.join(UPSTREAM, 'linux', series, msgfile)
    r = subprocess.run(['git', '-C', REPO, 'log', '-1', '--format=%aD', '--', msgpath],
                       capture_output=True, text=True)
    date = r.stdout.strip() or email.utils.formatdate(localtime=True)
    return subject, (f'From {"0" * 40} Mon Sep 17 00:00:00 2001\n'
                     f'From: {args.author}\nDate: {date}\n'
                     f'Subject: [PATCH{number}] {subject}\n\n'
                     f'{with_trailers(body, args)}\n---\n{diff}')


def cmd_patches(args):
    out = args.output or os.path.join(REPO, 'build/upstream/patches')
    for series, s in SERIES.items():
        n = len(s['patches'])
        for i in range(n):
            _, files, hookpatch, _ = s['patches'][i]
            if not files and not hookpatch:
                continue   # nothing in this repository's build
            subject, text = openwrt_patch_text(args, series, i, n)
            name = f'{series}-{i + 1:02d}-{slug(subject)}.patch'
            write(os.path.join(out, name), text)
            print(os.path.join(out, name))


def pinned_openwrt():
    m = re.search(r'^OPENWRT_COMMIT=(\w+)', read(os.path.join(REPO, 'config/openwrt.env')), re.M)
    return m.group(1)


def apply_3way(tree, diff, pinned=None):
    """apply one of this repository's patches with a three-way merge, for
    when the tree has moved on since the commit it was made against; the
    paths it changes"""
    paths = re.findall(r'^\+\+\+ b/(\S+)', diff, re.M)
    # The index lines only abbreviate the original blobs, which a partial
    # clone cannot fetch; fetch them by path from the pinned commit
    for p in paths if pinned else []:
        subprocess.run(['git', '-C', tree, 'cat-file', 'blob', f'{pinned}:{p}'],
                       capture_output=True)
    git(tree, 'apply', '--3way', '-', input=diff)
    return paths


def commit_patch_file(tree, args, path, pinned=None):
    """commit one of this repository's patch files: a Subject line, the
    message, then the diff after ---"""
    m = re.match(r'Subject: \[PATCH\] (.+?)\n\n(.*?)\n---\n(.*)', read(path), re.S)
    if not m:
        die(f'no Subject, message and diff in {path}')
    subject, body, diff = m.groups()
    commit(tree, args, subject, body.strip('\n'), apply_3way(tree, diff, pinned))


def upstream_dts(text):
    """the device tree for OpenWrt: without the node of this repository's
    PoE LED driver, whose LEDs follow with openwrt/openwrt#25341"""
    m = re.search(r'\n\t/\*\n\t \* PoE status LEDs\..*?\n\t\};\n'
                  r'(?=\};\n)', text, re.S)
    if not m or 'realtek,rtl9301-port-leds' not in m.group(0):
        die('no PoE LED node in the device tree')
    text = text[:m.start()] + text[m.end():]
    for old, new in (
            ('\t * and is driven in software (led-controller@cc48 below). The cells\n'
             '\t * list LED 2 first. Set 1 is the SFP+ port\'s.\n',
             '\t * and needs software control. The cells list LED 2 first. Set 1 is\n'
             '\t * the SFP+ port\'s.\n'),
            ('\t * Fixed I2C bus numbers, so that the PSE LED trigger names below\n',
             '\t * Fixed I2C bus numbers, so that the PSE LED trigger names\n')):
        if old not in text:
            die(f'no comment {old.strip()!r} in the device tree')
        text = text.replace(old, new)
    # Only the PoE LED node used it
    return text.replace('#include <dt-bindings/leds/common.h>\n', '')


def openwrt_board(tree, args):
    patchdir = 'target/linux/realtek/patches-6.18'
    modules = os.path.join(tree, 'target/linux/realtek/modules.mk')
    for series, s in SERIES.items():
        if not s['openwrt']:
            continue
        number, kmod, msgfile = s['openwrt']
        n = len(s['patches'])
        paths = ['target/linux/realtek/modules.mk']
        for i in range(n):
            subject, text = openwrt_patch_text(args, series, i, n)
            name = f'{number}-{i + 1:02d}-{slug(subject)}.patch'
            write(os.path.join(tree, patchdir, name), text)
            paths.append(f'{patchdir}/{name}')
        write(modules, '\n' + kmod_block(kmod), mode='a')
        subject, body = message(os.path.join(UPSTREAM, 'openwrt', msgfile))
        commit(tree, args, subject, body, paths)

    # The function for the device's PoE default
    commit_patch_file(tree, args, BASE_FILES_PATCH, pinned_openwrt())

    # The device: image recipe, network setup, PoE default and fan control,
    # then the device tree, both without this repository's PoE LED driver
    paths = apply_3way(tree, read(BOARD_PATCH), pinned_openwrt())
    image = os.path.join(tree, 'target/linux/realtek/image/rtl930x.mk')
    text = read(image)
    if '\tkmod-leds-rtl9300-port \\\n' not in text:
        die('no kmod-leds-rtl9300-port in the device recipe')
    write(image, text.replace('\tkmod-leds-rtl9300-port \\\n', '', 1))
    dts = os.path.join(tree, DTS)
    write(dts, upstream_dts(read(os.path.join(REPO, 'openwrt', DTS))))
    subject, body = message(os.path.join(UPSTREAM, 'openwrt', '4-board.txt'))
    commit(tree, args, subject, body, paths + [DTS])


def openwrt_dsa_fix(tree, args):
    """the switch driver fix, a patch to OpenWrt's own files"""
    commit_patch_file(tree, args, DSA_PATCH, pinned_openwrt())


def openwrt_pse_fix(tree, args):
    """the PSE core fix as a generic pending patch, after the PSE series
    (897-*) whose changes it applies on top of"""
    subject, text = openwrt_patch_text(args, 'pse-fix', 0, 1)
    path = f'target/linux/generic/pending-6.18/{PSE_FIX_NUMBER}-{slug(subject)}.patch'
    write(os.path.join(tree, path), text)
    subject, body = message(os.path.join(UPSTREAM, 'openwrt', '5-pse-fix.txt'))
    commit(tree, args, subject, body, [path])


OPENWRT_PRS = {'board': openwrt_board, 'dsa-fix': openwrt_dsa_fix,
               'pse-fix': openwrt_pse_fix}


def cmd_openwrt(args):
    tree = os.path.abspath(args.tree)
    pr = args.series or 'board'
    if pr not in OPENWRT_PRS:
        die(f'unknown OpenWrt pull request {pr}; one of {", ".join(OPENWRT_PRS)}')
    check_clean(tree)
    base = git(tree, 'rev-parse', 'HEAD').strip()
    OPENWRT_PRS[pr](tree, args)

    out = args.output or os.path.join(REPO, 'build/upstream/openwrt', pr)
    os.makedirs(out, exist_ok=True)
    print(git(tree, '-c', f'user.name={args.name}', '-c', f'user.email={args.email}',
              'format-patch', '-o', out, f'{base}..HEAD'), end='')


def cmd_netifd(args):
    """the board.json PSE default, which this repository's build carries as
    a patch to OpenWrt's netifd package, as a commit to netifd"""
    tree = os.path.abspath(args.tree)
    check_clean(tree)
    base = git(tree, 'rev-parse', 'HEAD').strip()
    commit_patch_file(tree, args, NETIFD_PATCH)

    out = args.output or os.path.join(REPO, 'build/upstream/netifd')
    os.makedirs(out, exist_ok=True)
    print(git(tree, '-c', f'user.name={args.name}', '-c', f'user.email={args.email}',
              'format-patch', '-o', out, f'{base}..HEAD'), end='')


# A section title: after a blank line, not a field line, followed by a field
MAINT_HEADER = re.compile(r'(?<=\n\n)(?![A-Z]:\t)[^\n]+\n(?=[A-Z]:\t)')


def add_maintainers(tree, args, title, files, mlist):
    path = os.path.join(tree, 'MAINTAINERS')
    text = read(path)
    entry = (f'{title}\nM:\t{args.author}\nL:\t{mlist}\nS:\tMaintained\n'
             + ''.join(f'F:\t{f}\n' for f in files) + '\n')
    # The file is sorted, but not strictly: insert after the section with
    # the largest title that still sorts before the new one
    heads = [m for m in MAINT_HEADER.finditer(text)
             if m.group(0).rstrip('\n') != 'THE REST']
    best = None
    for i, m in enumerate(heads):
        head = m.group(0).rstrip('\n').upper()
        if head < title.upper() and (best is None or head > best[0]):
            best = (head, i)
    if best and best[1] + 1 < len(heads):
        at = heads[best[1] + 1].start()
        write(path, text[:at] + entry + text[at:])
        return
    die('could not place the MAINTAINERS entry')


def cmd_linux(args):
    tree = os.path.abspath(args.tree)
    if args.series not in SERIES:
        die(f'unknown series {args.series}; one of {", ".join(SERIES)}')
    check_clean(tree)
    s = SERIES[args.series]
    base = git(tree, 'rev-parse', 'HEAD').strip()
    for i, (msgfile, files, _, hooks) in enumerate(s['patches']):
        paths = []
        for p in files:
            write(os.path.join(tree, p), read(os.path.join(FILES, p)))
            paths.append(p)
        if hooks:
            diff = read(os.path.join(UPSTREAM, 'linux', args.series, hooks))
            git(tree, 'apply', '--3way', '-', input=diff)
            paths += re.findall(r'^\+\+\+ b/(\S+)', diff, re.M)
        if s['maintainers'] and files and any(p.endswith('.c') for p in files):
            title, mfiles = s['maintainers']
            add_maintainers(tree, args, title, mfiles, s['list'])
            paths.append('MAINTAINERS')
        subject, body = message(os.path.join(UPSTREAM, 'linux', args.series, msgfile))
        commit(tree, args, subject, body, paths)

    out = args.output or os.path.join(REPO, 'build/upstream/linux', args.series)
    os.makedirs(out, exist_ok=True)
    # Only this version, so that sending *.patch can't send an earlier one
    for name in os.listdir(out):
        if name.endswith('.patch'):
            os.remove(os.path.join(out, name))
    n = len(s['patches'])
    fmt = ['-c', f'user.name={args.name}', '-c', f'user.email={args.email}',
           'format-patch', f'--subject-prefix={s.get("prefix", "PATCH")}',
           '-o', out, f'{base}..HEAD']
    if n > 1:
        fmt.insert(5, '--cover-letter')
    changes = None
    if args.reroll_count:
        fmt.insert(5, f'--reroll-count={args.reroll_count}')
        path = os.path.join(UPSTREAM, 'linux', args.series, 'changes.txt')
        if not os.path.exists(path):
            die(f'-v {args.reroll_count} needs {path}')
        changes = read(path).strip('\n')
    # Notes for the reviewers, such as a request for testing, go with the
    # changes since earlier versions: outside the commit message
    path = os.path.join(UPSTREAM, 'linux', args.series, 'notes.txt')
    notes = read(path).strip('\n') if os.path.exists(path) else None
    changes = '\n\n'.join(x for x in (notes, changes) if x) or None
    files = git(tree, *fmt).split()
    print('\n'.join(files))
    cover = os.path.join(UPSTREAM, 'linux', args.series, 'cover.txt')
    if n > 1 and os.path.exists(cover):
        subject, body = message(cover)
        if changes:
            body += '\n\n' + changes
        letter = files[0]
        text = read(letter)
        text = text.replace('*** SUBJECT HERE ***', subject)
        text = text.replace('*** BLURB HERE ***', body)
        write(letter, text)
    elif changes:
        # Below the --- line, which git am leaves out of the commit
        text = read(files[0])
        text = text.replace('\n---\n', f'\n---\n{changes}\n\n', 1)
        write(files[0], text)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['openwrt', 'netifd', 'linux', 'patches'])
    p.add_argument('tree', nargs='?')
    p.add_argument('series', nargs='?')
    p.add_argument('-o', '--output')
    p.add_argument('--author')
    p.add_argument('--signoff', action='store_true')
    p.add_argument('--assisted-by')
    p.add_argument('--link')
    p.add_argument('-v', '--reroll-count', type=int)
    args = p.parse_args()

    if not args.author:
        name = subprocess.run(['git', 'config', 'user.name'], capture_output=True,
                              text=True).stdout.strip()
        email = subprocess.run(['git', 'config', 'user.email'], capture_output=True,
                               text=True).stdout.strip()
        if not name or not email:
            die('set --author "Name <email>" or git config user.name/user.email')
        args.author = f'{name} <{email}>'
    m = re.fullmatch(r'(.+?) <([^>]+)>', args.author)
    if not m:
        die('--author must be "Name <email>"')
    args.name, args.email = m.groups()

    if args.command == 'patches':
        cmd_patches(args)
    elif args.command == 'openwrt':
        if not args.tree:
            die('openwrt needs TREE')
        cmd_openwrt(args)
    elif args.command == 'netifd':
        if not args.tree:
            die('netifd needs TREE')
        cmd_netifd(args)
    else:
        if not args.tree or not args.series:
            die('linux needs TREE and SERIES')
        cmd_linux(args)
    if not args.signoff:
        print('note: no Signed-off-by added; add it once you have reviewed the patches '
              '(--signoff)', file=sys.stderr)


if __name__ == '__main__':
    main()
