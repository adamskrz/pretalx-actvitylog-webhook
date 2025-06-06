import json
import re

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.forms import model_to_dict
from django.urls import reverse
from pretalx.common.models.log import ActivityLog
from pretalx.orga.signals import nav_event_settings

from .models import Webhook
from .settings import get_settings
from .tasks import fire_webhook


@receiver(nav_event_settings)
def pretalx_activitylog_webhook_settings(sender, request, **kwargs):
    if not request.user.has_perm("event.update_event", request.event):
        return []
    return [
        {
            "label": "ActvityLog Webhook plugin",
            "url": reverse(
                "plugins:pretalx_activitylog_webhook:settings",
                kwargs={"event": request.event.slug},
            ),
            "active": request.resolver_match.url_name == "plugins:pretalx_activitylog_webhook:settings",
        }
    ]


@receiver(post_save, sender=ActivityLog, weak=False, dispatch_uid="activitylog_webhook_handler")
def handle_activitylog_save(sender, instance, created=False, **kwargs):
    """
    Process ActivityLog saves and trigger webhooks for the action type.
    """
    action_type = instance.action_type
    webhook_ids = _find_webhooks(action_type)
    encoder_cls = get_settings()["PAYLOAD_ENCODER_CLASS"]

    user_str = "by "
    if instance.person:
        user_str += instance.person.get_display_name()
        user_str += " (organiser) " if instance.is_orga_action else ""

    # This is particularly janky, but display_object is a html string, so we need to parse it to get the url and text
    # content. If provided by a plugin, it, might not work.
    object_html = instance.display_object
    match = re.match(r'^(.*?)\s*<a href="([^"]+)">([^<]+)</a>$', object_html)
    url_path = ""
    link_text = ""
    text_content = ""
    url = None

    if match:
        text_content = match.group(1).strip()
        url_path = match.group(2)
        link_text = match.group(3)

    if url_path:
        url = settings.SITE_URL + url_path

    text_content += (" " + link_text) if link_text else ""

    for id, uuid in webhook_ids:
        payload_dict = dict(
            content=None,
            embeds=[
                dict(
                    title=text_content,
                    description=instance.display,  # action type
                    url=url,
                    color=3778171,
                    footer=dict(
                        text=user_str,
                    ),
                    timestamp=instance.timestamp.isoformat(),
                )
            ],
            webhook_uuid=str(uuid),
        )
        payload = json.dumps(payload_dict, cls=encoder_cls)
        fire_webhook.delay(
            id,
            payload,
            action_type=action_type,
        )


def model_dict(model):
    """
    Returns the model instance as a dict, nested values for related models.
    """
    fields = {field.name: field.value_from_object(model) for field in model._meta.fields}
    return model_to_dict(model, fields=fields)  # type: ignore


def _find_webhooks(topic: str):
    return Webhook.objects.filter(active=True, _action_types__action_type=topic).values_list("id", "uuid")
