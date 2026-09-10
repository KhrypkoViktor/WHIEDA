FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e ".[dev]"

COPY app /app/app
COPY scripts/partner_library_s3.py /app/scripts/partner_library_s3.py
COPY scripts/configure_telegram_menu.py /app/scripts/configure_telegram_menu.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
