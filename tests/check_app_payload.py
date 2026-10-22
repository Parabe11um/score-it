"""Allow only runtime application files in every saved production app layer."""

from pathlib import PurePosixPath
import tarfile

from check_caddy_runtime import Archive, layer_names, require


REQUIRED = {
    'app/manage.py',
    'app/config/settings.py',
    'app/config/database.py',
    'app/config/wsgi.py',
    'app/poker/models.py',
    'app/poker/import_process.py',
    'app/poker/import_worker.py',
    'app/poker/migrations/__init__.py',
    'app/poker/management/commands/check_database_security.py',
    'app/poker/management/commands/transfer_data.py',
    'app/templates/base.html',
    'app/static/css/app.css',
    'app/staticfiles/css/app.css',
    'app/deploy/configure_db_roles.py',
    'app/deploy/gunicorn.conf.py',
    'app/deploy/gunicorn_logger.py',
}
ENTRY_POINTS = {
    'app/manage.py',
    'app/deploy/configure_db_roles.py',
    'app/deploy/gunicorn.conf.py',
    'app/deploy/gunicorn_logger.py',
}


def allowed_path(name, directory):
    parts = PurePosixPath(name).parts
    if not parts or parts[0] != 'app':
        return True
    if directory and name in ('app', 'app/deploy', 'app/data'):
        return True
    if name in ENTRY_POINTS:
        return not directory
    if len(parts) >= 2 and parts[1] in ('config', 'poker'):
        return not any(p.startswith('.') or p in ('tests', '__pycache__') for p in parts[2:]) and (
            directory or name.endswith('.py'))
    if len(parts) >= 2 and parts[1] in ('static', 'templates', 'staticfiles'):
        return True
    return False


def check_app_archive(path, image, platform='linux/amd64'):
    found = set()
    with Archive(path) as source:
        _, layers = layer_names(source, image, platform)
        for layer_name in layers:
            member = source.members[str(PurePosixPath(layer_name))]
            require(member.isfile(), 'Missing application layer')
            with source.tar.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r|*') as layer:
                for item in layer:
                    name = str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                            'Unsafe application archive path')
                    if PurePosixPath(name).parts[:1] != ('app',):
                        continue
                    require(item.isfile() or item.isdir(), 'Application payload contains a link: ' + name)
                    require(allowed_path(name, item.isdir()), 'Non-runtime application file retained: ' + name)
                    if item.isfile():
                        found.add(name)
    require(REQUIRED <= found, 'Required application files missing: ' + ', '.join(sorted(REQUIRED - found)))
