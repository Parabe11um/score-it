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
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "updated_at")
    search_fields = ("name", "owner__username")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "project", "status", "estimate_display")
    list_filter = ("status", "project")
    search_fields = ("number", "title")


@admin.register(VotingSession)
class VotingSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "status", "current_task", "created_at")
    list_filter = ("status",)


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("name", "session", "joined_at", "last_seen_at")
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
    extra = 0


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "start_date", "end_date", "capacity")
    inlines = (SprintTaskInline,)

