from django.contrib import admin

from .models import (
    Participant,
    Project,
    Sprint,
    SprintTask,
    Task,
    Vote,
    VotingRound,
    VotingSession,
    VotingSessionTask,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "updated_at")
    search_fields = ("name", "owner__username")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "title",
        "project",
        "status",
        "estimate_display",
        "completed_at",
    )
    list_filter = ("status", "project", "completed_at")
    search_fields = ("number", "title")


class VotingSessionTaskInline(admin.TabularInline):
    model = VotingSessionTask
    extra = 0


@admin.register(VotingSession)
class VotingSessionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "project",
        "status",
        "minimum_participants",
        "current_task",
        "archived_at",
        "created_at",
    )
    list_filter = ("status", "archived_at")
    inlines = (VotingSessionTaskInline,)


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "session",
        "joined_at",
        "last_seen_at",
        "completed_at",
    )
    search_fields = ("name",)


@admin.register(VotingRound)
class VotingRoundAdmin(admin.ModelAdmin):
    list_display = ("task", "session", "number", "status", "created_at")
    list_filter = ("status",)


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("participant", "voting_round", "value", "updated_at")


class SprintTaskInline(admin.TabularInline):
    model = SprintTask
    fk_name = "sprint"
    extra = 0


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "project",
        "status",
        "start_date",
        "end_date",
        "capacity",
        "archived_at",
    )
    list_filter = ("status", "archived_at")
    inlines = (SprintTaskInline,)
