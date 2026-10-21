"""Run real Chromium through a temporary HTTPS Caddy on isolated test images.

Requires local Docker, openssl, Node and the locked tests/browser dependencies.
No production project is accepted. App/DB ports stay private; only the temporary
proxy publishes an ephemeral port bound to 127.0.0.1. Reports contain no secrets.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*arguments, source=None, timeout=45):
    environment = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'DOCKER_API_VERSION'):
        environment.pop(key, None)
    result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *arguments],
                            input=source, stdin=None if source is not None else subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout, env=environment)
    require(result.returncode == 0, 'Docker operation failed: ' + ' '.join(arguments[:2]))
    return result.stdout


def preflight(args):
    require(re.fullmatch(r'score-it-check-[a-z0-9][a-z0-9_-]*', args.project or ''),
            'Only an isolated score-it-check-* project is accepted')
    require(re.fullmatch(r'[0-9a-f]{40}', args.release or ''), 'A full release SHA is required')
    require(re.fullmatch(r'score_it_(?:container_ci|check_[a-z0-9_]+)', args.database or ''),
            'Only a test database name is accepted')
    ids = docker('ps', '--all', '--quiet', '--filter', 'label=com.docker.compose.project=' + args.project).split()
    require(ids, 'Staging project was not found')
    containers = json.loads(docker('inspect', *ids))
    selected = {}
    for service in ('web', 'db'):
        matches = [c for c in containers if c['Config'].get('Labels', {}).get('com.docker.compose.service') == service]
        require(len(matches) == 1, 'Expected one staging ' + service)
        item = matches[0]
        require(item['Config']['Labels'].get('com.docker.compose.project') == args.project, 'Project label differs')
        require(item['State'].get('Health', {}).get('Status') == 'healthy', 'Staging ' + service + ' is not healthy')
        expected = ('score-it:' if service == 'web' else 'score-it-postgres:17-') + args.release
        require(item['Config']['Image'] == expected, 'Staging image differs for ' + service)
        require(not any((item['HostConfig'].get('PortBindings') or {}).values()), 'App/DB published ports are forbidden')
        networks = item['NetworkSettings']['Networks']
        require(len(networks) == 1, 'Expected one private staging network')
        network = json.loads(docker('network', 'inspect', next(iter(networks))))[0]
        require(network['Internal'] and network.get('Labels', {}).get('com.docker.compose.project') == args.project,
                'A non-private or foreign network is attached')
        selected[service] = item
    require(set(selected['web']['NetworkSettings']['Networks']) == set(selected['db']['NetworkSettings']['Networks']),
            'App and DB do not share the same isolated network')
    database_volumes = [m for m in selected['db']['Mounts'] if m['Destination'] == '/var/lib/postgresql/data']
    require(len(database_volumes) == 1 and database_volumes[0]['Type'] == 'volume', 'Expected a test database volume')
    volume = json.loads(docker('volume', 'inspect', database_volumes[0]['Name']))[0]
    require(volume.get('Labels', {}).get('com.docker.compose.project') == args.project,
            'Database volume does not belong to this test project')
    return selected


def fixture_action(web, args, action, fixture=None, nonce=None):
    controller = ROOT / 'tests/browser/fixture.py'
    checksum = hashlib.sha256(controller.read_bytes()).hexdigest()
    program = ("import hashlib,pathlib,sys; p=pathlib.Path('/app/tests/browser/fixture.py'); "
               "assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[1], 'Fixture source differs'; "
               "exec(compile(p.read_text(),str(p),'exec'))")
    payload = json.dumps(dict(database=args.database, action=action, fixture=fixture, nonce=nonce))
    output = docker('exec', '-i', web, 'python', '-c', program, checksum, source=payload)
    return json.loads(output)


def wait_https(base):
    # The certificate is generated for this private test proxy. Issuer trust
    # is outside this browser-flow check and is not claimed by its report.
    deadline = time.monotonic() + 25
    while True:
        try:
            with urllib.request.urlopen(base + '/healthz/', context=ssl._create_unverified_context(), timeout=3) as response:
                require(response.status == 200 and response.headers.get('Referrer-Policy') == 'strict-origin',
                        'Temporary HTTPS proxy did not return the expected policy')
            return
        except Exception:
            require(time.monotonic() < deadline, 'Temporary HTTPS Caddy did not become ready')
            time.sleep(0.2)


def loopback_base(item):
    bindings = (item['NetworkSettings'].get('Ports') or {}).get('443/tcp')
    require(isinstance(bindings, list) and len(bindings) == 1
            and bindings[0].get('HostIp') == '127.0.0.1',
            'Proxy HTTPS port was not published only to loopback')
    port = bindings[0].get('HostPort', '')
    require(port.isdigit() and 0 < int(port) < 65536, 'Proxy HTTPS port is invalid')
    return 'https://127.0.0.1:' + port


def redact(message, fixture):
    message = str(message)
    if fixture:
        for user in fixture.get('users', []):
            message = message.replace(user['password'], '[redacted]')
    message = re.sub(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', '[redacted]', message, flags=re.I)
    message = re.sub(r'\b(?:set-cookie|cookie|x-csrftoken):[^\n]+', '[redacted header]', message, flags=re.I)
    return re.sub(r'\b(csrfmiddlewaretoken|csrftoken|sessionid)\s*[=:]\s*[\"\']?[^\s;\"\']+',
                  r'\1=[redacted]', message, flags=re.I)


def run(args):
    report = dict(release=args.release, started_utc=datetime.now(timezone.utc).isoformat(),
                  scope='Isolated Chromium -> HTTPS Caddy -> production Django image -> PostgreSQL',
                  tls_scope='Ephemeral self-signed test certificate; public certificate trust is outside this check',
                  production_used=False, ok=False)
    fixture = None
    proxy = None
    frontend = None
    selected = None
    errors = []
    try:
        version = subprocess.run(['node', '-p', "require('@playwright/test/package.json').version"],
                                 capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        required = json.loads((ROOT / 'tests/browser/package.json').read_text())['devDependencies']['@playwright/test']
        require(version == required, 'Installed Playwright version differs from the locked version')
        selected = preflight(args)
        web = selected['web']['Name'].lstrip('/')
        network = next(iter(selected['web']['NetworkSettings']['Networks']))
        caddy_image = 'score-it-caddy:' + args.release
        caddy_info = json.loads(docker('image', 'inspect', caddy_image))[0]
        report['images'] = {name: dict(reference=item['Config']['Image'], runtime_image_id=item['Image'])
                            for name, item in selected.items()}
        report['images']['caddy'] = dict(reference=caddy_image, runtime_image_id=caddy_info['Id'])
        nonce = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='score-it-browser-') as directory:
            directory = Path(directory)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-keyout', str(directory / 'key.pem'), '-out', str(directory / 'cert.pem'),
                            '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
                           capture_output=True, text=True, check=True, timeout=20)
            template = (ROOT / 'deploy/Caddyfile.score-it.example').read_text()
            require(template.count('score-it.example.com {') == 1 and template.count('score-it-web:8000') == 1,
                    'Proxy template changed; review the test adaptation')
            template = template.replace('{\n', '{\n    admin off\n', 1)
            template = template.replace('score-it.example.com {',
                                        'https://127.0.0.1 {\n    tls /certs/cert.pem /certs/key.pem')
            template = template.replace('score-it-web:8000', web + ':8000')
            config = directory / 'Caddyfile'
            config.write_text(template)
            # Docker does not publish ports for an internal-only endpoint.
            # Only the ephemeral proxy joins this second bridge; web and DB
            # remain on their original private network without host ports.
            frontend = docker('network', 'create', '--driver', 'bridge',
                              '--label', 'score-it.browser-test=' + nonce,
                              'score-it-browser-' + nonce).strip()
            proxy = docker('create', '--pull=never', '--name', 'score-it-browser-' + nonce,
                           '--network', frontend, '--publish', '127.0.0.1::443/tcp', '--read-only',
                           '--tmpfs', '/data:rw,mode=1777', '--tmpfs', '/config:rw,mode=1777',
                           '--tmpfs', '/tmp:rw,mode=1777', '--security-opt', 'no-new-privileges:true',
                           '--mount', 'type=bind,source=' + str(config) + ',target=/etc/caddy/Caddyfile,readonly',
                           '--mount', 'type=bind,source=' + str(directory) + ',target=/certs,readonly',
                           '--entrypoint', 'caddy', caddy_image, 'run', '--config', '/etc/caddy/Caddyfile',
                           '--adapter', 'caddyfile').strip()
            docker('network', 'connect', network, proxy)
            docker('start', proxy)
            item = json.loads(docker('inspect', proxy))[0]
            base = loopback_base(item)
            report['proxy_bound_only_to_loopback'] = True
            wait_https(base)
            fixture = fixture_action(web, args, 'seed', nonce=nonce)
            result = subprocess.run(['node', str(ROOT / 'tests/browser/check.cjs')],
                                    input=json.dumps(dict(base_url=base, fixture=fixture)),
                                    capture_output=True, text=True, timeout=300)
            try:
                browser = json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError('Browser process returned no result: ' + redact(result.stderr[-3000:], fixture))
            report['browser_result'] = browser
            require(result.returncode == 0 and browser.get('ok'),
                    'Browser check failed: ' + str(browser.get('failed_check')) + ': ' + str(browser.get('error')))
            report['database_result'] = fixture_action(web, args, 'verify', fixture=fixture)
            report['ok'] = True
    except Exception as error:
        errors.append(redact(error, fixture))
    finally:
        if fixture is not None and selected is not None:
            try:
                report['cleanup'] = fixture_action(selected['web']['Name'].lstrip('/'), args, 'cleanup', fixture=fixture)
            except Exception as error:
                errors.append('Fixture cleanup failed: ' + redact(error, fixture))
        if proxy is not None:
            try:
                docker('stop', '--time', '5', proxy)
                docker('rm', proxy)
                report['proxy_removed'] = True
            except Exception as error:
                errors.append('Proxy cleanup failed: ' + redact(error, fixture))
        if frontend is not None:
            try:
                docker('network', 'rm', frontend)
                report['proxy_network_removed'] = True
            except Exception as error:
                errors.append('Proxy network cleanup failed: ' + redact(error, fixture))
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['errors'] = errors
        if errors:
            report['ok'] = False
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if not report['ok']:
        raise SystemExit('BROWSER_SECURITY_FAILED: ' + '; '.join(errors))
    print('BROWSER_ACCESS_BOUNDARIES_OK')
    print('BROWSER_VOTING_FLOW_OK')
    print('BROWSER_SECURITY_OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args())
