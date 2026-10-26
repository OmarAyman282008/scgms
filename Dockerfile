# Smart Committee Governance & Management System (SCGMS)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings

RUN apt-get update \
 && apt-get install -y --no-install-recommends gettext netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py compilemessages -l ar >/dev/null \
 && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput >/dev/null \
 && useradd --create-home --uid 1000 scgms \
 && mkdir -p /app/data/media \
 && chown -R scgms:scgms /app/data \
 && chmod +x docker/entrypoint.sh

USER scgms
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
