"""Private fixture controller, invoked only after the host's staging checks.

Requests and responses use stdin/stdout so generated credentials never enter
command arguments, logs or persistent reports. No HTTP test endpoint is added.
"""

import hashlib
import json
import os
import re
import secrets
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def configure(expected_database):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection
    from io import StringIO
    require(os.geteuid() == 10001, 'Probe must run as the application UID 10001')
    require(not settings.DEBUG, 'Browser staging must use DEBUG=False')
    require(connection.vendor == 'postgresql', 'An isolated PostgreSQL staging database is required')
    require(re.fullmatch(r'score_it_(?:container_ci|check_[a-z0-9_]+)', expected_database or ''),
            'Only a test database name is accepted')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        require(cursor.fetchone()[0] == expected_database, 'Unexpected staging database')
    call_command('check_database_security', stdout=StringIO())


def foreign_digest(project):
    from django.core.serializers.json import DjangoJSONEncoder
    data = {'project': list(type(project).objects.filter(pk=project.pk).values()),
            'tasks': list(project.tasks.order_by('pk').values()),
            'rooms': list(project.voting_sessions.order_by('pk').values()),
            'sprints': list(project.sprints.order_by('pk').values())}
    return hashlib.sha256(json.dumps(data, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()


def seed(nonce):
    from django.contrib.auth import get_user_model
    from poker.models import Project, Sprint, Task, VotingSession
    require(re.fullmatch(r'[0-9a-f]{32}', nonce or ''), 'Invalid staging nonce')
    names = ['browser_a_' + nonce, 'browser_b_' + nonce]
    users = get_user_model()
    require(not users.objects.filter(username__in=names).exists(), 'Probe users already exist')
    passwords = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
    owners = [users.objects.create_user(name, password=password)
              for name, password in zip(names, passwords)]
    projects = [Project.objects.create(owner=owner, name='Browser private project ' + str(index))
                for index, owner in enumerate(owners)]
    tasks = [Task.objects.create(project=project, number='BROWSER-' + str(index),
                                 title='Synthetic browser estimation ' + str(index))
             for index, project in enumerate(projects)]
    foreign_room = VotingSession.objects.create(project=projects[1], name='Private foreign room')
    foreign_sprint = Sprint.objects.create(project=projects[1], name='Private foreign sprint')
    return {'nonce': nonce, 'users': [dict(username=name, password=password)
                                     for name, password in zip(names, passwords)],
            'project': projects[0].pk, 'task': tasks[0].pk, 'task_number': tasks[0].number,
            'foreign_project': projects[1].pk, 'foreign_room': foreign_room.pk,
            'foreign_public_token': str(foreign_room.public_token), 'foreign_sprint': foreign_sprint.pk,
            'foreign_digest': foreign_digest(projects[1]),
            'room_name': 'Browser voting ' + nonce[:8]}


def verify(fixture):
    from django.contrib.auth import get_user_model
    from poker.models import Participant, Project, Task, Vote, VotingRound, VotingSession
    names = [user['username'] for user in fixture['users']]
    users = get_user_model().objects.filter(username__in=names)
    require(users.count() == 2 and not users.filter(is_staff=True).exists()
            and not users.filter(is_superuser=True).exists(), 'Probe account privileges changed')
    project = Project.objects.get(pk=fixture['project'], owner__username=names[0])
    rooms = project.voting_sessions.all()
    require(rooms.count() == 1, 'Unexpected number of probe rooms')
    room = rooms.get()
    require(room.status == VotingSession.Status.FINISHED and room.current_task_id is None,
            'Room was not finished by its owner')
    require(room.participants.count() == 2, 'Resume or late join created an unexpected participant')
    require(not room.participants.filter(completed_at__isnull=True).exists(), 'Participants did not finish')
    voting_round = room.rounds.get()
    require(voting_round.status == VotingRound.Status.CLOSED, 'Accepted round was modified')
    require(sorted(voting_round.votes.values_list('value', flat=True)) == [2, 8], 'Votes were impersonated or changed')
    task = Task.objects.get(pk=fixture['task'])
    require(task.estimate_sum == 10 and task.estimate_count == 2 and task.estimate == 4,
            'Accepted estimate or original vote totals differ')
    require(project.tasks.count() == 2, 'Rejected import created an unexpected task')
    imported = project.tasks.get(number='BROWSER-IMPORT-1')
    require(imported.title == 'Synthetic imported task' and imported.estimate == 8,
            'Native CSV import did not preserve its task and estimate')
    foreign = Project.objects.get(pk=fixture['foreign_project'], owner__username=names[1])
    require(foreign_digest(foreign) == fixture['foreign_digest'], 'Foreign business data changed')
    return {'database_end_state_verified': True, 'foreign_data_unchanged': True,
            'room_finished': True, 'participants': 2, 'votes': [2, 8], 'estimate': 4,
            'native_import_verified': True, 'invalid_uploads_created_no_tasks': True}


def cleanup(fixture):
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session
    from axes import models as axes_models
    from poker.models import VotingSession
    nonce = fixture['nonce']
    require(re.fullmatch(r'[0-9a-f]{32}', nonce or ''), 'Invalid cleanup nonce')
    names = ['browser_a_' + nonce, 'browser_b_' + nonce]
    users = get_user_model().objects.filter(username__in=names)
    ids = set(users.values_list('pk', flat=True))
    room_ids = VotingSession.objects.filter(project__owner_id__in=ids).values_list('pk', flat=True)
    participant_keys = {f'score_it_participant_{pk}' for pk in room_ids}
    for session in Session.objects.all().iterator():
        data = session.get_decoded()
        if str(data.get('_auth_user_id')) in {str(pk) for pk in ids} or participant_keys.intersection(data):
            session.delete()
    users.delete()
    for name in ('AccessAttempt', 'AccessLog', 'AccessFailureLog'):
        model = getattr(axes_models, name, None)
        if model is not None:
            model.objects.filter(username__in=names).delete()
    require(not get_user_model().objects.filter(username__in=names).exists(), 'Probe users were not removed')
    return {'probe_accounts_removed': True}


def main():
    request = json.load(sys.stdin)
    configure(request['database'])
    from django.db import transaction
    with transaction.atomic():
        if request['action'] == 'seed':
            result = seed(request['nonce'])
        elif request['action'] == 'verify':
            result = verify(request['fixture'])
        elif request['action'] == 'cleanup':
            result = cleanup(request['fixture'])
        else:
            raise RuntimeError('Unknown fixture action')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
