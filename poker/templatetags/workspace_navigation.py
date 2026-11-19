from django import template
from django.urls import reverse


register = template.Library()

SECTIONS = (
    ("user", "user_settings", "Пользователь"),
    ("projects", "dashboard", "Проекты"),
    ("voting", "voting_list", "Голосования"),
    ("sprints", "sprint_list", "Спринты"),
    ("teams", "team_list", "Команды"),
)


def _section(view):
    if view in {name for _, name, _ in SECTIONS}:
        return view
    if view in {"project_team", "project_member_edit"}:
        return "team_list"
    if view.startswith(("session_", "participant_resume_")) and view != "session_create":
        return "voting_list"
    if view.startswith("sprint_") and view != "sprint_create":
        return "sprint_list"
    return "dashboard"


@register.inclusion_tag("poker/workspace_menu.html", takes_context=True)
def workspace_menu(context):
    match = context["request"].resolver_match
    view = (match.url_name or "") if match else ""
    section = _section(view)
    items = [
        {
            "icon": icon,
            "label": label,
            "url": reverse(f"poker:{name}"),
            "current": ("page" if view == name else "location") if section == name else "",
        }
        for icon, name, label in SECTIONS
    ]
    return {"items": items, "current_label": next(item["label"] for item in items if item["current"])}
