import os
from celery import Celery

# Use DJANGO_SETTINGS_MODULE from environment if set, else default to 'enterprise.settings'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'enterprise.settings'))

app = Celery('enterprise')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Example: schedule admin invite reminders every hour
from celery.schedules import crontab
app.conf.beat_schedule = {
    'test-task-every-minute': {
        'task': 'enterprise.tasks.test_periodic_task_one_minute',
        'schedule': crontab(),  # every minute
        'args': [],
    },
    'send-admin-invite-reminders-every-minute': {
        'task': 'enterprise.tasks.send_enterprise_admin_invite_reminders',
        'schedule': crontab(),  # every minute
        'args': [],
    },
}
