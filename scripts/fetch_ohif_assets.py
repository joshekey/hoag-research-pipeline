"""Fetch the pinned official OHIF 3.9.2 static bundle; no Docker daemon needed.

Only static files from the verified image layer are extracted, never executed.
The destination must be new. Clinical data and configuration are never uploaded.
Source: https://docs.ohif.org/deployment/docker/ (ohif/app:v3.9.2).
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

LAYER = '3f0d19f961ee98a0584e4a30b8800878c9ac53e3418a58574535dd441435f25b'
SIZE = 12283955
PREFIX = 'usr/share/nginx/html/'


def download(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Use a new destination directory; existing viewer files are preserved')
    with urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:ohif/app:pull', timeout=60) as response:
        token = json.load(response)['token']
    request = urllib.request.Request('https://registry-1.docker.io/v2/ohif/app/blobs/sha256:' + LAYER,
                                     headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(SIZE + 1)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != LAYER:
        raise ValueError('OHIF image layer checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = [m for m in archive if m.isfile() and m.name.startswith(PREFIX)]
        if not any(m.name == PREFIX + 'index.html' for m in members):
            raise ValueError('OHIF bundle has no entry point')
        for member in members:
            relative = PurePosixPath(member.name[len(PREFIX):])
            if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative):
                raise ValueError('Invalid bundle path')
        destination.mkdir(parents=True)
        for member in members:
            target = destination / member.name[len(PREFIX):]
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                target.write_bytes(source.read())
    print('Verified OHIF 3.9.2 static assets installed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    download(parser.parse_args().destination)
