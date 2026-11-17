"""Build two independent source artifacts without private files or core dependencies."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile


ROOT = Path(__file__).resolve().parent


def package(destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in ('mcp', 'core-reader'):
        root = ROOT / name
        # Explicit source roots and top-level public setup files only.
        paths = [root / item for item in ('pyproject.toml', 'uv.lock', 'README.md')]
        paths += [root / item for item in ('DEPLOYMENT.md', 'EXECUTION.md', 'AUTOMATIC_RULES.md', 'OPERATIONS_INTEGRATION.md') if (root / item).exists()]
        paths += sorted(root.glob('*.example.json'))
        paths += sorted(root.glob('*.ps1'))
        if name == 'mcp':
            paths += sorted((root / 'src').rglob('*.py'))
        else:
            paths += [root / 'reader.py']
        if (root / 'scripts').exists():
            paths += sorted((root / 'scripts').glob('*.ps1'))
        archive = destination / f'baidu-ads-{name}.zip'
        files = {}
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            for path in paths:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError('Package source escapes service directory')
                relative = path.relative_to(root).as_posix()
                raw = path.read_bytes()
                files[relative] = hashlib.sha256(raw).hexdigest()
                output.writestr(relative, raw)
            output.writestr('SHA256SUMS.json', json.dumps(files, indent=2) + '\n')
        manifest[name] = {'artifact': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': len(files)}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(package(parser.parse_args().output), indent=2))
