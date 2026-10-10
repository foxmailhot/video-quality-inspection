import hashlib, json, os, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spec = json.loads((root / 'publish.json').read_text('utf-8-sig'))
version = spec['version']
tag = 'v' + version
identity = json.loads(subprocess.run(['gh', 'api', 'repositories/1412709349'], check=True, capture_output=True, text=True, encoding='utf-8').stdout)
repo = identity['full_name']
file = root / 'release-output' / f'FrameScope-{version}-Setup-x64.exe'
digest = hashlib.sha256(file.read_bytes()).hexdigest()
manifest = dict(product='FrameScope', platform='windows-x64', version=version,
                size=file.stat().st_size, sha256=digest, notes=spec['notes'],
                parts=[dict(url=f'https://github.com/{repo}/releases/download/{tag}/{file.name}',
                            size=file.stat().st_size, sha256=digest)])
record = file.parent / 'latest.json'
record.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
checksum = file.with_suffix('.sha256.txt')
checksum.write_text(digest + '  ' + file.name + '\n', 'ascii')
notes = file.parent / 'notes.md'
notes.write_text(spec['notes'], 'utf-8')

def gh(*args):
    return subprocess.run(['gh', *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout

existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'isDraft'],
                          capture_output=True, text=True, encoding="utf-8")
if existing.returncode == 0:
    if not json.loads(existing.stdout)['isDraft']:
        raise SystemExit('Published version already exists; choose a new version')
else:
    gh('release', 'create', tag, '--repo', repo, '--draft', '--title', 'FrameScope ' + version,
       '--notes-file', str(notes), '--target', os.environ['GITHUB_SHA'])
gh('release', 'upload', tag, str(file), str(record), str(checksum), '--repo', repo, '--clobber')
release = json.loads(gh('api', f'repos/{repo}/releases/tags/{tag}'))
assets = {asset['name']: asset for asset in release['assets']}
for path in (file, record, checksum):
    asset = assets[path.name]
    if asset['size'] != path.stat().st_size:
        raise SystemExit('Uploaded asset size mismatch')
    if asset.get('digest') and asset['digest'] != 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest():
        raise SystemExit('Uploaded asset hash mismatch')
gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--latest')
print(f'Published https://github.com/{repo}/releases/tag/{tag}')
