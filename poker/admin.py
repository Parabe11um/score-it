from django.contrib import admin
from django.utils.html import format_html

from .models import (
    OrganizerInvitation,
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


@admin.register(OrganizerInvitation)
class OrganizerInvitationAdmin(admin.ModelAdmin):
    list_display = (
        "recipient_label_display",
        "status_badge",
        "created_at",
        "expires_at",
        "used_by",
        "copy_link",
    )
    search_fields = ("recipient_label", "used_by__username")
    date_hierarchy = "created_at"
    readonly_fields = (
        "copy_link",
        "status_badge",
        "token",
        "created_by",
        "created_at",
        "expires_at",
        "used_at",
        "used_by",
    )

    class Media:
        css = {"all": ("admin/css/organizer_invitations.css",)}
        js = ("admin/js/organizer_invitations.js",)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("recipient_label",)
        return (
            "recipient_label",
            "status_badge",
            "copy_link",
            "expires_at",
            "used_by",
            "used_at",
            "created_by",
            "created_at",
            "token",
        )

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Для кого", ordering="recipient_label")
    def recipient_label_display(self, obj):
        return obj.recipient_label or "Без пометки"

    @admin.display(description="Статус")
    def status_badge(self, obj):
        labels = {
            "active": ("Действует", "active"),
            "used": ("Использовано", "used"),
            "expired": ("Истекло", "expired"),
        }
        label, css_class = labels[obj.status]
        return format_html(
            '<span class="organizer-invitation-status organizer-invitation-status--{}">{}</span>',
            css_class,
            label,
        )

    @admin.display(description="Ссылка приглашения")
    def copy_link(self, obj):
        if not obj or not obj.pk:
            return "Ссылка появится после сохранения приглашения."
        if not obj.is_available:
            return "Ссылка больше недоступна."
        path = obj.get_absolute_url()
        return format_html(
            '<div class="organizer-invitation-copy" data-invitation-path="{}">'
            '<input class="vTextField" type="text" value="{}" readonly '
            'aria-label="Ссылка приглашения">'
            '<button class="button" type="button" data-copy-invitation>'
            "Копировать"
            "</button>"
            '<span data-copy-status aria-live="polite"></span>'
            "</div>",
            path,
            path,
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
        "competency",
        "project",
        "status",
        "estimate_display",
        "completed_at",
    )
    list_filter = ("competency", "status", "project", "completed_at")
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
        "analysis_capacity",
        "development_capacity",
        "testing_capacity",
        "archived_at",
    )
    list_filter = ("status", "archived_at")
    inlines = (SprintTaskInline,)
