"""IP address on audit events, and audit events can't be edited (security checklist 25).

On PostgreSQL a trigger refuses any UPDATE that changes what an event says. The only changes
allowed are the ones Django and retention make: setting the actor or business to NULL when
that user or business is deleted, and clearing the IP address. Deleting old events (retention)
is still allowed. SQLite (local development only) has no trigger; the model refuses saves.
"""
from django.db import migrations, models

CREATE = """
CREATE OR REPLACE FUNCTION audit_event_append_only() RETURNS trigger AS $$
BEGIN
    IF (NEW.id, NEW.action, NEW.actor_type, NEW.resource_type, NEW.resource_id, NEW.result,
        NEW.reason, NEW.correlation_id, NEW.metadata::text, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.action, OLD.actor_type, OLD.resource_type, OLD.resource_id, OLD.result,
        OLD.reason, OLD.correlation_id, OLD.metadata::text, OLD.created_at)
       OR (NEW.actor_id IS DISTINCT FROM OLD.actor_id AND NEW.actor_id IS NOT NULL)
       OR (NEW.organization_id IS DISTINCT FROM OLD.organization_id AND NEW.organization_id IS NOT NULL)
       OR (NEW.ip_address IS DISTINCT FROM OLD.ip_address AND NEW.ip_address IS NOT NULL)
    THEN
        RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_event_append_only ON audit_auditevent;
CREATE TRIGGER audit_event_append_only BEFORE UPDATE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_event_append_only();
"""

DROP = """
DROP TRIGGER IF EXISTS audit_event_append_only ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_event_append_only();
"""


def create_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_actor_time_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]
