FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# dev (par défaut) ou prod
ARG REQUIREMENTS=dev
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/${REQUIREMENTS}.txt

COPY . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
