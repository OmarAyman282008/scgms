# Deployment guide (for IT)

SCGMS ships as a Docker Compose stack:

| Service | Image | Role |
|---|---|---|
| `nginx` | nginx:1.27-alpine | Public entry point on port 80 (or 443 once TLS is added) |
| `web` | built from `Dockerfile` | Django + gunicorn (3 workers). On every start it runs migrations, and on the **first** start it loads the school data |
| `worker` | same image | Celery worker: sends e-mail notifications |
| `beat` | same image | Celery beat: daily reminders at `REMINDER_HOUR` (default 07:00 Cairo time) |
| `db` | postgres:16-alpine | Database (volume `pgdata`) |
| `redis` | redis:7-alpine | Message broker |

Uploaded documents (MOMs, evidence, templates) are kept in the volume `media`. They are **never served directly** by nginx: every download goes through the application, which checks the user's permission first.

## 1. Server requirements

* Linux (Ubuntu 22.04/24.04 LTS or similar). The minimum is 2 vCPU, 4 GB RAM and 20 GB disk; storage grows with the uploaded documents.
* Docker Engine 24+ with the Compose plugin (`docker compose version`).
* A DNS name, for example `committees.<university-domain>`.
* Outbound SMTP access, if e-mail notifications are wanted.

## 2. Install

```bash
sudo mkdir -p /opt/scgms && sudo chown $USER /opt/scgms
cd /opt/scgms && unzip ~/scgms.zip && cd scgms
cp .env.example .env
```

Edit `.env`:

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | Random 50+ characters: `python3 -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `POSTGRES_PASSWORD` | A strong database password |
| `DJANGO_ALLOWED_HOSTS` | The DNS name(s), comma-separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://<dns-name>` (must include the scheme) |
| `SITE_URL` | `https://<dns-name>`, used for links in e-mails |
| `LOAD_DEMO_DATA` | `1` to load sample data on the first start (removable later), otherwise `0` |

Start the stack:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f web          # wait for "bootstrap done" and gunicorn "Listening"
```

Then open `http://<dns-name>/` and log in as `admin` / `ChangeMe@2026`. Change that password at once.

## 3. HTTPS

The recommended setup terminates TLS on the university's existing reverse proxy or load balancer, forwarding to this server's port 80 with the header `X-Forwarded-Proto: https`. Then set `DJANGO_SECURE_COOKIES=1` in `.env`. This setting enables:

* secure cookies;
* HSTS;
* trust of the forwarded protocol header.

Run `docker compose up -d` to apply it.

To terminate TLS in this stack instead:

1. Put the certificate and key in `docker/certs/` (`fullchain.pem`, `privkey.pem`).
2. Add the following server block to `docker/nginx.conf`, and change the port-80 block to `return 301 https://$host$request_uri;`:

   ```nginx
   server {
       listen 443 ssl;
       http2 on;
       server_name committees.example.edu;
       ssl_certificate     /etc/nginx/certs/fullchain.pem;
       ssl_certificate_key /etc/nginx/certs/privkey.pem;
       client_max_body_size 30m;
       location / {
           proxy_pass http://scgms_web;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
   }
   ```

3. In `docker-compose.yml`, under `nginx`, add the port `"443:443"` and the volume `./docker/certs:/etc/nginx/certs:ro`.
4. Set `DJANGO_SECURE_COOKIES=1`, then run `docker compose up -d`.

## 4. E-mail notifications (optional)

In-app notifications always work. To also send e-mails:

1. Fill `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` and `DEFAULT_FROM_EMAIL` in `.env`.
2. Run `docker compose up -d`.
3. Log in as the Vice-Dean or the administrator, then switch on **Administration → Policy settings → Send e-mail notifications**.

Each user can choose in their profile between immediate e-mails and a single daily digest.

## 5. Reminders

The `beat` container runs the daily reminder job, which covers:

* upcoming meetings;
* MOMs due or overdue;
* tasks due soon or overdue;
* escalation to management after N overdue days;
* overdue decisions;
* the daily digest.

Reminders are de-duplicated, so a person never receives the same reminder twice in a day. To run the job by hand: `docker compose exec web python manage.py send_reminders`.

Without Docker, schedule the same command once a day (cron: `0 7 * * * cd /path/scgms && .venv/bin/python manage.py send_reminders`).

## 6. Backups

Back up **both** the database and the documents volume. Example nightly script (`/etc/cron.daily/scgms-backup`):

```bash
#!/bin/sh
set -e
cd /opt/scgms/scgms
STAMP=$(date +%F)
mkdir -p /var/backups/scgms
docker compose exec -T db pg_dump -U scgms -Fc scgms > /var/backups/scgms/db-$STAMP.dump
docker run --rm -v scgms_media:/m -v /var/backups/scgms:/b alpine tar czf /b/media-$STAMP.tgz -C /m .
find /var/backups/scgms -mtime +30 -delete
```

The volume name is `<folder>_media`; check it with `docker volume ls`. Copy the backups off the server as well.

**Restore:**

```bash
docker compose stop web worker beat
docker compose exec -T db pg_restore -U scgms -d scgms --clean --if-exists < db-YYYY-MM-DD.dump
docker run --rm -v scgms_media:/m -v /var/backups/scgms:/b alpine sh -c "cd /m && tar xzf /b/media-YYYY-MM-DD.tgz"
docker compose start web worker beat
```

## 7. Updates

```bash
cd /opt/scgms/scgms
# replace the code with the new version (keep .env), then:
docker compose up -d --build        # migrations run automatically
```

## 8. Useful commands

```bash
docker compose exec web python manage.py purge_demo          # remove DEMO records
docker compose exec web python manage.py createsuperuser     # extra administrator
docker compose exec web python manage.py changepassword admin
docker compose exec web python manage.py test apps           # run the test-suite (uses a throwaway DB)
docker compose logs --tail=200 web worker beat
```

## 9. Security checklist

- [ ] `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` are long and random. The application refuses to start with the development key.
- [ ] `DJANGO_DEBUG=0`.
- [ ] HTTPS is on and `DJANGO_SECURE_COOKIES=1`.
- [ ] The default `admin` password is changed, and each user has changed their initial password (enforced at first login).
- [ ] Ports 5432 and 6379 are not exposed. They are not published by the compose file.
- [ ] Nightly backups are configured and a restore has been tested.
- [ ] Upload limits are set: `MAX_UPLOAD_MB` (default 25) and nginx `client_max_body_size 30m`.

Allowed file types are pdf, docx, doc, xlsx, xls, pptx, jpg, png, zip, txt and csv. Each upload's content is checked against its extension.
