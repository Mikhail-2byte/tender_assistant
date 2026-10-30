"""Read PyPI vulnerability records for installed application/build dependencies."""
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
import json
from pathlib import Path
import sys

import requests
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main():
    output = Path(sys.argv[1])
    root = Path(__file__).resolve().parents[1]
    pending = []
    for filename in ('requirements.txt', 'requirements-dev.txt'):
        pending.extend(line.strip() for line in (root / filename).read_text().splitlines()
                       if line.strip() and not line.startswith(('#', '-')))
    versions = {}
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in versions:
            continue
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        pending.extend(distribution.requires or [])

    def check(item):
        name, version = item
        entry = {'package': name, 'version': version,
                 'source': f'https://pypi.org/pypi/{name}/{version}/json'}
        try:
            with requests.get(entry['source'], timeout=(5, 15)) as response:
                response.raise_for_status()
                payload = response.json()
                entry['vulnerabilities'] = payload['vulnerabilities']
                entry['status'] = 'checked'
        except (requests.RequestException, ValueError, KeyError):
            entry['status'] = 'unavailable'
        print(f'{name} {version}: {entry["status"]}', flush=True)
        return entry

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, sorted(versions.items())))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(entry['status'] != 'checked' or entry['vulnerabilities'] for entry in results))


if __name__ == '__main__':
    raise SystemExit(main())
