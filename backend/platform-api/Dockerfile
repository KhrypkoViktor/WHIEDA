FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e ".[dev]"

COPY app /app/app
COPY scripts/partner_library_s3.py /app/scripts/partner_library_s3.py
COPY scripts/configure_telegram_menu.py /app/scripts/configure_telegram_menu.py
COPY scripts/check_telegram_webhook_health.py /app/scripts/check_telegram_webhook_health.py
COPY scripts/send_telegram_command_canary.py /app/scripts/send_telegram_command_canary.py
COPY scripts/send_due_partner_reminders.py /app/scripts/send_due_partner_reminders.py
COPY scripts/send_partner_payment_reminder.py /app/scripts/send_partner_payment_reminder.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
