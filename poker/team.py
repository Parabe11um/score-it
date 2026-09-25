"""Create independent resource snapshots for a sprint."""
from .models import SprintResource


def add_project_members(sprint, members=None):
    if members is None:
        members = sprint.project.members.filter(is_active=True)
    added = 0
    for member in members:
        resource = SprintResource(
            sprint=sprint, member=member, full_name=member.full_name,
            competency=member.competency, allocation_percent=member.allocation_percent,
            hours_per_day=member.hours_per_day,
        )
        if sprint.resources.filter(member=member).exists():
            continue
        resource.full_clean()
        resource.save()
        added += 1
    return added
