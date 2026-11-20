from django import template

from poker.models import Project


register = template.Library()

@register.inclusion_tag("poker/workspace_menu.html", takes_context=True)
def workspace_menu(context):
    request = context["request"]
    view = request.resolver_match.url_name if request.resolver_match else None
    project = context.get("project")
    if project is None and (work_item := context.get("voting_session") or context.get("sprint")):
        project = work_item.project
    in_account = view == "user_settings"
    return {
        "projects": Project.objects.filter(owner=request.user)
        .order_by("name", "pk").values("pk", "name"),
        "selected_project_id": project.pk if project else None,
        "project_current": "page" if view == "project_overview" else "location",
        "projects_current": (
            ("page" if view == "dashboard" else "location")
            if not project and not in_account else ""
        ),
        "in_account": in_account,
        "current_label": project.name if project else ("Пользователь" if in_account else "Проекты"),
    }
