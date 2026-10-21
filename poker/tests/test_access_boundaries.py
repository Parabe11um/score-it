"""Exercise object ownership with valid CSRF, including nested object IDs."""

import hashlib
import json
from decimal import Decimal
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from poker.models import (
    Participant, Project, ProjectMember, Sprint, SprintResource, SprintTask,
    Task, Vote, VotingRound, VotingSession, VotingSessionTask,
)


@override_settings(DEBUG=False, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
class AccessBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("boundary-owner", password="test-owner-password")
        cls.other = get_user_model().objects.create_user("boundary-other", password="test-other-password")
        cls.owned = cls.bundle(cls.owner, "owned")
        cls.sibling = cls.bundle(cls.owner, "sibling")
        cls.foreign = cls.bundle(cls.other, "foreign")

    @staticmethod
    def bundle(owner, marker):
        project = Project.objects.create(owner=owner, name=f"Private {marker} project")
        task = Task.objects.create(project=project, number=marker + "-1", title="Private " + marker)
        estimated = Task.objects.create(project=project, number=marker + "-2", title="Estimated " + marker,
                                        status=Task.Status.ESTIMATED, imported_estimate=Decimal(8))
        room = VotingSession.objects.create(project=project, name="Private " + marker,
                                            status=VotingSession.Status.ACTIVE, current_task=task)
        voting_round = VotingRound.objects.create(session=room, task=task, number=1)
        VotingSessionTask.objects.create(session=room, task=task, current_round=voting_round,
                                         position=1, status=VotingSessionTask.Status.ACTIVE)
        first = Participant.objects.create(session=room, name=marker + " participant one", current_task=task)
        second = Participant.objects.create(session=room, name=marker + " participant two", current_task=task)
        Vote.objects.create(voting_round=voting_round, participant=first, value=2)
        Vote.objects.create(voting_round=voting_round, participant=second, value=8)
        sprint = Sprint.objects.create(project=project, name="Private " + marker + " sprint")
        SprintTask.objects.create(sprint=sprint, task=estimated, position=1)
        member = ProjectMember.objects.create(project=project, full_name="Private " + marker + " employee",
                                              competency=Task.Competency.ANALYSIS)
        resource = SprintResource.objects.create(sprint=sprint, member=member, full_name=member.full_name,
                                                competency=member.competency)
        return dict(project=project, task=task, room=room, round=voting_round, first=first,
                    second=second, sprint=sprint, member=member, resource=resource)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.owner)
        self.client.get(self.owned['room'].get_absolute_url(), secure=True)

    @staticmethod
    def fingerprint():
        data = {model._meta.label: list(model.objects.order_by('pk').values())
                for model in apps.get_app_config('poker').get_models()}
        return hashlib.sha256(json.dumps(data, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()

    def post(self, client, name, args, data=None):
        return client.post(reverse('poker:' + name, args=args),
                           {**(data or {}), 'csrfmiddlewaretoken': client.cookies['csrftoken'].value},
                           secure=True, HTTP_ORIGIN='https://testserver')

    def private_routes(self, item):
        p, t, r, s = (item[key].pk for key in ('project', 'task', 'room', 'sprint'))
        reads = [
            ('project_detail', [p]), ('project_team', [p]),
            ('project_member_edit', [p, item['member'].pk]), ('session_manage', [r]),
            ('session_export_csv', [r]), ('session_export_xlsx', [r]), ('session_state', [r]),
            ('sprint_detail', [s]), ('sprint_team', [s]), ('sprint_export', [s]),
            ('sprint_export_eva', [s]),
        ]
        writes = [(name, [p]) for name in ('project_team', 'task_import', 'task_import_file',
                                          'session_create', 'sprint_create')]
        writes += [('project_member_edit', [p, item['member'].pk])]
        writes += [(name, [p, t]) for name in ('task_complete', 'task_reopen', 'task_competency_update', 'task_delete')]
        writes += [(name, [r]) for name in ('session_import_file', 'session_start', 'session_queue_add',
                                            'session_reveal', 'session_revote', 'session_accept', 'session_finish',
                                            'session_archive', 'session_restore', 'session_delete', 'session_copy')]
        writes += [('session_navigate', [r, 'next']), ('session_start_task', [r, t]),
                   ('participant_resume_rotate', [r, item['first'].pk])]
        writes += [(name, [s]) for name in ('sprint_team', 'sprint_members_add', 'sprint_resource_create',
                                            'sprint_import', 'sprint_set_status', 'sprint_capacity_update',
                                            'sprint_archive', 'sprint_restore', 'sprint_delete', 'sprint_copy',
                                            'sprint_transfer_tasks', 'sprint_add_tasks')]
        writes += [(name, [s, item['resource'].pk]) for name in ('sprint_resource_update', 'sprint_resource_remove')]
        writes += [('sprint_remove_task', [s, item['task'].pk])]
        return reads, writes

    def test_other_owner_cannot_read_or_mutate_any_management_route(self):
        initial = self.fingerprint()
        reads, writes = self.private_routes(self.foreign)
        for name, args in reads:
            with self.subTest(method='GET', route=name):
                response = self.client.get(reverse('poker:' + name, args=args), secure=True)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(self.foreign['project'].name.encode(), response.content)
        for name, args in writes:
            with self.subTest(method='POST', route=name):
                response = self.post(self.client, name, args, {'name': 'tampered', 'status': 'completed',
                                     'value': 52, 'task_ids': [self.foreign['task'].pk]})
                self.assertEqual(response.status_code, 404, 'Valid CSRF must reach the ownership check')
                self.assertEqual(self.fingerprint(), initial, 'Denied request changed business data')

    def test_route_matrix_covers_all_object_management_names(self):
        from poker.urls import urlpatterns
        reads, writes = self.private_routes(self.foreign)
        public = {'organizer_invitation_accept', 'dashboard', 'project_create', 'room', 'room_join',
                  'room_resume', 'room_state', 'room_vote', 'room_navigate', 'room_complete'}
        self.assertEqual({p.name for p in urlpatterns} - public, {name for name, _ in reads + writes})

    def test_anonymous_and_participant_sessions_cannot_manage_objects(self):
        anonymous = Client(enforce_csrf_checks=True)
        anonymous.get('/login/', secure=True)
        participant = self.participant_client(self.owned['first'])
        reads, writes = self.private_routes(self.owned)
        initial = self.fingerprint()
        for client in (anonymous, participant):
            for name, args in reads:
                response = client.get(reverse('poker:' + name, args=args), secure=True)
                self.assertEqual(response.status_code, 302, name)
                self.assertTrue(response.url.startswith('/login/?next='), name)
            for name, args in writes:
                response = self.post(client, name, args)
                self.assertEqual(response.status_code, 302, name)
                self.assertTrue(response.url.startswith('/login/?next='), name)
        self.assertEqual(self.fingerprint(), initial)

    def test_nested_objects_must_belong_to_the_supplied_parent_even_for_same_owner(self):
        p, r, s = (self.owned[key].pk for key in ('project', 'room', 'sprint'))
        initial = self.fingerprint()
        for source in (self.sibling, self.foreign):
            cases = [(name, [p, source['task'].pk]) for name in
                     ('task_complete', 'task_reopen', 'task_competency_update', 'task_delete')]
            cases += [('project_member_edit', [p, source['member'].pk]),
                      ('session_start_task', [r, source['task'].pk]),
                      ('participant_resume_rotate', [r, source['first'].pk]),
                      ('sprint_resource_update', [s, source['resource'].pk]),
                      ('sprint_resource_remove', [s, source['resource'].pk]),
                      ('sprint_remove_task', [s, source['task'].pk])]
            for name, args in cases:
                with self.subTest(route=name, owner=source['project'].owner_id):
                    self.assertEqual(self.post(self.client, name, args).status_code, 404)
                    self.assertEqual(self.fingerprint(), initial)
            for name, args, data in (
                ('session_queue_add', [r], {'task_ids': [source['task'].pk]}),
                ('sprint_add_tasks', [s], {'task_ids': [source['task'].pk]}),
                ('sprint_members_add', [s], {'member_id': source['member'].pk}),
                ('sprint_transfer_tasks', [s], {'target_sprint': source['sprint'].pk,
                                              'task_ids': [self.owned['task'].pk]}),
            ):
                with self.subTest(route=name, owner=source['project'].owner_id):
                    response = self.post(self.client, name, args, data)
                    self.assertIn(response.status_code, (302, 404))
                    self.assertEqual(self.fingerprint(), initial)

    def participant_client(self, participant):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[f'score_it_participant_{participant.session_id}'] = str(participant.client_token)
        session.save()
        client.get(participant.session.get_public_url(), secure=True)
        return client

    def test_participant_ids_and_tokens_in_vote_body_do_not_impersonate_another_voter(self):
        first, second, room = (self.owned[key] for key in ('first', 'second', 'room'))
        client = self.participant_client(first)
        response = self.post(client, 'room_vote', [room.public_token], {
            'value': 12, 'participant': second.pk, 'participant_id': second.pk,
            'client_token': str(second.client_token), 'task_id': self.foreign['task'].pk,
            'round_id': self.foreign['round'].pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Vote.objects.get(voting_round=self.owned['round'], participant=first).value, 12)
        self.assertEqual(Vote.objects.get(voting_round=self.owned['round'], participant=second).value, 8)
        self.assertEqual(Vote.objects.filter(voting_round=self.foreign['round']).count(), 2)

    def test_joined_participant_cannot_read_or_vote_in_another_room(self):
        client = self.participant_client(self.owned['first'])
        initial = self.fingerprint()
        token = self.foreign['room'].public_token
        self.assertEqual(client.get(reverse('poker:room_state', args=[token]), secure=True).status_code, 403)
        for name, data in (('room_vote', {'value': 52}), ('room_navigate', {'direction': 'next'}),
                           ('room_complete', {})):
            self.assertEqual(self.post(client, name, [token], data).status_code, 403)
        self.assertEqual(self.fingerprint(), initial)

    def test_votes_and_other_personal_tokens_are_hidden_before_reveal(self):
        client = self.participant_client(self.owned['first'])
        room = self.owned['room']
        response = client.get(reverse('poker:room_state', args=[room.public_token]), secure=True)
        data = response.json()
        self.assertEqual(data['round']['my_vote'], 2)
        self.assertEqual(data['round']['votes'], [])
        self.assertIsNone(data['round']['average'])
        self.assertIsNone(data['round']['final_estimate'])
        self.assertNotIn(str(self.owned['second'].client_token), response.content.decode())
        self.assertNotIn(str(self.foreign['first'].client_token), response.content.decode())

    def test_rotation_revokes_both_old_resume_url_and_existing_browser_binding(self):
        participant, room = self.owned['first'], self.owned['room']
        client = self.participant_client(participant)
        old = participant.client_token
        response = self.post(self.client, 'participant_resume_rotate', [room.pk, participant.pk])
        self.assertEqual(response.status_code, 200)
        participant.refresh_from_db()
        self.assertNotEqual(participant.client_token, old)
        self.assertEqual(self.post(client, 'room_vote', [room.public_token], {'value': 52}).status_code, 403)
        fresh = Client(enforce_csrf_checks=True)
        fresh.get(reverse('poker:room_resume', args=[room.public_token, old]), secure=True)
        self.assertEqual(fresh.get(reverse('poker:room_state', args=[room.public_token]), secure=True).status_code, 403)
        fresh.get(reverse('poker:room_resume', args=[room.public_token, participant.client_token]), secure=True)
        self.assertEqual(fresh.get(reverse('poker:room_state', args=[room.public_token]), secure=True).status_code, 200)
        self.assertEqual(Vote.objects.get(voting_round=self.owned['round'], participant=participant).value, 2)

    def test_resume_token_from_other_room_does_not_bind_identity(self):
        client = Client(enforce_csrf_checks=True)
        token = self.owned['room'].public_token
        response = client.get(reverse('poker:room_resume', args=[token, self.foreign['first'].client_token]), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(reverse('poker:room_state', args=[token]), secure=True).status_code, 403)

    def test_finished_room_rejects_votes_and_new_participants(self):
        participant = self.participant_client(self.owned['first'])
        room = self.owned['room']
        self.assertEqual(self.post(self.client, 'session_finish', [room.pk]).status_code, 302)
        initial = self.fingerprint()
        self.assertEqual(self.post(participant, 'room_vote', [room.public_token], {'value': 52}).status_code, 409)
        self.assertEqual(self.post(participant, 'room_join', [room.public_token], {'name': 'late voter'}).status_code, 302)
        self.assertEqual(self.fingerprint(), initial)

    def test_non_staff_organizer_cannot_access_invitation_admin(self):
        response = self.client.get('/admin/poker/organizerinvitation/', secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin/login/'))

    def test_expired_server_session_cannot_finish_room(self):
        from django.contrib.sessions.models import Session
        Session.objects.filter(session_key=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1))
        initial = self.fingerprint()
        response = self.post(self.client, 'session_finish', [self.owned['room'].pk])
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/?next='))
        self.assertEqual(self.fingerprint(), initial)
