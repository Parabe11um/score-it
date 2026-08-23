from django.db import migrations


def reset_incomplete_participants(apps, schema_editor):
    Participant = apps.get_model("poker", "Participant")
    Vote = apps.get_model("poker", "Vote")
    VotingSessionTask = apps.get_model("poker", "VotingSessionTask")

    for participant in Participant.objects.filter(
        completed_at__isnull=False
    ).iterator():
        queue_total = VotingSessionTask.objects.filter(
            session_id=participant.session_id
        ).count()
        voted_total = (
            Vote.objects.filter(
                participant_id=participant.pk,
                voting_round__queue_item__session_id=participant.session_id,
            )
            .values("voting_round__queue_item")
            .distinct()
            .count()
        )
        if queue_total == 0 or voted_total < queue_total:
            Participant.objects.filter(pk=participant.pk).update(
                completed_at=None
            )


class Migration(migrations.Migration):

    dependencies = [
        ("poker", "0007_organizerinvitation"),
    ]

    operations = [
        migrations.RunPython(
            reset_incomplete_participants,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
